#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FunctionGraph 代码自包含部署脚本（供 terraform null_resource 调用）。

职责：
  1) 若 zip 不存在则自动用 pip install --target 构建（含全部传递依赖）；
  2) 通过 FunctionGraph SDK update_function_code 把 zip 上传到线上函数。

凭据：从环境变量 HW_ACCESS_KEY / HW_SECRET_KEY 读取（由 terraform 的
      local-exec environment 注入，绝不落盘）。

project_id 直接从函数 URN 解析（urn:fss:<region>:<project_id>:function:...），
无需额外 IAM 调用。
"""

import os
import sys
import shutil
import argparse
import tempfile
import zipfile
import subprocess
import base64


def _pip_install(target, *pkgs):
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--target", target, *pkgs],
        check=True,
    )


def build_zip(handler_path, out_path):
    build_dir = tempfile.mkdtemp(prefix="fgsbuild-")
    _pip_install(
        build_dir,
        "huaweicloudsdkevs", "huaweicloudsdkorganizations",
        "huaweicloudsdkiam", "huaweicloudsdktms",
    )
    shutil.copy(handler_path, os.path.join(build_dir, "index.py"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if os.path.exists(out_path):
        os.remove(out_path)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(build_dir):
            dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
            for fn in files:
                if fn.endswith(".pyc"):
                    continue
                full = os.path.join(root, fn)
                zf.write(full, os.path.relpath(full, build_dir))
    print("BUILD zip -> %s (%.2f MB)" % (out_path, os.path.getsize(out_path) / 1024 / 1024))


def project_id_from_urn(urn):
    # urn:fss:cn-north-4:<project_id>:function:default:<name>:latest
    return urn.split(":")[3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--urn", required=True)
    ap.add_argument("--zip", required=False, default=None,
                    help="zip 路径（默认根据脚本位置推导）")
    ap.add_argument("--region", required=True)
    ap.add_argument("--handler", required=False, default=None,
                    help="handler 路径（默认根据脚本位置推导）")
    ap.add_argument("--build", action="store_true",
                    help="zip 不存在时自动构建")
    a = ap.parse_args()

    # 根据脚本自身位置推导 handler / zip，避免命令行传路径的引号/相对路径问题
    here = os.path.dirname(os.path.abspath(__file__))
    handler = a.handler or os.path.join(here, "..", "adapter", "snapshot_report.py")
    zipp = a.zip or os.path.join(here, "dist", "snapshot_report.zip")

    if a.build or not os.path.exists(zipp):
        build_zip(handler, zipp)

    ak = os.environ.get("HW_ACCESS_KEY")
    sk = os.environ.get("HW_SECRET_KEY")
    if not ak or not sk:
        raise SystemExit("缺少 HW_ACCESS_KEY / HW_SECRET_KEY 环境变量")

    pid = project_id_from_urn(a.urn)

    # 自包含：把 functiongraph SDK 装进临时目录并加入 sys.path
    sdk_dir = tempfile.mkdtemp(prefix="fgsdk-")
    _pip_install(sdk_dir, "huaweicloudsdkfunctiongraph")
    sys.path.insert(0, sdk_dir)

    from huaweicloudsdkcore.auth.credentials import BasicCredentials
    from huaweicloudsdkfunctiongraph.v2 import (
        FunctionGraphClient, UpdateFunctionCodeRequest,
        UpdateFunctionCodeRequestBody, FuncCode,
    )
    from huaweicloudsdkfunctiongraph.v2.region.functiongraph_region import (
        FunctionGraphRegion,
    )

    client = (
        FunctionGraphClient.new_builder()
        .with_credentials(BasicCredentials(ak, sk, pid))
        .with_region(FunctionGraphRegion.value_of(a.region))
        .build()
    )

    with open(zipp, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    req = UpdateFunctionCodeRequest(function_urn=a.urn)
    req.body = UpdateFunctionCodeRequestBody(
        code_type="zip",
        code_filename="index.zip",
        func_code=FuncCode(file=b64),
    )
    resp = client.update_function_code(req)
    print("UPLOAD ok code_size=%s code_filename=%s" % (resp.code_size, resp.code_filename))


if __name__ == "__main__":
    main()

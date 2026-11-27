#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
打包 FunctionGraph 函数 zip（含华为云 SDK 纯 Python 依赖）

解决 FunctionGraph 运行期不含 huaweicloudsdk* 的坑：必须自带 SDK。
两道护栏：
  - 缺 huaweicloudsdkcore 直接报错退出（不产空包）
  - 产物 < 200KB 报错（仅 handler 约 3KB，带 SDK 应 > 2MB）

用法：
  python build_function_package.py \
      --handler ../adapter/snapshot_report.py \
      --out dist/snapshot_report.zip \
      [--python <venv 或系统 python>]
"""

import os
import sys
import shutil
import subprocess
import zipfile
import argparse
import tempfile

# 需要打包进 zip 的 SDK（用 pip install --target 拉全传递依赖，避免漏包）
# 注：OBS 上传为可选且默认关闭，故不进默认包；启用时需在 requirements 安装 huaweicloudsdkobs
PIP_PKGS = [
    "huaweicloudsdkevs", "huaweicloudsdkorganizations",
    "huaweicloudsdkiam", "huaweicloudsdktms",
]

# 需要过滤的目录/扩展名
SKIP_DIRS = {"__pycache__", "tests", "test"}
SKIP_SUFFIXES = (".pyc", ".pyd", ".so")


def ensure_venv(python):
    """若给定 python 未装 pip 或 SDK，则创建 venv 并安装。返回 venv 的 python 路径。"""
    try:
        subprocess.run(
            [python, "-c", "import huaweicloudsdkcore"],
            check=True, capture_output=True,
        )
        return python
    except subprocess.CalledProcessException:
        pass
    venv_dir = tempfile.mkdtemp(prefix="hwfgs-")
    subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)
    vpy = os.path.join(venv_dir, "Scripts", "python.exe")
    if not os.path.exists(vpy):
        vpy = os.path.join(venv_dir, "bin", "python")
    subprocess.run([vpy, "-m", "pip", "install", "--quiet", *PIP_PKGS], check=True)
    return vpy


def build(handler_path, out_path, python):
    python = ensure_venv(python)

    build_dir = tempfile.mkdtemp(prefix="fgsbuild-")

    # 关键：用 pip install --target 把 SDK 及其全部传递依赖（defusedxml/requests/...）
    # 装进干净目录，保证运行期不缺第三方包。
    subprocess.run(
        [python, "-m", "pip", "install", "--quiet", "--no-cache-dir",
         "--target", build_dir, *PIP_PKGS],
        check=True,
    )

    if not os.path.isdir(os.path.join(build_dir, "huaweicloudsdkcore")):
        raise SystemExit("pip 未装入 huaweicloudsdkcore，打包中止")

    # 复制 handler（FunctionGraph 约定入口名 index.py，避免非 index 模块名解析失败）
    shutil.copy(handler_path, os.path.join(build_dir, "index.py"))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if os.path.exists(out_path):
        os.remove(out_path)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(build_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                       and not d.endswith(".dist-info")
                       and not d.endswith(".egg-info")]
            for fn in files:
                if fn.endswith(SKIP_SUFFIXES):
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, build_dir)
                zf.write(full, rel)

    size = os.path.getsize(out_path)
    if size < 200 * 1024:
        raise SystemExit("产物仅 %d 字节（<200KB），疑似未打入 SDK，中止" % size)
    print("打包完成: %s (%.2f MB)" % (out_path, size / 1024 / 1024))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--handler",
                    default=os.path.join(os.path.dirname(__file__),
                                         "..", "adapter", "snapshot_report.py"))
    ap.add_argument("--out",
                    default=os.path.join(os.path.dirname(__file__),
                                         "dist", "snapshot_report.zip"))
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args()
    build(os.path.abspath(a.handler), os.path.abspath(a.out), a.python)

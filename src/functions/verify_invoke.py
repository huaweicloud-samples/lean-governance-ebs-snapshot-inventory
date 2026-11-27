#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L3 业务验证：同步调用 FunctionGraph 函数，确认端到端跑通。

用法（凭据经环境变量，绝不落盘）：
  export HW_ACCESS_KEY="<AK>"
  export HW_SECRET_KEY="<SK>"
  export HW_REGION_NAME="cn-north-4"
  python src/functions/verify_invoke.py

输出函数返回体 + 运行日志尾部。
"""

import os
import sys

from huaweicloudsdkcore.auth.credentials import BasicCredentials, GlobalCredentials
from huaweicloudsdkiam.v3 import IamClient, KeystoneListAuthProjectsRequest
from huaweicloudsdkiam.v3.region.iam_region import IamRegion
from huaweicloudsdkfunctiongraph.v2 import FunctionGraphClient, InvokeFunctionRequest
from huaweicloudsdkfunctiongraph.v2.region.functiongraph_region import (
    FunctionGraphRegion,
)

FUNCTION_NAME = "ebs_snapshot_reporter_func"


def main():
    ak = os.environ.get("HW_ACCESS_KEY")
    sk = os.environ.get("HW_SECRET_KEY")
    region = os.environ.get("HW_REGION_NAME", "cn-north-4")
    if not ak or not sk:
        raise SystemExit("缺少 HW_ACCESS_KEY / HW_SECRET_KEY 环境变量")

    # 解析 project_id（invoke 需项目级凭据）
    iam = (
        IamClient.new_builder()
        .with_credentials(GlobalCredentials(ak, sk))
        .with_region(IamRegion.value_of(region))
        .build()
    )
    proj = iam.keystone_list_auth_projects(KeystoneListAuthProjectsRequest())
    pid = next((p.id for p in (proj.projects or []) if p.name == region), None)
    if not pid:
        raise SystemExit("无法解析 region=%s 的 project_id" % region)

    urn = "urn:fss:%s:%s:function:default:%s:latest" % (region, pid, FUNCTION_NAME)

    client = (
        FunctionGraphClient.new_builder()
        .with_credentials(BasicCredentials(ak, sk, pid))
        .with_region(FunctionGraphRegion.value_of(region))
        .build()
    )

    req = InvokeFunctionRequest()
    req.function_urn = urn
    req.body = "{}"
    req.x_cff_log_type = "tail"
    req.x_cff_request_version = "v1"
    resp = client.invoke_function(req)

    print("FUNCTION_URN=", urn)
    print("STATUS=", resp.status)
    print("RESULT_FULL=", resp.result or "")
    log = resp.log or ""
    print("LOG_TAIL=", log[-2000:])


if __name__ == "__main__":
    main()

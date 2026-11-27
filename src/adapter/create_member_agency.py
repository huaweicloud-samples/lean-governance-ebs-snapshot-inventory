#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
成员账号侧一次性建委托（跨账号盘点的前置条件）

在【每个成员账号】内，用该账号自身的 AK/SK 运行本脚本，创建一个委托，
把 EVS/TMS 只读权限委托给管理账号（管理账号据此用
CreateTemporaryAccessKeyByAgency 获取本账号临时凭证）。

前置：
  - 设置环境变量 HW_ACCESS_KEY / HW_SECRET_KEY（成员账号自身）
  - 提供管理账号的 domain id（--mgmt-domain-id，可由管理账号
    `huaweicloudsdkiam` KeystoneListAuthDomains 取得）

用法：
  python create_member_agency.py \
      --region cn-north-4 \
      --agency-name OrgSnapshotAgency \
      --mgmt-domain-id <管理账号 domain id>
"""

import os
import sys
import argparse

from huaweicloudsdkcore.auth.credentials import GlobalCredentials
from huaweicloudsdkcore.exceptions import exceptions
from huaweicloudsdkiam.v3 import (
    IamClient, CreateAgencyRequest, CreateAgencyRequestBody,
    AgencyProjectRole,
)
from huaweicloudsdkiam.v3.region.iam_region import IamRegion


def main():
    parser = argparse.ArgumentParser(description="在成员账号创建跨账号委托")
    parser.add_argument("--region", default="cn-north-4")
    parser.add_argument("--agency-name", default="OrgSnapshotAgency")
    parser.add_argument("--mgmt-domain-id", required=True,
                        help="管理账号的 domain id（被委托方）")
    args = parser.parse_args()

    ak = os.environ.get("HW_ACCESS_KEY")
    sk = os.environ.get("HW_SECRET_KEY")
    if not ak or not sk:
        raise SystemExit("请设置成员账号的 HW_ACCESS_KEY / HW_SECRET_KEY")

    cred = GlobalCredentials(ak, sk)
    client = IamClient.new_builder().with_credentials(cred).with_region(
        IamRegion.value_of(args.region)
    ).build()

    body = CreateAgencyRequestBody(
        name=args.agency_name,
        description="EVS/TMS 只读委托，供管理账号跨账号盘点快照",
        domain_id=args.mgmt_domain_id,
        duration="FOREVER",
        project_role=[
            AgencyProjectRole(
                project=args.region,
                roles=["EVS ReadOnlyAccess", "TMS ReadOnlyAccess"],
            )
        ],
    )
    req = CreateAgencyRequest()
    req.body = body
    try:
        resp = client.create_agency(req)
        agency = getattr(resp, "agency", None)
        print("委托创建成功: id=%s name=%s" % (
            getattr(agency, "id", "?"), getattr(agency, "name", args.agency_name)
        ))
    except exceptions.ClientRequestException as e:
        # 已存在则视为成功
        if "agency already exists" in str(e.error_msg).lower() or e.status_code == 409:
            print("委托已存在，跳过")
        else:
            raise


if __name__ == "__main__":
    main()

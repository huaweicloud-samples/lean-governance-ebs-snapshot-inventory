#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EBS 快照全组织盘点报告（华为云版）

聚合 Organizations 内成员账号的 EVS 快照并导出 CSV。两种运行方式：
  1) CLI：python snapshot_report.py --region cn-north-4 --file output.csv [--agency <委托名>] [--with-tags]
  2) FunctionGraph：zip 包内作为函数入口，handler = snapshot_report.handler，
     区域/委托名/落盘桶由函数环境变量（user_data）注入。

数据源：
  - Organizations ListAccounts       枚举组织内 ACTIVE 账号
  - IAM Agency CreateTemporaryAccessKeyByAgency  取得成员账号临时凭证
  - EVS ListSnapshots                收集快照明细
  - TMS ListTagsForResource          可选，补充 Owner/Team/instance_id 标签

凭据来源（不落盘）：
  - FunctionGraph 运行期：经 handler 的 context 注入的委托临时凭证。
  - CLI 运行期：环境变量 HW_ACCESS_KEY / HW_SECRET_KEY / HW_PROJECT_ID。
跨账号：通过管理账号永久密钥调用 CreateTemporaryAccessKeyByAgency 获取成员账号临时凭证。
"""

import os
import sys
import csv
import json
import logging
import argparse
from datetime import datetime, timezone

# FunctionGraph 运行时 /opt/function/ 为只读，不能写日志文件；
# 统一输出到 stdout（FunctionGraph 会捕获 stdout 到日志服务，CLI 直接可见）。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("snapshot_report")

# 报告字段
FIELD_NAMES = [
    "Account", "Snapshot_Id", "Volume_Size", "Volume_Id",
    "start_time", "Owner", "Team", "instance_id", "Description",
]

# 自定义标签键会被提取进报告
TAG_KEYS = ("Owner", "Team", "instance_id")


def _log(msg):
    logger.info(msg)


# ---------------------------------------------------------------------------
# 凭据构造
# ---------------------------------------------------------------------------

def get_mgmt_credentials(context):
    """返回 (ak, sk, token, project_id_from_env)。
    FunctionGraph 运行期从 context 取委托临时凭证；CLI 从环境变量取。
    """
    ak = sk = token = project_id = None
    if context is not None:
        try:
            ak = context.getSecurityAccessKey()
            sk = context.getSecuritySecretKey()
            token = context.getSecurityToken()
        except Exception as e:  # noqa: BLE001
            logger.warning("从 context 读取委托凭证失败: %s", e)
    if not ak or not sk:
        ak = os.environ.get("HW_ACCESS_KEY")
        sk = os.environ.get("HW_SECRET_KEY")
        token = os.environ.get("HW_SECURITY_TOKEN")
    project_id = os.environ.get("HW_PROJECT_ID")
    if not ak or not sk:
        raise RuntimeError(
            "未找到凭据：FunctionGraph 请确认函数已绑定执行委托；"
            "CLI 请设置 HW_ACCESS_KEY / HW_SECRET_KEY 环境变量。"
        )
    return ak, sk, token, project_id


def _basic(ak, sk, project_id, token=None):
    """构造带 project_id 的基础凭证（项目级服务 EVS/TMS 用）。"""
    from huaweicloudsdkcore.auth.credentials import BasicCredentials
    cred = BasicCredentials(ak, sk, project_id)
    if token:
        cred = cred.with_security_token(token)
    return cred


def _global(ak, sk, token=None):
    """构造全局凭证（IAM / Organizations 用）。
    临时委托凭证必须带 security_token，否则 IAM 报 401。
    """
    from huaweicloudsdkcore.auth.credentials import GlobalCredentials
    cred = GlobalCredentials(ak, sk)
    if token:
        cred = cred.with_security_token(token)
    return cred


def resolve_project_id_by_region(ak, sk, token, region):
    """KeystoneListAuthProjects 解析当前凭证在指定区域的 project_id。"""
    from huaweicloudsdkiam.v3 import IamClient, KeystoneListAuthProjectsRequest
    from huaweicloudsdkiam.v3.region.iam_region import IamRegion

    # keystone_list_auth_projects 是全局 IAM API，临时凭证也必须用 GlobalCredentials
    # 带 token（BasicCredentials 调全局接口会 401）。
    cred = _global(ak, sk, token)
    client = IamClient.new_builder().with_credentials(cred).with_region(
        IamRegion.value_of(region)
    ).build()
    resp = client.keystone_list_auth_projects(KeystoneListAuthProjectsRequest())
    for p in getattr(resp, "projects", []) or []:
        if getattr(p, "name", None) == region:
            return p.id
    # 退化：取第一个
    projects = getattr(resp, "projects", []) or []
    if projects:
        return projects[0].id
    raise RuntimeError("无法解析 region=%s 的 project_id" % region)


# ---------------------------------------------------------------------------
# 组织账号枚举
# ---------------------------------------------------------------------------

def list_active_accounts(ak, sk, region, token=None):
    """返回 [(account_id, account_name), ...]（仅 ACTIVE）。"""
    from huaweicloudsdkorganizations.v1 import (
        OrganizationsClient, ListAccountsRequest,
    )
    from huaweicloudsdkorganizations.v1.region.organizations_region import (
        OrganizationsRegion,
    )

    client = OrganizationsClient.new_builder().with_credentials(
        _global(ak, sk, token)
    ).with_region(OrganizationsRegion.value_of(region)).build()

    accounts = []
    marker = None
    while True:
        req = ListAccountsRequest()
        if marker:
            req.x_security_token = marker  # 翻页（若有）
        resp = client.list_accounts(req)
        for acc in getattr(resp, "accounts", []) or []:
            if str(getattr(acc, "status", "")).upper() == "ACTIVE":
                accounts.append((acc.id, getattr(acc, "name", acc.id)))
        # Organizations 翻页：若返回 truncated 则用 marker 继续（保守处理）
        if not getattr(resp, "truncated", False):
            break
        marker = getattr(resp, "marker", None)
        if not marker:
            break
    _log("组织内 ACTIVE 账号数: %d" % len(accounts))
    return accounts


def get_caller_domain_id(ak, sk, token=None):
    """KeystoneListAuthDomains 取调用方 domain id（用于识别管理账号自身）。"""
    from huaweicloudsdkiam.v3 import IamClient, KeystoneListAuthDomainsRequest
    from huaweicloudsdkiam.v3.region.iam_region import IamRegion

    client = IamClient.new_builder().with_credentials(
        _global(ak, sk, token)
    ).with_region(IamRegion.value_of("cn-north-4")).build()
    resp = client.keystone_list_auth_domains(KeystoneListAuthDomainsRequest())
    domains = getattr(resp, "domains", []) or []
    if domains:
        return domains[0].id
    return None


# ---------------------------------------------------------------------------
# 跨账号：成员账号临时密钥
# ---------------------------------------------------------------------------

def get_member_temp_credential(ak, sk, agency_name, member_domain_name, region, token=None):
    """管理账号调用 CreateTemporaryAccessKeyByAgency 获取成员账号临时凭证。
    返回 (temp_ak, temp_sk, temp_token)。
    """
    from huaweicloudsdkiam.v3 import (
        IamClient, CreateTemporaryAccessKeyByAgencyRequest,
        CreateTemporaryAccessKeyByAgencyRequestBody,
        CreateTemporaryAccessKeyByAgencySessionUser,
    )
    from huaweicloudsdkiam.v3.region.iam_region import IamRegion

    client = IamClient.new_builder().with_credentials(
        _global(ak, sk, token)
    ).with_region(IamRegion.value_of(region)).build()

    body = CreateTemporaryAccessKeyByAgencyRequestBody(
        agency_name=agency_name,
        session_user=CreateTemporaryAccessKeyByAgencySessionUser(
            domain_name=member_domain_name,
            whoami="snapshot-session",
        ),
    )
    req = CreateTemporaryAccessKeyByAgencyRequest()
    req.body = body
    resp = client.create_temporary_access_key_by_agency(req)

    # 兼容两种响应结构
    cred = getattr(resp, "credential", None)
    if cred is None:
        cred = getattr(getattr(resp, "user", None), "credential", None)
    if cred is None:
        raise RuntimeError("CreateTemporaryAccessKeyByAgency 响应缺少 credential")
    return cred.ak, cred.sk, cred.securitytoken


# ---------------------------------------------------------------------------
# EVS 快照列举
# ---------------------------------------------------------------------------

_TMS_DISABLED = False  # 一旦 TMS 调用失败，后续快照不再重试，避免海量报错


def _fetch_tags_via_tms(snap_id, region, tms_client):
    """通过 TMS 获取 EVS 快照标签（尽力而为）。"""
    global _TMS_DISABLED
    if _TMS_DISABLED or tms_client is None:
        return {}
    try:
        from huaweicloudsdktms.v1 import ListTagsForResourceRequest
        req = ListTagsForResourceRequest(resource_id=snap_id, resource_type="snapshots")
        resp = tms_client.list_tags_for_resource(req)
        tags = {}
        for t in getattr(resp, "tags", []) or []:
            k = getattr(t, "key", None)
            v = getattr(t, "value", None)
            if k is not None:
                tags[k] = v
        return tags
    except Exception as e:  # noqa: BLE001
        _TMS_DISABLED = True
        logger.warning("TMS 标签获取失败，已关闭后续 TMS 调用: %s", e)
        return {}


def list_snapshots_for_account(evs_client, tms_client, region, account_label, with_tags):
    """列举单账号（项目）下全部 EVS 快照，返回报告行列表。"""
    from huaweicloudsdkevs.v2 import ListSnapshotsRequest

    rows = []
    offset = 0
    limit = 1000
    while True:
        req = ListSnapshotsRequest(limit=limit, offset=offset)
        resp = evs_client.list_snapshots(req)
        snaps = getattr(resp, "snapshots", []) or []
        if not snaps:
            break
        for snap in snaps:
            snap_id = getattr(snap, "id", "")
            volume_size = getattr(snap, "size", "")
            volume_id = getattr(snap, "volume_id", "")
            start_time = getattr(snap, "created_at", "")
            description = getattr(snap, "description", "") or ""
            status = getattr(snap, "status", "")

            # 优先读取响应内联 tags；否则按需走 TMS
            tags = {}
            inline = getattr(snap, "tags", None)
            if inline:
                for t in inline:
                    if isinstance(t, dict):
                        tags[t.get("key")] = t.get("value")
                    else:
                        k = getattr(t, "key", None)
                        v = getattr(t, "value", None)
                        if k is not None:
                            tags[k] = v
            elif with_tags:
                tags = _fetch_tags_via_tms(snap_id, region, tms_client)

            row = {
                "Account": account_label,
                "Snapshot_Id": snap_id,
                "Volume_Size": volume_size,
                "Volume_Id": volume_id,
                "start_time": start_time,
                "Owner": tags.get("Owner", ""),
                "Team": tags.get("Team", ""),
                "instance_id": tags.get("instance_id", ""),
                "Description": description,
            }
            rows.append(row)
        if len(snaps) < limit:
            break
        offset += limit
    _log("账号 %s 快照数: %d" % (account_label, len(rows)))
    return rows


def _make_evs_client(ak, sk, project_id, token, region):
    from huaweicloudsdkevs.v2 import EvsClient
    from huaweicloudsdkevs.v2.region.evs_region import EvsRegion
    cred = _basic(ak, sk, project_id, token)
    return EvsClient.new_builder().with_credentials(cred).with_region(
        EvsRegion.value_of(region)
    ).build()


def _make_tms_client(ak, sk, project_id, token, region):
    from huaweicloudsdktms.v1 import TmsClient
    from huaweicloudsdktms.v1.region.tms_region import TmsRegion
    # TMS 是全局服务，必须用 GlobalCredentials（带 token 的临时凭证同样需要）
    cred = _global(ak, sk, token)
    return TmsClient.new_builder().with_credentials(cred).with_region(
        TmsRegion.value_of(region)
    ).build()


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def collect(region, agency_name=None, with_tags=True):
    """核心收集逻辑。返回 (rows, mgmt_account_id)。

    设计：始终先扫描「当前账号」（管理账号）；若指定 --agency 且当前账号
    具备 Organizations 权限，则跨账号枚举组织内其他成员账号。组织枚举失败
    （如当前账号非组织管理账号、未启用 Organizations）时优雅调整——仅含
    当前账号快照并明确提示，不影响函数整体可用性。
    """
    ak = os.environ.get("HW_ACCESS_KEY")
    sk = os.environ.get("HW_SECRET_KEY")
    token = os.environ.get("HW_SECURITY_TOKEN")
    env_project = os.environ.get("HW_PROJECT_ID")

    if not ak or not sk:
        raise RuntimeError("CLI 模式需设置 HW_ACCESS_KEY / HW_SECRET_KEY")

    rows = []
    try:
        mgmt_domain_id = get_caller_domain_id(ak, sk, token)
    except Exception as e:  # noqa: BLE001
        logger.warning("无法解析调用方 domain id: %s", e)
        mgmt_domain_id = None

    # 当前（管理）账号 project_id
    mgmt_pid = env_project or resolve_project_id_by_region(ak, sk, token, region)
    mgmt_evs = _make_evs_client(ak, sk, mgmt_pid, token, region)
    mgmt_tms = _make_tms_client(ak, sk, mgmt_pid, token, region) if with_tags else None

    # 始终先扫描当前账号
    label = mgmt_domain_id or "local"
    _log("扫描当前账号: %s" % label)
    rows.extend(
        list_snapshots_for_account(mgmt_evs, mgmt_tms, region, label, with_tags)
    )

    if not agency_name:
        _log("未指定 --agency，仅扫描当前账号")
        return rows, mgmt_domain_id

    # 跨账号：枚举组织内其他成员账号
    try:
        accounts = list_active_accounts(ak, sk, region, token)
    except Exception as e:  # noqa: BLE001
        logger.error("枚举组织账号失败（需当前账号为组织管理账号且具备 Organizations 权限）: %s", e)
        _log("⚠️ 组织账号枚举失败，已跳过跨账号扫描；本报告仅包含当前账号快照。"
             "如需全组织扫描，请用组织管理账号运行，或在该账号启用 Organizations。")
        return rows, mgmt_domain_id

    for acc_id, acc_name in accounts:
        if mgmt_domain_id and acc_id == mgmt_domain_id:
            continue  # 已在上面扫描
        try:
            tak, tsk, ttoken = get_member_temp_credential(
                ak, sk, agency_name, acc_name, region, token
            )
            pid = resolve_project_id_by_region(tak, tsk, ttoken, region)
            evs = _make_evs_client(tak, tsk, pid, ttoken, region)
            tms = _make_tms_client(tak, tsk, pid, ttoken, region) if with_tags else None
            rows.extend(
                list_snapshots_for_account(evs, tms, region, acc_id, with_tags)
            )
        except Exception as e:  # noqa: BLE001
            logger.error("账号 %s 获取临时凭证失败，跳过: %s", acc_id, e)
            _log("账号 %s 跳过（无法获取临时凭证）" % acc_id)
            continue
    return rows, mgmt_domain_id


def write_csv(rows, filepath):
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, FIELD_NAMES)
        writer.writeheader()
        writer.writerows(rows)
    _log("已写入 CSV: %s (%d 行)" % (filepath, len(rows)))


def upload_to_obs(filepath, bucket, region, ak, sk, token, project_id):
    """可选：将 CSV 上传到 OBS（需 obs 包，运行中按需 import）。"""
    from huaweicloudsdkobs.v1 import ObsClient
    obs = ObsClient(ak, sk, token, server="obs.%s.myhuaweicloud.com" % region)
    key = "snapshot-report/%s.csv" % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    obs.putFile(bucket, key, filepath)
    _log("已上传 OBS: obs://%s/%s" % (bucket, key))


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main_cli():
    parser = argparse.ArgumentParser(
        description="生成华为云 EVS 快照全组织盘点 CSV 报告"
    )
    parser.add_argument("--region", required=True, help="区域，如 cn-north-4")
    parser.add_argument("--file", required=True, help="输出 CSV 路径")
    parser.add_argument("--agency", required=False, default=None,
                        help="成员账号中创建的委托名（用于跨账号盘点）")
    parser.add_argument("--with-tags", action="store_true", default=True,
                        help="通过 TMS 获取快照标签（默认开启；大量快照时可能较慢）")
    parser.add_argument("--no-tags", dest="with_tags", action="store_false",
                        help="关闭 TMS 标签获取")
    args = parser.parse_args()

    if os.path.exists(args.file):
        raise SystemExit("输出文件已存在，中止以避免覆盖: %s" % args.file)

    rows, _ = collect(args.region, agency_name=args.agency, with_tags=args.with_tags)
    write_csv(rows, args.file)


# ---------------------------------------------------------------------------
# FunctionGraph 入口
# ---------------------------------------------------------------------------

def handler(event, context):
    print("=== HANDLER ENTRY ===", flush=True)
    region = os.environ.get("REGION", "cn-north-4")
    agency_name = os.environ.get("AGENCY_NAME") or None
    with_tags = str(os.environ.get("WITH_TAGS", "true")).lower() == "true"
    bucket = os.environ.get("REPORT_OBS_BUCKET") or None

    # FunctionGraph 运行期：用 context 注入的委托临时凭证作为"管理账号"凭证
    ak = sk = token = None
    if context is not None:
        try:
            ak = context.getSecurityAccessKey()
            sk = context.getSecuritySecretKey()
            token = context.getSecurityToken()
        except Exception:  # noqa: BLE001
            pass
    # 注入到环境变量，供 collect() 读取
    if ak and sk:
        os.environ["HW_ACCESS_KEY"] = ak
        os.environ["HW_SECRET_KEY"] = sk
        if token:
            os.environ["HW_SECURITY_TOKEN"] = token

    try:
        rows, _ = collect(region, agency_name=agency_name, with_tags=with_tags)
    except Exception as e:  # noqa: BLE001
        print("COLLECT_FAILED: %s" % e, flush=True)
        raise

    # 函数运行期无稳定本地磁盘语义，直接将 CSV 内容作为返回体
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(buf, FIELD_NAMES)
    writer.writeheader()
    writer.writerows(rows)
    csv_text = buf.getvalue()

    if bucket:
        try:
            upload_to_obs(csv_text, bucket, region, ak, sk, token,
                          os.environ.get("HW_PROJECT_ID"))
        except Exception as e:  # noqa: BLE001
            print("OBS_UPLOAD_FAILED: %s" % e, flush=True)

    return {
        "status": "ok",
        "region": region,
        "rows": len(rows),
        "csv": csv_text,
    }


if __name__ == "__main__":
    main_cli()

# lean-governance-ebs-snapshot-inventory · EVS 快照全组织盘点报告

![License](https://img.shields.io/badge/License-MIT--0-blue.svg)
![Huawei Cloud](https://img.shields.io/badge/Huawei%20Cloud-EVS%20%2F%20Organizations-orange.svg)

## 1. 简介 / Overview

### 简介


本实践提供一套**华为云原生**的工具，用于一键盘点**组织（Organizations）内所有账号**的 EVS（云硬盘）快照，并导出为 CSV 报告。报告字段包括：账号、快照 ID、云硬盘大小、云硬盘 ID、创建时间、Owner/Team/instance_id 标签、描述。

- 适用对象：需要统一审计、清理或合规盘点跨账号 EVS 快照的云运营 / 运维人员。
- 解决痛点：原生控制台无法跨组织账号聚合快照视图；本工具一次运行覆盖全部成员账号。

---

## 2. 目录

- [1. 简介 / Overview](#1-简介--overview)
- [2. 目录](#2-目录)
- [3. 架构图](#3-架构图)
- [4. 方案亮点](#4-方案亮点)
- [5. 涉及云服务](#5-涉及云服务)
- [6. 前置条件](#6-前置条件)
- [7. 快速开始](#7-快速开始)
- [8. 分步部署](#8-分步部署)
- [9. 使用方法 / 验证](#9-使用方法--验证)
- [10. 清理资源](#10-清理资源)
- [11. 详细说明](#11-详细说明)
- [12. 依赖](#12-依赖)
- [13. FAQ / 故障排除](#13-faq--故障排除)
- [14. 贡献指南](#14-贡献指南)
- [15. 许可证](#15-许可证)
- [16. 联系方式 / 维护者](#16-联系方式--维护者)

---

## 3. 架构图

```
管理账号（运行主体）
│
├─ Organizations ListAccounts ──► 枚举组织内 ACTIVE 账号
│
├─（成员账号）IAM Agency CreateTemporaryAccessKeyByAgency
│ └─► 取得成员账号临时 AK/SK/Token
│
├─ EVS ListSnapshots（每账号/区域）─► 收集快照明细
│
└─（可选）TMS ListTagsForResource ─► 补充 Owner/Team/instance_id 标签
│
▼
CSV 报告（CLI 本地文件 / FunctionGraph 返回值 / 可选 OBS 落盘）

调度形态（可选）：FunctionGraph + TIMER 触发器
```

---

## 4. 方案亮点

- **零公网 NAT**：FunctionGraph 走默认出口调用华为云 API，不创建 VPC / 子网 / NAT / EIP。
- **按调用执行**：FunctionGraph 按需调用运行，定时器按需触发，无需常驻资源。
- **跨账号原生**：基于华为云 Organizations + Agency 委托模型，无需在成员账号常驻资源。
- **CLI / 函数一体**：同一份 `snapshot_report.py` 既可在本地运行，也可作为 FunctionGraph 入口。

---

## 5. 涉及云服务

| 服务 | 用途 | 备注 |
|---|---|---|
| EVS | 快照查询（ListSnapshots） | 跨账号枚举成员快照 |
| Organizations | 列出组织内账号（ListAccounts） | 需管理账号运行 |
| IAM | 委托 + 临时凭证（CreateTemporaryAccessKeyByAgency） | 跨账号临时凭证获取 |
| FunctionGraph | 调度函数（snapshot_report） | 零基础设施 |
| TMS | 快照标签（ListTagsForResource） | 可选，补充 Owner/Team/instance_id |
| OBS（可选） | 报告 CSV 落盘 | 按需开启 |

> **提示**：所有服务均可通过本仓库的 Terraform 一键创建；如需清理，请参见 [第 10 节「清理资源」](#10-清理资源)。

---

## 6. 前置条件

- **组织级盘点前置（重要）**：**当前账号必须是华为云 Organizations 的「管理账号」**（或具 Organizations 只读 + IAM 相应权限的委托主体），`ListAccounts` 才能返回组织内账号。
- 若当前账号**非组织管理账号 / 未启用 Organizations**，调用 `ListAccounts` 会返回 `Organizations.1015`。本工具**已做优雅调整**：自动跳过跨账号枚举，仅盘点**当前账号**的 EVS 快照并明确提示，函数仍可正常出报告（详见第 13 节）。
- 本地已安装 **Terraform ≥ 1.5**，以及 Python ≥ 3.9（仅 CLI / 打包时需要）。
- 区域约束：脚本与函数按单一区域扫描（默认 `cn-north-4`），需与被盘点快照所在区域一致。
- **跨账号前置（一次性）**：在每个**成员账号**内运行 `src/adapter/create_member_agency.py`，创建委托 `OrgSnapshotAgency`（可由 `var.member_agency_name` 覆盖）并委托管理账号（详见第 11 节）。未做此步时，仅能盘点管理账号自身快照。

---

## 7. 快速开始

### 快速开始（一键部署调度）


```bash
# 1) 通过会话环境变量配置 AK/SK（deploy_code 经 TF_VAR_* 读取）
export HW_ACCESS_KEY="<AK>"
export HW_SECRET_KEY="<SK>"
export HW_REGION_NAME="cn-north-4"
export TF_VAR_hw_access_key="$HW_ACCESS_KEY"
export TF_VAR_hw_secret_key="$HW_SECRET_KEY"

# 2) 初始化（首次会下载 huaweicloud provider）
cd infra
terraform init

# 3) 预览将要创建的资源
terraform plan

# 4) 应用（创建 委托 + 函数 + 定时器；默认不建 OBS/NAT）
# null_resource 会在 apply 后用 SDK 自动上传函数代码（自包含 pip 构建 + 上传）
terraform apply
```

> 真实 AK/SK 请在华为云「我的凭证」页面获取，通过会话环境变量 / `TF_VAR_*` 提供给部署脚本，由 `null_resource` 的
> `local-exec environment` 临时传入；请勿将凭据写入任何交付文件（`*.tf`、
> `terraform.tfvars` 应加入 gitignore）。代码中统一使用 `<HW_ACCESS_KEY>` 占位符。
>
> 若仅修改函数环境变量（`user_data`），`terraform apply` 走元数据更新即可；
> 若需重建函数代码，可 `terraform apply -replace=huaweicloud_fgs_function.snapshot_report`。

---

## 8. 分步部署

1. `terraform init` → 下载 `huaweicloud/huaweicloud` provider。
2. `terraform plan` → 确认资源清单（应仅含 `huaweicloud_identity_agency` / `huaweicloud_fgs_function` / `huaweicloud_fgs_function_trigger`，以及可选 `huaweicloud_obs_bucket`）。
3. `terraform apply` → 输出函数 URN、委托名、定时器 ID。

---

## 9. 使用方法 / 验证

### 使用方法与验证


**A. 本地 CLI（推荐先验证逻辑）**

```bash
export HW_ACCESS_KEY="<AK>"
export HW_SECRET_KEY="<SK>"
# 仅盘点当前账号
python src/adapter/snapshot_report.py --region cn-north-4 --file out.csv
# 跨组织盘点（需成员账号已建委托）
python src/adapter/snapshot_report.py --region cn-north-4 --file out.csv --agency OrgSnapshotAgency
```

**B. FunctionGraph（调度形态）**

- 部署后函数由 TIMER 每天 08:00 触发；手动验证可在控制台「测试」或同步调用：
```bash
# 经 FunctionGraph SDK 同步调用取返回 CSV
```
- 函数返回体含 `rows`（快照条数）与 `csv`（完整报告文本）。

> 部署完成后请按本节步骤自行验证，确认业务功能可用，方可视为交付完成。

---

## 10. 清理资源

```bash
cd infra
terraform destroy
```

---

## 11. 详细说明

- **跨账号模型**：管理账号持有永久 AK/SK；对每个成员账号调用 `CreateTemporaryAccessKeyByAgency(agency_name="OrgSnapshotAgency", session_user.domain_name=<成员账号名>)` 取得临时凭证，再构建 EVS 客户端（需 `KeystoneListAuthProjects` 解析成员账号 `project_id`）。
- **分页**：EVS `ListSnapshots` 以 `limit=1000, offset` 翻页直到不足一页。
- **标签**：优先读取 EVS 响应内联 `tags`；缺失且开启 `--with-tags` 时走 TMS（`resource_type="snapshots"`），任一失败即关闭后续 TMS 调用以避免海量报错。
- **访问密钥配置**：CLI 读 `HW_ACCESS_KEY/SK`；FunctionGraph 从 `handler` 的 `context` 读取委托临时凭证。

### 11a. 成员账号一次性建委托


在**每个成员账号**内（用该账号自身 AK/SK）执行：

```bash
export HW_ACCESS_KEY="<成员账号 AK>"
export HW_SECRET_KEY="<成员账号 SK>"
python src/adapter/create_member_agency.py \
--region cn-north-4 \
--agency-name OrgSnapshotAgency \
--mgmt-domain-id <管理账号 domain id>
```

> 管理账号 domain id 可由管理账号调用 IAM `KeystoneListAuthDomains` 取得。
>
> 注意区分两个委托：
> - **执行委托** `ebs-snapshot-fg-agency`（`var.function_agency_name`）：函数资源本身的 `agency` 字段，让 FunctionGraph 能调用华为云 API（含 `IAM Agency Lambda WhiteList` 以调用 Agency 临时密钥）。由 Terraform 自动创建。
> - **成员委托** `OrgSnapshotAgency`（`var.member_agency_name`）：建在**每个成员账号**内、委托给管理账号，函数经它 `CreateTemporaryAccessKeyByAgency` 取得成员账号临时凭证。由 `create_member_agency.py` 在每个成员账号创建。
> 两者名称不同、作用域不同，请勿混淆。

---

## 12. 依赖

- 华为云 Python SDK：`huaweicloudsdkevs` / `huaweicloudsdkorganizations` / `huaweicloudsdkiam` / `huaweicloudsdktms` / `huaweicloudsdkobs`。
- 跨账号 Agency 范式参考 `Hatz-D/huaweicloud-organizations-accounts-projects`。

---

## 13. FAQ / 故障排除

- **`Organizations.1015: this operation can be called from any account in the organization`**：当前账号**不是组织管理账号**或未启用 Organizations，`ListAccounts` 被拒。本工具已优雅调整为「仅扫描当前账号」，日志会明确提示；如需全组织扫描，请用组织管理账号运行，或在该账号启用 Organizations。
- **函数执行报 403（跨账号）**：确认成员账号委托已建且委托管理账号；函数执行委托需含调用 Agency 临时密钥的权限（如 `IAM Agency Lambda WhiteList`，必要时追加 `iam:agencies:createTemporaryAccessKey` 自定义策略）。
- **函数日志全空 / 返回默认空响应**：运行期未打入 SDK（函数处于空壳）。本实践通过 `null_resource` 在 `terraform apply` 后自动 `pip` 构建并上传代码；若手动改动，可重跑 `terraform apply` 或 `terraform apply -replace=huaweicloud_fgs_function.snapshot_report`。
- **EVS `limit/offset` 不返回数据**：确认区域与成员账号 `project_id` 解析正确（函数日志会打印账号与快照数）。
- **`ModuleNotFoundError: No module named 'defusedxml'` 等**：函数包缺少传递依赖。用 `src/functions/build_function_package.py`（基于 `pip install --target`，含全部传递依赖）重新打包。

---

## 14. 贡献指南

变更请保持「无真实凭据落盘」「无公网 NAT」两条硬约束。

---

## 15. 许可证

本实践采用 **MIT No Attribution** 许可证，详见仓库根目录 [`LICENSE`](LICENSE) 文件。


本实践以 MIT-0 许可证发布。

---

## 16. 联系方式 / 维护者
| 项 | 内容 |
|---|---|
| **创建者** | [dgc-git](https://github.com/dgc-git) |
| **维护者** | [dgc-git](https://github.com/dgc-git) |
| **联系方式** | GitHub Issues / PR（见[第 14 节](#14-贡献指南)） |

# 部署指南

本指南描述如何部署组织级云硬盘（EVS）快照盘点方案。所有命令使用相对路径，在仓库根目录执行；涉及访问密钥的部分均为占位说明，不包含真实值。

## 1. 前置准备

| 项 | 要求 |
|---|---|
| 账号 | 华为云管理账号，具备组织服务、IAM 委托、函数计算创建权限；按需具备对象存储创建权限 |
| 命令行工具 | Terraform >= 1.5（推荐 1.9+），Python 3 与 pip（本地命令行形态需要） |
| 区域 | 默认 `cn-north-4`（华北-北京四），可在变量文件中调整 |
| 组织权限 | 管理账号已开通组织服务，成员账号可创建委托并授予快照读取权限 |
| 访问密钥 | 在华为云「我的凭证」页面获取 Access Key 与 Secret Key |

## 2. 配置访问凭据

部署与接口调用所需的访问密钥通过会话环境变量提供，由部署脚本读取。请在当前会话中设置：

```bash
export HW_ACCESS_KEY="<你的AccessKey>"
export HW_SECRET_KEY="<你的SecretKey>"
export HW_REGION_NAME="cn-north-4"
```

Terraform 变量从会话环境读取上述值，用于部署期上传函数代码：

```bash
export TF_VAR_hw_access_key="$HW_ACCESS_KEY"
export TF_VAR_hw_secret_key="$HW_SECRET_KEY"
```

> 请勿将 Access Key / Secret Key 写入任何 `.tf`、`.tfvars` 或提交到版本库；`terraform.tfvars` 应加入忽略列表。生产环境建议使用临时凭据或统一凭证管理服务。

## 3. 准备成员账号委托（跨账号前置）

若需盘点成员账号快照，需先在每个成员账号中一次性创建委托，授予云硬盘只读权限，并允许管理账号承担该委托。委托名称默认由变量 `member_agency_name` 指定，管理账号侧的委托由变量 `function_agency_name` 指定。未配置委托时，盘点范围仅为管理账号自身。

## 4. 初始化与部署

```bash
cd infra
terraform init
terraform plan
terraform apply
```

- `terraform init` 下载 `huaweicloud` provider。
- `terraform plan` 预览资源清单，应包含 IAM 委托、函数计算、定时器触发器，以及按需创建的对象存储桶。
- `terraform apply` 创建资源，并由部署脚本在函数代码就绪后上传程序包。`apply` 完成后输出函数名、函数 URN、委托名、定时器 ID 与（如有）报告桶名。

> 若仅需更新函数配置，重新执行 `apply` 即可；若需重新上传函数代码，可针对函数资源执行替换式应用。

## 5. 使用方法与验证

**A. 本地命令行（推荐先验证逻辑）**

在会话中保留访问密钥后运行盘点脚本：

```bash
# 仅盘点当前账号
python src/adapter/snapshot_report.py --region cn-north-4 --file out.csv

# 跨组织盘点（需成员账号已创建委托）
python src/adapter/snapshot_report.py --region cn-north-4 --file out.csv --agency <委托名称>
```

检查生成的 `out.csv` 是否包含快照条数与明细字段。

**B. 函数调度形态**

部署完成后，函数由定时器按计划周期（默认每日 08:00）触发。可在控制台对函数执行测试，或通过函数计算 SDK 同步调用获取返回体；返回体包含快照条数与完整报告文本。

## 6. 调整调度与规格

通过变量调整调度周期与函数规格：

| 变量 | 说明 |
|---|---|
| `schedule_cron` | 定时器触发表达式，默认每日执行 |
| `memory_size` | 函数内存规格，账号 / 快照较多时可调高 |
| `timeout` | 函数超时时间，数据量大时可调高 |
| `report_obs_bucket` | 报告对象存储桶名，留空则不落盘到对象存储 |

修改后重新执行 `plan` / `apply` 生效。

## 7. 清理资源

```bash
cd infra
terraform destroy
```

销毁后建议到控制台确认 IAM 委托、函数计算、定时器触发器与（如有）对象存储桶均已删除；成员账号中预先创建的委托需按组织治理流程单独回收。

## 8. 常见问题

- **函数未按时触发**：检查定时器触发器状态与 `schedule_cron` 表达式。
- **跨账号盘点为空**：确认成员账号委托已创建且授予快照读取权限，管理账号已获得承担该委托的授权。
- **报告缺少标签字段**：确认已启用 TMS 查询且快照带有标签。
- **函数执行超时**：调高 `timeout` 与 `memory_size`，或缩小单次盘点的账号 / 区域范围。

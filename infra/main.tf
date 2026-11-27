terraform {
  required_version = ">= 1.5"

  required_providers {
    huaweicloud = {
      source  = "huaweicloud/huaweicloud"
      version = "~> 1.70"
    }
  }

  backend "local" {
    path = "terraform.tfstate"
  }
}

# Provider 凭据从环境变量读取，绝不硬编码：
#   export HW_ACCESS_KEY="<AK>"
#   export HW_SECRET_KEY="<SK>"
#   export HW_REGION_NAME="cn-north-4"
provider "huaweicloud" {
  region = var.region
}

# ---------------------------------------------------------------------------
# 执行委托（FREE）：FunctionGraph 经此委托访问华为云服务
#  - 全局：Organizations 只读 + 调用 Agency 临时密钥所需权限
#  - 项目级：本账号 EVS / TMS 只读
# 注意：本方案不创建 VPC / 子网 / NAT / EIP / 安全组，函数走 FunctionGraph 默认出口，
#       不依赖公网 NAT。
# ---------------------------------------------------------------------------
resource "huaweicloud_identity_agency" "functiongraph" {
  name                  = var.function_agency_name
  description           = "EVS 快照全组织盘点 - FunctionGraph 执行委托"
  duration              = "FOREVER"
  delegated_service_name = "op_svc_cff"

  domain_roles = [
    "Organizations ReadOnlyAccess",
    "IAM Agency Lambda WhiteList",
  ]

  project_role {
    project = var.region
    roles = [
      "EVS ReadOnlyAccess",
      "TMS ReadOnlyAccess",
    ]
  }
}

# ---------------------------------------------------------------------------
# FunctionGraph 函数（无服务器执行）
# 代码包 = 函数入口 + 华为云 SDK（纯 Python，已在 dist/snapshot_report.zip 打入）
# ---------------------------------------------------------------------------
resource "huaweicloud_fgs_function" "snapshot_report" {
  app         = "default"
  name        = "ebs_snapshot_reporter_func"
  agency      = huaweicloud_identity_agency.functiongraph.name
  handler     = "index.handler"
  memory_size = var.memory_size
  timeout     = var.timeout
  runtime     = "Python3.9"

  code_type = "zip"
  code_url  = "${path.module}/../src/functions/dist/snapshot_report.zip"

  user_data = jsonencode({
    REGION      = var.region
    AGENCY_NAME = var.member_agency_name
    WITH_TAGS   = "true"
  })

  # 代码由 null_resource 经 SDK 上传，忽略 code_url 避免 provider 每次 apply
  # 重传代码触发 FSS.1012；user_data 变更走元数据更新（不重传代码）。
  lifecycle {
    ignore_changes = [code_url]
  }
}

# ---------------------------------------------------------------------------
# 定时触发器（FREE）：FunctionGraph TIMER 触发器
# ---------------------------------------------------------------------------
resource "huaweicloud_fgs_function_trigger" "timer" {
  function_urn = huaweicloud_fgs_function.snapshot_report.urn
  type         = "TIMER"
  status       = "ACTIVE"
  event_data   = jsonencode({
    name          = "ebs_snapshot_reporter_timer"
    schedule_type = "Cron"
    schedule      = var.schedule_cron
    user_event    = ""
  })

  # ACTIVE 状态触发器不可 in-place 更新，让出运行期字段所有权
  lifecycle {
    ignore_changes = [event_data, status]
  }
}

# ---------------------------------------------------------------------------
# 代码部署（terraform 本地 zip 上传在部分 provider 版本不可靠，这里用 SDK
# 在 apply 后把函数代码上传到线上函数，保证 `terraform apply` 一次自洽）。
# 部署脚本自包含：会自动 pip 构建 zip 并上传，仅需 python3 + 网络。
# 凭据经 environment 注入，不落盘。
# ---------------------------------------------------------------------------
locals {
  # 解析为干净绝对路径，避免 Windows 下 local-exec shell 对 ../ 的处理差异
  deploy_script = abspath("${path.module}/../src/functions/deploy_code.py")
}

resource "null_resource" "deploy_function_code" {
  depends_on = [huaweicloud_fgs_function.snapshot_report]

  triggers = {
    function_urn = huaweicloud_fgs_function.snapshot_report.urn
  }

  provisioner "local-exec" {
    # Windows 下默认 cmd.exe 会破坏带引号绝对路径的 python 命令；用 bash -c 解释
    interpreter = ["bash", "-c"]
    command     = "python \"${local.deploy_script}\" --urn \"${huaweicloud_fgs_function.snapshot_report.urn}\" --region \"${var.region}\" --build"
    environment = {
      HW_ACCESS_KEY = var.hw_access_key
      HW_SECRET_KEY = var.hw_secret_key
    }
  }
}

# ---------------------------------------------------------------------------
# 可选 OBS 桶：仅当指定 report_obs_bucket 时创建（存储费按字节，极低）
# ---------------------------------------------------------------------------
resource "huaweicloud_obs_bucket" "report" {
  count  = var.report_obs_bucket != "" ? 1 : 0
  bucket = var.report_obs_bucket
  acl    = "private"

  tags = {
    project = var.project_name
    source  = "huaweicloud-practice"
  }
}

variable "region" {
  description = "华为云区域"
  type        = string
  default     = "cn-north-4"
}

variable "project_name" {
  description = "资源命名前缀"
  type        = string
  default     = "ebs-snapshot-reporter"
}

variable "function_agency_name" {
  description = "FunctionGraph 执行委托名（函数凭此调用 Organizations / 跨账号临时密钥 / EVS）"
  type        = string
  default     = "ebs-snapshot-fg-agency"
}

variable "member_agency_name" {
  description = "成员账号中创建的委托名（函数凭此经 CreateTemporaryAccessKeyByAgency 取得成员账号临时凭证）；与执行委托 function_agency_name 是两回事"
  type        = string
  default     = "OrgSnapshotAgency"
}

variable "report_obs_bucket" {
  description = "报告落盘 OBS 桶名（全局唯一）；留空则不创建 OBS，报告仅经函数返回值输出"
  type        = string
  default     = ""
}

variable "schedule_cron" {
  description = "TIMER 触发器 cron（FunctionGraph 格式：6 段，秒在首位）。默认每天 08:00"
  type        = string
  default     = "0 0 8 * * ?"
}

variable "memory_size" {
  description = "函数内存（MB）"
  type        = number
  default     = 256
}

variable "timeout" {
  description = "函数超时（秒）"
  type        = number
  default     = 300
}

# 凭据：经环境变量注入。
# 通过 TF_VAR_hw_access_key / TF_VAR_hw_secret_key 传入，或写入本地 gitignored 的 terraform.tfvars。
variable "hw_access_key" {
  description = "华为云 Access Key（经 TF_VAR 注入，勿提交）"
  type        = string
  sensitive   = true
  default     = ""
}

variable "hw_secret_key" {
  description = "华为云 Secret Key（经 TF_VAR 注入，勿提交）"
  type        = string
  sensitive   = true
  default     = ""
}

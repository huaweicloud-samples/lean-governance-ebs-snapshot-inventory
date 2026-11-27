output "function_name" {
  description = "FunctionGraph 函数名"
  value       = huaweicloud_fgs_function.snapshot_report.name
}

output "function_urn" {
  description = "FunctionGraph 函数 URN"
  value       = huaweicloud_fgs_function.snapshot_report.urn
}

output "agency_name" {
  description = "执行委托名"
  value       = huaweicloud_identity_agency.functiongraph.name
}

output "timer_trigger_id" {
  description = "定时触发器 ID"
  value       = huaweicloud_fgs_function_trigger.timer.id
}

output "report_obs_bucket" {
  description = "报告落盘 OBS 桶（未启用则为空）"
  value       = var.report_obs_bucket != "" ? huaweicloud_obs_bucket.report[0].bucket : ""
}

output "cost_note" {
  description = "资源形态摘要"
  value       = "无 VPC/NAT/EIP；FunctionGraph 按调用计费（免费额度内≈0）；定时器免费；OBS 仅按字节计费"
}

variable "env" { type = string }
variable "aws_region" { type = string }
variable "subnet_ids" { type = list(string) }
variable "security_group_id" { type = string }
variable "cluster_name" {
  type        = string
  description = "EKS cluster resource name"
}

variable "vpc_id" {
  type        = string
  description = "VPC ID for destroy-time cleanup of LB/ENI/EIP"
}

variable "sqs_queue_arns" {
  type        = list(string)
  description = "SQS queue ARNs for IRSA"
  default     = []
}

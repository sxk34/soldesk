variable "env" {
  type    = string
  default = "prod"
}

variable "aws_region" {
  type    = string
  default = "ap-northeast-2"
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "eks_cluster_name" {
  type    = string
  default = "ticketing-eks"
}

variable "github_repo" {
  description = "GitHub 리포지토리 (owner/repo)"
  type        = string
  default     = "your-org/ticketing"
}

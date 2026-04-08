output "rds_writer_endpoint" {
  value     = module.rds.writer_endpoint
  sensitive = true
}

output "rds_reader_endpoint" {
  value     = module.rds.reader_endpoint
  sensitive = true
}

output "redis_endpoint" {
  value     = module.elasticache.redis_endpoint
  sensitive = true
}

output "sqs_queue_url" {
  value = module.sqs.reservation_queue_url
}

output "eks_cluster_name" {
  value = module.eks.cluster_name
}

output "vpc_id" {
  value = module.network.vpc_id
}

output "alb_controller_role_arn" {
  value = module.eks.alb_controller_role_arn
}

output "cluster_autoscaler_role_arn" {
  value = module.eks.cluster_autoscaler_role_arn
}

output "sqs_access_role_arn" {
  value = module.eks.sqs_access_role_arn
}

output "aws_region" {
  value = var.aws_region
}

output "aws_account_id" {
  value = data.aws_caller_identity.current.account_id
}

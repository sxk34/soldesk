terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }
  }
  required_version = ">= 1.5.0"
}

provider "aws" {
  region = var.aws_region
}

module "network" {
  source           = "./modules/network"
  env              = var.env
  aws_region       = var.aws_region
  eks_cluster_name = var.eks_cluster_name
}

module "sqs" {
  source = "./modules/sqs"
  env    = var.env
}

module "elasticache" {
  source            = "./modules/elasticache"
  env               = var.env
  subnet_ids        = module.network.private_subnet_ids
  security_group_id = module.network.redis_sg_id
  depends_on        = [module.network]
}

module "rds" {
  source            = "./modules/rds"
  env               = var.env
  subnet_ids        = module.network.private_subnet_ids
  security_group_id = module.network.rds_sg_id
  db_password       = var.db_password
  depends_on        = [module.network]
}

module "eks" {
  source            = "./modules/eks"
  env               = var.env
  aws_region        = var.aws_region
  vpc_id            = module.network.vpc_id
  subnet_ids        = module.network.public_subnet_ids
  security_group_id = module.network.eks_sg_id
  cluster_name      = var.eks_cluster_name
  sqs_queue_arns    = [module.sqs.reservation_queue_arn, module.sqs.reservation_dlq_arn]
  depends_on        = [module.network]
}

data "aws_caller_identity" "current" {}

# EKS 노드 → RDS 접근 허용
resource "aws_security_group_rule" "rds_from_eks_cluster_sg" {
  type                     = "ingress"
  description              = "MySQL from EKS cluster SG"
  from_port                = 3306
  to_port                  = 3306
  protocol                 = "tcp"
  security_group_id        = module.network.rds_sg_id
  source_security_group_id = module.eks.cluster_security_group_id
}

# EKS 노드 → Redis 접근 허용
resource "aws_security_group_rule" "redis_from_eks_cluster_sg" {
  type                     = "ingress"
  description              = "Redis from EKS cluster SG"
  from_port                = 6379
  to_port                  = 6379
  protocol                 = "tcp"
  security_group_id        = module.network.redis_sg_id
  source_security_group_id = module.eks.cluster_security_group_id
}

# EKS 노드 → SQS 접근 허용
resource "aws_iam_role_policy" "eks_node_sqs" {
  name = "ticketing-eks-node-sqs"
  role = module.eks.node_role_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "sqs:SendMessage",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
      ]
      Resource = [
        module.sqs.reservation_queue_arn,
        module.sqs.reservation_dlq_arn,
      ]
    }]
  })
}

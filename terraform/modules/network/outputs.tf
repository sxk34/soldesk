output "vpc_id" { value = aws_vpc.main.id }
output "public_subnet_ids" { value = aws_subnet.public[*].id }
output "private_subnet_ids" { value = aws_subnet.private[*].id }
output "eks_sg_id" { value = aws_security_group.was.id }
output "rds_sg_id" { value = aws_security_group.db.id }
output "redis_sg_id" { value = aws_security_group.cache.id }
output "monitoring_sg_id" { value = aws_security_group.web.id }

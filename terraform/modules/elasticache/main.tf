resource "aws_elasticache_subnet_group" "main" {
  name       = "ticketing-redis-subnet-group"
  subnet_ids = var.subnet_ids
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "ticketing-redis"
  description          = "Ticketing Redis replication group"
  engine               = "redis"
  engine_version       = "7.0"
  node_type            = "cache.t3.micro"
  port                 = 6379

  # Primary 1 + Replica 1 = 총 2노드, 자동 failover
  num_cache_clusters   = 2
  automatic_failover_enabled = true
  multi_az_enabled           = true

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [var.security_group_id]

  # 스냅샷 비활성화 (프리티어 최적화)
  snapshot_retention_limit = 0

  tags = { Name = "ticketing-redis", Environment = var.env }
}

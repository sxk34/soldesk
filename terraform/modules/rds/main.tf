resource "aws_db_subnet_group" "main" {
  name       = "prod-rds-subnet-group"
  subnet_ids = var.subnet_ids
  tags       = { Name = "prod-rds-subnet-group", Environment = var.env }
}

# Primary (Writer) - db.t3.micro MySQL
resource "aws_db_instance" "writer" {
  identifier        = "prod-ticketing-writer"
  engine            = "mysql"
  engine_version    = "8.0"
  instance_class    = "db.t3.micro"
  allocated_storage = 20
  storage_type      = "gp2"

  db_name  = "ticketing"
  username = "root"
  password = var.db_password
  port     = 3306

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [var.security_group_id]

  backup_retention_period = 1
  skip_final_snapshot     = true
  deletion_protection     = false

  tags = { Name = "ticketing-mysql-writer", Role = "primary", Environment = var.env }
}

# Read Replica (Reader) - db.t3.micro
resource "aws_db_instance" "reader" {
  identifier          = "prod-ticketing-reader"
  replicate_source_db = aws_db_instance.writer.identifier
  instance_class      = "db.t3.micro"

  skip_final_snapshot = true
  deletion_protection = false

  tags = { Name = "ticketing-mysql-reader", Role = "replica", Environment = var.env }
}

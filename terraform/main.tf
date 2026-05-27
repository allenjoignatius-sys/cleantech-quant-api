###############################################################################
# Cleantech Quant API — AWS Production Infrastructure
# Provisions: VPC, EC2 (API), RDS PostgreSQL, ElastiCache Redis, S3, ACM, ALB
#
# Usage:
#   cd terraform
#   terraform init
#   terraform workspace new production
#   terraform plan -var-file="environments/production/terraform.tfvars"
#   terraform apply -var-file="environments/production/terraform.tfvars"
###############################################################################

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    random = { source = "hashicorp/random"; version = "~> 3.6" }
  }

  # Remote state — uncomment once your S3 bucket exists
  # backend "s3" {
  #   bucket         = "cleantech-quant-tf-state"
  #   key            = "production/terraform.tfstate"
  #   region         = "us-east-1"
  #   encrypt        = true
  #   dynamodb_table = "cleantech-quant-tf-lock"
  # }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = "cleantech-quant-api"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# ─── Data sources ─────────────────────────────────────────────────────────────

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]  # Canonical
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# ─── VPC & Networking ─────────────────────────────────────────────────────────

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags = { Name = "${var.project_name}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project_name}-igw" }
}

# Public subnets (for ALB)
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.${count.index}.0/24"
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags = { Name = "${var.project_name}-public-${count.index + 1}" }
}

# Private subnets (for RDS, ElastiCache, EC2)
resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.${count.index + 10}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags = { Name = "${var.project_name}-private-${count.index + 1}" }
}

resource "aws_eip" "nat" {
  domain = "vpc"
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  depends_on    = [aws_internet_gateway.main]
  tags = { Name = "${var.project_name}-nat" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${var.project_name}-rt-public" }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  tags = { Name = "${var.project_name}-rt-private" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ─── Security Groups ──────────────────────────────────────────────────────────

resource "aws_security_group" "alb" {
  name        = "${var.project_name}-sg-alb"
  vpc_id      = aws_vpc.main.id
  description = "ALB — allow HTTPS/HTTP from internet"

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "api" {
  name        = "${var.project_name}-sg-api"
  vpc_id      = aws_vpc.main.id
  description = "API EC2 — allow from ALB only"

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  # SSH from specific IPs only
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidrs
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "rds" {
  name        = "${var.project_name}-sg-rds"
  vpc_id      = aws_vpc.main.id
  description = "RDS PostgreSQL — allow from API EC2 only"

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.api.id]
  }
}

resource "aws_security_group" "redis" {
  name        = "${var.project_name}-sg-redis"
  vpc_id      = aws_vpc.main.id
  description = "ElastiCache Redis — allow from API EC2 only"

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.api.id]
  }
}

# ─── RDS PostgreSQL 15 ────────────────────────────────────────────────────────

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-db-subnet-group"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_parameter_group" "postgres15" {
  name   = "${var.project_name}-pg15-params"
  family = "postgres15"

  parameter { name = "work_mem";             value = "16384" }  # 16MB
  parameter { name = "maintenance_work_mem"; value = "131072" } # 128MB
  parameter { name = "effective_cache_size"; value = "524288" } # 512MB
  parameter { name = "log_min_duration_statement"; value = "1000" } # log slow queries >1s
  parameter { name = "shared_preload_libraries"; value = "pg_stat_statements" }
}

resource "aws_db_instance" "postgres" {
  identifier              = "${var.project_name}-postgres"
  engine                  = "postgres"
  engine_version          = "15.6"
  instance_class          = var.db_instance_class
  allocated_storage       = 50
  max_allocated_storage   = 500   # auto-scaling storage
  storage_type            = "gp3"
  storage_encrypted       = true

  db_name  = "cleantech_api"
  username = "ctquser"
  password = random_password.db.result

  vpc_security_group_ids = [aws_security_group.rds.id]
  db_subnet_group_name   = aws_db_subnet_group.main.name
  parameter_group_name   = aws_db_parameter_group.postgres15.name

  backup_retention_period = 7
  backup_window           = "03:00-04:00"
  maintenance_window      = "sun:04:00-sun:05:00"
  deletion_protection     = var.environment == "production"
  skip_final_snapshot     = var.environment != "production"
  final_snapshot_identifier = var.environment == "production" ? "${var.project_name}-final-snapshot" : null

  performance_insights_enabled = true
  monitoring_interval          = 60
  monitoring_role_arn          = aws_iam_role.rds_monitoring.arn

  tags = { Name = "${var.project_name}-rds" }
}

resource "aws_iam_role" "rds_monitoring" {
  name = "${var.project_name}-rds-monitoring"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

# ─── ElastiCache Redis 7 ──────────────────────────────────────────────────────

resource "random_password" "redis" {
  length  = 32
  special = false
}

resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.project_name}-redis-subnet-group"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "${var.project_name}-redis"
  description          = "Redis for rate limiting, caching, and task queue"

  node_type            = var.redis_node_type
  num_cache_clusters   = var.environment == "production" ? 2 : 1
  engine               = "redis"
  engine_version       = "7.1"
  port                 = 6379

  subnet_group_name    = aws_elasticache_subnet_group.main.name
  security_group_ids   = [aws_security_group.redis.id]

  auth_token               = random_password.redis.result
  transit_encryption_enabled = true
  at_rest_encryption_enabled = true

  automatic_failover_enabled = var.environment == "production"
  multi_az_enabled           = var.environment == "production"

  snapshot_retention_limit = 3
  snapshot_window          = "02:00-03:00"

  tags = { Name = "${var.project_name}-redis" }
}

# ─── EC2 Instance (API + Workers) ─────────────────────────────────────────────

resource "aws_iam_instance_profile" "api" {
  name = "${var.project_name}-api-profile"
  role = aws_iam_role.api_ec2.name
}

resource "aws_iam_role" "api_ec2" {
  name = "${var.project_name}-api-ec2"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.api_ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_policy" "s3_reports" {
  name = "${var.project_name}-s3-reports"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
      Resource = [
        aws_s3_bucket.reports.arn,
        "${aws_s3_bucket.reports.arn}/*"
      ]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "s3_reports" {
  role       = aws_iam_role.api_ec2.name
  policy_arn = aws_iam_policy.s3_reports.arn
}

resource "aws_key_pair" "deployer" {
  key_name   = "${var.project_name}-deployer"
  public_key = var.ssh_public_key
}

resource "aws_instance" "api" {
  ami                  = data.aws_ami.ubuntu.id
  instance_type        = var.api_instance_type
  subnet_id            = aws_subnet.private[0].id
  security_groups      = [aws_security_group.api.id]
  iam_instance_profile = aws_iam_instance_profile.api.name
  key_name             = aws_key_pair.deployer.key_name

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
    encrypted   = true
  }

  user_data = base64encode(templatefile("${path.module}/templates/user_data.sh", {
    db_url        = "postgresql+asyncpg://ctquser:${random_password.db.result}@${aws_db_instance.postgres.endpoint}/cleantech_api"
    redis_url     = "rediss://:${random_password.redis.result}@${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379"
    s3_bucket     = aws_s3_bucket.reports.id
    aws_region    = var.aws_region
    environment   = var.environment
    secret_key    = random_password.api_secret.result
    jwt_secret    = random_password.jwt_secret.result
  }))

  tags = { Name = "${var.project_name}-api" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "random_password" "api_secret" { length = 64; special = false }
resource "random_password" "jwt_secret" { length = 64; special = false }

# ─── Application Load Balancer ────────────────────────────────────────────────

resource "aws_lb" "main" {
  name               = "${var.project_name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  enable_deletion_protection = var.environment == "production"

  access_logs {
    bucket  = aws_s3_bucket.logs.id
    prefix  = "alb"
    enabled = true
  }

  tags = { Name = "${var.project_name}-alb" }
}

resource "aws_lb_target_group" "api" {
  name     = "${var.project_name}-tg-api"
  port     = 8000
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id

  health_check {
    path                = "/v1/health"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }
}

resource "aws_lb_target_group_attachment" "api" {
  target_group_arn = aws_lb_target_group.api.arn
  target_id        = aws_instance.api.id
  port             = 8000
}

# ACM Certificate (assumes DNS is already delegated to Route53)
resource "aws_acm_certificate" "api" {
  domain_name       = "api.${var.domain_name}"
  validation_method = "DNS"
  lifecycle { create_before_destroy = true }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.api.arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# ─── S3 Buckets ───────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "reports" {
  bucket = "${var.project_name}-reports-${var.environment}"
}

resource "aws_s3_bucket_versioning" "reports" {
  bucket = aws_s3_bucket.reports.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket                  = aws_s3_bucket.reports.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket" "logs" {
  bucket        = "${var.project_name}-logs-${var.environment}"
  force_destroy = var.environment != "production"
}

resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    id     = "expire-old-logs"
    status = "Enabled"
    expiration { days = 90 }
    filter { prefix = "alb/" }
  }
}

# ─── CloudWatch Alarms ────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "api_cpu" {
  alarm_name          = "${var.project_name}-high-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "API EC2 CPU > 80%"
  dimensions          = { InstanceId = aws_instance.api.id }
  alarm_actions       = var.environment == "production" ? [aws_sns_topic.alerts.arn] : []
}

resource "aws_cloudwatch_metric_alarm" "rds_connections" {
  alarm_name          = "${var.project_name}-rds-connections"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "DatabaseConnections"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "RDS connections > 80"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.postgres.id }
  alarm_actions       = var.environment == "production" ? [aws_sns_topic.alerts.arn] : []
}

resource "aws_sns_topic" "alerts" {
  name = "${var.project_name}-ops-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.ops_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.ops_email
}

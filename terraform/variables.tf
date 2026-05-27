###############################################################################
# Cleantech Quant API — Terraform Variables
###############################################################################

variable "project_name" {
  description = "Project identifier used in all resource names"
  type        = string
  default     = "cleantech-quant"
}

variable "environment" {
  description = "Deployment environment: staging | production"
  type        = string
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be 'staging' or 'production'."
  }
}

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "domain_name" {
  description = "Root domain (e.g. cleantechquant.io). API will be at api.<domain>"
  type        = string
}

variable "ssh_public_key" {
  description = "SSH public key for EC2 access (cat ~/.ssh/id_rsa.pub)"
  type        = string
  sensitive   = true
}

variable "allowed_ssh_cidrs" {
  description = "CIDR blocks allowed to SSH to API instances"
  type        = list(string)
  default     = []  # No SSH by default — use AWS SSM Session Manager instead
}

variable "ops_email" {
  description = "Email for CloudWatch alarm notifications"
  type        = string
  default     = ""
}

# ─── Instance sizing ──────────────────────────────────────────────────────────

variable "api_instance_type" {
  description = "EC2 instance type for the API server"
  type        = string
  default     = "t3.medium"  # $30/mo — upgrade to c5.xlarge at scale
  # Recommended progression: t3.medium → t3.large → c5.xlarge → c5.2xlarge
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.medium"  # $50/mo — upgrade to db.r6g.large at >500 users
}

variable "redis_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.t3.micro"  # $20/mo — upgrade to cache.r6g.large for production
}

# ─── Cost reference ───────────────────────────────────────────────────────────
# Default config (t3.medium + db.t3.medium + cache.t3.micro):
#   EC2:          ~$30/mo
#   RDS:          ~$50/mo
#   ElastiCache:  ~$20/mo
#   ALB:          ~$18/mo
#   NAT Gateway:  ~$32/mo
#   S3/CloudWatch: ~$5/mo
#   Total:        ~$155/mo
#
# At 10 Enterprise subscribers ($15,000/mo revenue): 1% infra cost ratio ✓

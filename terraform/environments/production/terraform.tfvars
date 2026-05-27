# ─── Production Environment Variables ─────────────────────────────────────────

project_name      = "cleantech-quant"
environment       = "production"
aws_region        = "us-east-1"
domain_name       = "cleantechquant.io"
ops_email         = "alerts@cleantechquant.io"

# Replace this with the output of `cat ~/.ssh/id_rsa.pub` from your local machine
ssh_public_key    = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC... replace-me-with-your-real-key"

# ─── Sizing Overrides (Optional) ──────────────────────────────────────────────
# These match your defaults, but are exposed here for easy upgrading later.
api_instance_type = "t3.medium"
db_instance_class = "db.t3.medium"
redis_node_type   = "cache.t3.micro"
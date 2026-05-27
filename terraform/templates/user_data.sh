#!/bin/bash
set -e

# 1. System updates and required packages
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y docker.io docker-compose git awscli jq

# 2. Start and enable Docker daemon
systemctl enable docker
systemctl start docker
usermod -aG docker ubuntu

# 3. Create application directory
mkdir -p /opt/cleantech-quant
cd /opt/cleantech-quant

# 4. Inject Terraform variables into the production .env file
# Terraform will automatically replace the ${...} placeholders 
# before injecting this script into the EC2 instance.
cat <<EOF > .env
APP_NAME="NH3 Intelligence API"
DEBUG=false
ENVIRONMENT=${environment}
AWS_REGION=${aws_region}

# Dynamically injected endpoints from AWS RDS and ElastiCache
DATABASE_URL=${db_url}
REDIS_URL=${redis_url}
AWS_S3_BUCKET=${s3_bucket}

# Cryptographic keys
SECRET_KEY=${secret_key}
JWT_SECRET=${jwt_secret}
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
API_KEY_PREFIX=ctq_

# Payment & Email (To be updated manually or via secrets manager)
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
SENDGRID_API_KEY=
FROM_EMAIL=alerts@cleantechquant.io
EOF

# Note: Once the EC2 instance is up, your CI/CD pipeline (e.g., GitHub Actions) 
# would SSH or use AWS Systems Manager to pull the code and run `docker-compose up -d`.
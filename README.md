# Cleantech Quant API — Complete Deployment Guide

## Architecture Overview

```
Internet → Traefik (reverse proxy + TLS) → FastAPI app (4 workers)
                                          → Celery workers (scraping)
                                          → Celery beat (scheduler)
Database: PostgreSQL 15 (persistent)
Cache:    Redis 7 (rate limiting, task queue, response cache)
Storage:  AWS S3 (report files)
```

---

## Quick Start (Local Development)

### 1. Clone and configure
```bash
git clone https://github.com/your-org/cleantech-quant-api
cd cleantech-quant-api/backend

cp .env.example .env
# Edit .env with your secrets
```

### 2. Start the full stack
```bash
cd ..
docker compose --profile dev up -d
```

The API will be live at `http://localhost:8000`
- API docs: `http://localhost:8000/docs`
- DB admin: `http://localhost:8080`

### 3. Run database migrations
```bash
docker compose exec api alembic upgrade head
```

### 4. Seed initial data
```bash
docker compose exec api python -m app.cli seed-database
```

---

## Production Deployment on AWS

### Option A: Docker on EC2 (cheapest, recommended to start)

**Recommended instance**: `t3.medium` ($30/mo) for under 100 users.
Scale to `c5.xlarge` ($150/mo) when you hit 500+ req/min.

```bash
# 1. Launch EC2 Ubuntu 22.04, open ports 80, 443
# 2. Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu

# 3. Clone and configure
git clone https://github.com/your-org/cleantech-quant-api
cd cleantech-quant-api
cp backend/.env.example backend/.env
nano backend/.env   # add your secrets

# 4. Point your DNS A record to the EC2 IP
# 5. Start with Traefik for automatic TLS
docker compose -f docker-compose.prod.yml up -d

# 6. Run migrations
docker compose exec api alembic upgrade head
```

### Option B: Railway (zero-ops, ~$20/mo to start)

```bash
# Install Railway CLI
npm install -g @railway/cli
railway login

# Deploy
railway init
railway add --database postgresql
railway add --database redis
railway up

# Set env vars
railway variables set SECRET_KEY=$(openssl rand -hex 32)
railway variables set STRIPE_SECRET_KEY=sk_live_...
```

### Option C: AWS ECS + RDS (enterprise-grade)

Use the provided `terraform/` directory:
```bash
cd terraform
terraform init
terraform plan -var="environment=production"
terraform apply
```

---

## Environment Variables Reference

```bash
# Required
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname
REDIS_URL=redis://host:6379
SECRET_KEY=<64-char random string: openssl rand -hex 32>
JWT_SECRET=<64-char random string>

# Payments
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...

# Email (for alerts)
SENDGRID_API_KEY=SG...
FROM_EMAIL=alerts@cleantechquant.io

# Storage (for report exports)
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_BUCKET=cleantech-quant-reports
AWS_REGION=us-east-1

# Optional: Monitoring
SENTRY_DSN=https://...@sentry.io/...
```

---

## API Endpoints Summary

### Authentication
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/auth/register` | Create account |
| POST | `/v1/auth/login` | Get JWT token |
| GET | `/v1/auth/me` | Current user info |
| GET | `/v1/auth/keys` | List API keys |
| POST | `/v1/auth/keys` | Create API key |
| DELETE | `/v1/auth/keys/{id}` | Revoke API key |

### Catalyst Benchmarks
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/catalysts/` | List benchmarks (filtered, paginated) |
| GET | `/v1/catalysts/stats` | Aggregate statistics |
| GET | `/v1/catalysts/compare` | Side-by-side catalyst comparison |
| GET | `/v1/catalysts/{id}` | Single benchmark detail |
| POST | `/v1/catalysts/` | Submit new benchmark (analyst+) |
| GET | `/v1/catalysts/export/csv` | CSV export (analyst+) |

### Efficiency & Degradation
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/efficiency/curves` | Degradation curves over time |
| GET | `/v1/efficiency/predict` | ML-based degradation prediction |

### Cost Models
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/costs/` | List cost datapoints |
| GET | `/v1/costs/geographies` | Available geographies |
| GET | `/v1/costs/curve/{geo}` | Cost curve over time for geography |
| POST | `/v1/costs/sensitivity` | Tornado chart / sensitivity analysis |
| GET | `/v1/costs/benchmark/landing-cost` | Published import chain costs |

### Project Intelligence
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/projects/` | List all tracked projects |
| GET | `/v1/projects/{id}` | Project detail with timeline |
| GET | `/v1/projects/map` | GeoJSON for mapping |
| GET | `/v1/projects/recent-fids` | Recent final investment decisions |

### Alerts & Webhooks
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/alerts/` | List user alerts |
| POST | `/v1/alerts/` | Create alert |
| DELETE | `/v1/alerts/{id}` | Delete alert |
| GET | `/v1/webhooks/` | List webhooks |
| POST | `/v1/webhooks/` | Register webhook |

---

## Subscription Plans & Pricing

| Feature | Free | Analyst ($499/mo) | Enterprise ($1,500/mo) |
|---------|------|-------------------|------------------------|
| API requests/day | 100 | 10,000 | Unlimited |
| Requests/minute | 10 | 100 | Custom SLA |
| API keys | 1 | 5 | 50 |
| CSV export | ❌ | ✅ | ✅ |
| Sensitivity analysis | ❌ | ✅ | ✅ |
| Raw data access | ❌ | ✅ | ✅ |
| Webhooks | ❌ | 5 | Unlimited |
| Alerts | 1 | 20 | Unlimited |
| SLA | None | 99.5% | 99.9% + support |
| Custom data ingestion | ❌ | ❌ | ✅ |

**Revenue Math**:
- 10 Enterprise subscribers = $15,000/mo = **$3,750/week**
- 20 Analyst subscribers = $9,980/mo = **$2,495/week**
- Mixed: 5 Enterprise + 15 Analyst = $15,000/mo = **$3,750/week** ✓

---

## First 90 Days Action Plan

### Week 1-2: Build the MVP
1. Set up the database with seed data from 50 scraped academic papers
2. Deploy on Railway (free tier initially)
3. Write the teardown report: "Ru vs Ni Catalyst: What the Data Actually Shows"

### Week 3-4: Validate Demand
1. Post the teardown on LinkedIn (company page, not personal)
2. Email it to 20 analysts at Japanese trading houses (JERA, Mitsui, Mitsubishi)
3. Offer 30-day free Analyst trial to first 5 respondents

### Month 2: First Revenue
1. Convert 2-3 trial users to paid Analyst ($499/mo)
2. Run 1 enterprise outreach campaign targeting project finance teams at JBIC/MUFG
3. Publish second teardown: "JERA Blue Point: The Cracker Economics Nobody Is Modeling"

### Month 3: Scale
1. Automate scrapers for weekly data refresh
2. Add webhook + alert system for project FID notifications
3. Target first Enterprise client ($1,500/mo)

---

## Support

- Docs: https://docs.cleantechquant.io
- Status: https://status.cleantechquant.io
- Email: api@cleantechquant.io

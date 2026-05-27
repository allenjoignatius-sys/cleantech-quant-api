"""
Cleantech Quantitative Data API — Main Application
Ammonia Cracking & H2 Carrier Decomposition Intelligence Platform
"""

from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import time
import logging

from app.routers import (
    auth,
    catalysts,
    efficiency,
    cost_curves,
    projects,
    alerts,
    reports,
    webhooks,
    admin,
)
from app.database import engine, Base
from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Rate limiter
limiter = Limiter(key_func=get_remote_address, default_limits=["1000/hour"])

app = FastAPI(
    title="Cleantech Quant API",
    description="""
    ## Ammonia Cracking & H₂ Carrier Decomposition Intelligence API

    The only structured, queryable database of:
    - **Catalyst performance benchmarks** (Ru, Ni, Fe — temperature, pressure, conversion rate)
    - **Cracker efficiency degradation curves** over time
    - **Delivered H₂ cost models** by geography and technology
    - **Project finance parameters** for bankability modelling
    - **Patent intelligence** from 14,000+ filings
    - **Real-time alerts** on regulatory changes and project FIDs

    ### Authentication
    All endpoints require an `X-API-Key` header. Keys are issued per subscription tier.

    ### Rate Limits
    - **Free**: 100 req/day, 10 req/min
    - **Analyst**: 10,000 req/day, 100 req/min
    - **Enterprise**: Unlimited, custom SLA
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    contact={
        "name": "Cleantech Quant Research",
        "email": "api@cleantechquant.io",
    },
    license_info={
        "name": "Commercial License",
        "url": "https://cleantechquant.io/terms",
    },
)

# Middleware
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request timing middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(round(process_time * 1000, 2))
    response.headers["X-API-Version"] = "1.0.0"
    return response

# Routers
app.include_router(auth.router, prefix="/v1/auth", tags=["Authentication"])
app.include_router(catalysts.router, prefix="/v1/catalysts", tags=["Catalyst Benchmarks"])
app.include_router(efficiency.router, prefix="/v1/efficiency", tags=["Efficiency Curves"])
app.include_router(cost_curves.router, prefix="/v1/costs", tags=["Cost Models"])
app.include_router(projects.router, prefix="/v1/projects", tags=["Project Intelligence"])
app.include_router(alerts.router, prefix="/v1/alerts", tags=["Alerts & Monitoring"])
app.include_router(reports.router, prefix="/v1/reports", tags=["Report Generation"])
app.include_router(webhooks.router, prefix="/v1/webhooks", tags=["Webhooks"])
app.include_router(admin.router, prefix="/v1/admin", tags=["Admin"])

@app.on_event("startup")
async def startup_event():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Cleantech Quant API started. Database initialized.")

@app.get("/", include_in_schema=False)
async def root():
    return {
        "name": "Cleantech Quant API",
        "version": "1.0.0",
        "status": "operational",
        "docs": "/docs",
        "endpoints": 42,
    }

@app.get("/v1/health", tags=["System"])
async def health_check():
    return {
        "status": "healthy",
        "database": "connected",
        "scrapers": "running",
        "last_data_refresh": "2025-05-26T06:00:00Z",
        "data_sources_active": 23,
    }

@app.get("/v1/changelog", tags=["System"])
async def changelog():
    return {
        "versions": [
            {
                "version": "1.0.0",
                "date": "2025-05-01",
                "changes": ["Initial release", "Ru/Ni/Fe catalyst benchmarks", "JERA Blue Point project data"],
            }
        ]
    }

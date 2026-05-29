"""
Cleantech Quant API — application entrypoint.

Mounts all v1 routers under stable prefixes, installs structured-logging /
request-id / process-time middleware, and a Redis-backed rate limiter that
protects the public Developer API (requests authenticated with an X-API-Key).
"""
import hashlib
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

# Original routers
from app.routers import (
    auth, catalysts, efficiency, cost_curves, projects, alerts, webhooks,
    admin, reports,
)
# Intelligence-module routers (self-prefixed with /v1/...)
from app.routers.literature import router as literature_router
from app.routers.energy_prices import router as energy_prices_router
from app.routers.project_databases import router as project_databases_router
from app.routers.news import router as news_router
# New enterprise routers
from app.routers import quant, scenarios, organizations

from app.config import settings
from app.database import engine, Base, get_redis_client
from app.ratelimit import RateLimiter
from app.observability import configure_logging, get_logger, set_request_id

API_VERSION = "3.0.0"
configure_logging(level=settings.LOG_LEVEL)
logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables (idempotent) — Alembic owns production migrations.
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # don't crash boot if DB is briefly unavailable
        logger.warning("startup create_all skipped", extra={"error": str(exc)})

    app.state.redis = get_redis_client() if settings.RATE_LIMIT_ENABLED else None
    app.state.rate_limiter = RateLimiter(app.state.redis)
    logger.info("api.startup", extra={"version": API_VERSION,
                                      "rate_limit_enabled": settings.RATE_LIMIT_ENABLED})
    yield
    await engine.dispose()


app = FastAPI(
    title="Cleantech Quant API",
    version=API_VERSION,
    description="Institutional green-hydrogen & derivatives market-intelligence platform.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Process-Time", "X-Request-ID", "X-API-Version",
                    "X-RateLimit-Limit", "X-RateLimit-Remaining"],
)


# ── Middleware: request id, timing, structured access log ──────────────────────
@app.middleware("http")
async def observability_and_ratelimit(request: Request, call_next):
    rid = set_request_id(request.headers.get("X-Request-ID"))
    start = time.perf_counter()

    # Rate-limit only Developer API traffic (authenticated via API key).
    api_key = request.headers.get("X-API-Key")
    if api_key and settings.RATE_LIMIT_ENABLED:
        limiter: RateLimiter = getattr(request.app.state, "rate_limiter", None) or RateLimiter(None)
        key = "apikey:" + hashlib.sha256(api_key.encode()).hexdigest()[:32]
        rl = await limiter.check(key, settings.API_RATE_LIMIT_PER_MINUTE,
                                 settings.RATE_LIMIT_WINDOW_SECONDS)
        if not rl.allowed:
            logger.warning("ratelimit.block", extra={"path": request.url.path,
                                                     "limit": rl.limit, "backend": rl.backend})
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Slow down or upgrade your plan.",
                         "limit": rl.limit, "window_seconds": settings.RATE_LIMIT_WINDOW_SECONDS},
                headers={
                    "Retry-After": str(rl.reset_after),
                    "X-RateLimit-Limit": str(rl.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-Request-ID": rid,
                },
            )

    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request.error", extra={"path": request.url.path,
                                                 "method": request.method})
        raise

    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Process-Time"] = f"{elapsed_ms:.2f}ms"
    response.headers["X-Request-ID"] = rid
    response.headers["X-API-Version"] = API_VERSION
    logger.info("request.access", extra={
        "method": request.method, "path": request.url.path,
        "status": response.status_code, "latency_ms": round(elapsed_ms, 2),
    })
    return response


# ── Routers (mounted under stable /v1 prefixes) ────────────────────────────────
app.include_router(auth.router, prefix="/v1/auth", tags=["Auth"])
app.include_router(catalysts.router, prefix="/v1/catalysts", tags=["Catalysts"])
app.include_router(efficiency.router, prefix="/v1/efficiency", tags=["Efficiency"])
app.include_router(cost_curves.router, prefix="/v1/costs", tags=["Costs"])
app.include_router(projects.router, prefix="/v1/projects", tags=["Projects"])
app.include_router(alerts.router, prefix="/v1/alerts", tags=["Alerts"])
app.include_router(webhooks.router, prefix="/v1/webhooks", tags=["Webhooks"])
app.include_router(admin.router, prefix="/v1/admin", tags=["Admin"])
app.include_router(reports.router, prefix="/v1/reports", tags=["Reports"])

# New enterprise routers
app.include_router(quant.router, prefix="/v1/quant", tags=["Quant Engineering"])
app.include_router(scenarios.router, prefix="/v1/scenarios", tags=["Scenarios"])
app.include_router(organizations.router, prefix="/v1/organizations", tags=["Organizations"])

# Self-prefixed intelligence routers
app.include_router(literature_router)
app.include_router(energy_prices_router)
app.include_router(project_databases_router)
app.include_router(news_router)


# ── System endpoints ───────────────────────────────────────────────────────────
@app.get("/", tags=["System"])
async def root():
    return {"name": settings.APP_NAME, "version": API_VERSION, "docs": "/docs", "status": "ok"}


@app.get("/health", tags=["System"])
async def health():
    return {"status": "ok", "version": API_VERSION}


@app.get("/v1/health", tags=["System"])
async def health_v1():
    return {"status": "healthy", "version": API_VERSION}


@app.get("/v1/changelog", tags=["System"])
async def changelog():
    return {
        "versions": [
            {"version": "3.0.0", "highlights": [
                "Policy & subsidy engine (IRA 45V/ITC, EU Hydrogen Bank)",
                "Monte-Carlo LCOH risk, Excel financial-model export",
                "Green NH3/MeOH/SAF commodity costs + carbon-market integration",
                "Multi-tenant organizations, RBAC, scenario comparison",
                "Resilient NER/LLM extraction; Redis rate limiting; JSON logging",
            ]},
            {"version": "2.0.0", "highlights": ["Literature, news, energy & project feeds"]},
            {"version": "1.0.0", "highlights": ["Catalyst, cost, project & efficiency APIs"]},
        ]
    }

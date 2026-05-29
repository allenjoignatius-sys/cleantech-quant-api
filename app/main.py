from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# Original routers (FIXED: costs -> cost_curves)
from app.routers import auth, catalysts, efficiency, cost_curves, projects, alerts, webhooks

# NEW intelligence module routers
from app.routers.literature import router as literature_router
from app.routers.energy_prices import router as energy_prices_router
from app.routers.project_databases import router as project_databases_router
from app.routers.news import router as news_router

# FIXED: app.core.config -> app.config
from app.config import settings
# FIXED: app.core.database -> app.database
from app.database import engine, Base

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()

app = FastAPI(
    title="Cleantech Quant API",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(catalysts.router)
app.include_router(efficiency.router)
app.include_router(cost_curves.router) # FIXED
app.include_router(projects.router)
app.include_router(alerts.router)
app.include_router(webhooks.router)

app.include_router(literature_router)
app.include_router(energy_prices_router)
app.include_router(project_databases_router)
app.include_router(news_router)

@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}

"""Router package — all FastAPI routers for the Cleantech Quant API."""
from app.routers import (
    auth,
    catalysts,
    cost_curves,
    efficiency,
    projects,
    alerts,
    reports,
    webhooks,
    admin,
)

__all__ = [
    "auth",
    "catalysts",
    "cost_curves",
    "efficiency",
    "projects",
    "alerts",
    "reports",
    "webhooks",
    "admin",
]

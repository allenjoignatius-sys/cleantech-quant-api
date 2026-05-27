"""
/v1/admin — Admin Endpoints
Protected by is_admin flag on User model.
Provides data management, scraper control, and system monitoring.
NOT exposed in public API docs (include_in_schema=False on sensitive endpoints).
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text, delete
from typing import Optional
from pydantic import BaseModel
from datetime import datetime, timedelta

from app.database import get_db, engine
from app.models import (
    User, APIKey, CatalystBenchmark, CostDatapoint, Project,
    Patent, Alert, Webhook, Report, AuditLog, SubscriptionPlan
)
from app.auth import get_current_user

router = APIRouter()


# ─── Admin guard dependency ───────────────────────────────────────────────────

async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Admin access required.",
        )
    return current_user


# ─── Schemas ──────────────────────────────────────────────────────────────────

class SystemStats(BaseModel):
    users: dict
    data: dict
    system: dict
    generated_at: datetime


class ScraperRunResult(BaseModel):
    status: str
    triggered_at: datetime
    message: str


class UserPlanUpdate(BaseModel):
    plan: SubscriptionPlan


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get(
    "/stats",
    response_model=SystemStats,
    summary="System-wide statistics dashboard",
)
async def admin_stats(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    # User stats
    total_users = (await db.execute(select(func.count(User.id)))).scalar()
    active_users = (await db.execute(
        select(func.count(User.id)).where(User.is_active == True)
    )).scalar()

    # Users by plan
    plan_dist = {}
    for plan in SubscriptionPlan:
        n = (await db.execute(
            select(func.count(User.id)).where(User.plan == plan)
        )).scalar()
        plan_dist[plan.value] = n

    # New users last 30 days
    cutoff = datetime.utcnow() - timedelta(days=30)
    new_30d = (await db.execute(
        select(func.count(User.id)).where(User.created_at >= cutoff)
    )).scalar()

    # Data stats
    benchmarks = (await db.execute(select(func.count(CatalystBenchmark.id)))).scalar()
    cost_dps = (await db.execute(select(func.count(CostDatapoint.id)))).scalar()
    projects = (await db.execute(select(func.count(Project.id)))).scalar()
    patents = (await db.execute(select(func.count(Patent.id)))).scalar()
    alerts_active = (await db.execute(
        select(func.count(Alert.id)).where(Alert.is_active == True)
    )).scalar()
    webhooks_active = (await db.execute(
        select(func.count(Webhook.id)).where(Webhook.is_active == True)
    )).scalar()

    return SystemStats(
        users={
            "total": total_users,
            "active": active_users,
            "new_last_30_days": new_30d,
            "by_plan": plan_dist,
        },
        data={
            "catalyst_benchmarks": benchmarks,
            "cost_datapoints": cost_dps,
            "projects": projects,
            "patents": patents,
            "active_alerts": alerts_active,
            "active_webhooks": webhooks_active,
        },
        system={
            "api_version": "1.0.0",
            "database": "connected",
        },
        generated_at=datetime.utcnow(),
    )


@router.get(
    "/users",
    summary="List all users (paginated)",
)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    plan: Optional[SubscriptionPlan] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    query = select(User)
    if plan:
        query = query.where(User.plan == plan)
    if search:
        query = query.where(
            User.email.ilike(f"%{search}%") | User.company.ilike(f"%{search}%")
        )

    total = (await db.execute(
        select(func.count()).select_from(query.subquery())
    )).scalar()
    query = query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(query)).scalars().all()

    return {
        "data": [
            {
                "id": u.id,
                "email": u.email,
                "company": u.company,
                "job_title": u.job_title,
                "plan": u.plan.value,
                "is_active": u.is_active,
                "requests_today": u.requests_today,
                "requests_this_month": u.requests_this_month,
                "created_at": u.created_at,
            }
            for u in rows
        ],
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


@router.patch(
    "/users/{user_id}/plan",
    summary="Update a user's subscription plan",
)
async def update_user_plan(
    user_id: str,
    payload: UserPlanUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = (await db.execute(
        select(User).where(User.id == user_id)
    )).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    old_plan = user.plan.value
    user.plan = payload.plan
    await db.commit()

    return {
        "user_id": user_id,
        "email": user.email,
        "plan_changed": f"{old_plan} → {payload.plan.value}",
    }


@router.patch(
    "/users/{user_id}/deactivate",
    summary="Deactivate a user account",
)
async def deactivate_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = (await db.execute(
        select(User).where(User.id == user_id)
    )).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    await db.commit()
    return {"status": "deactivated", "user_id": user_id}


@router.post(
    "/scrapers/trigger",
    response_model=ScraperRunResult,
    summary="Manually trigger a full scrape cycle",
)
async def trigger_scraper(
    background_tasks: BackgroundTasks,
    admin: User = Depends(require_admin),
):
    background_tasks.add_task(_run_scraper_cycle)
    return ScraperRunResult(
        status="triggered",
        triggered_at=datetime.utcnow(),
        message="Full scrape cycle queued. Results visible in /v1/admin/stats within 5-10 minutes.",
    )


@router.get(
    "/data/benchmarks",
    summary="Admin view of all benchmarks (unfiltered)",
)
async def admin_benchmarks(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    unverified_only: bool = Query(False, description="Show only unverified/pending review records"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    query = select(CatalystBenchmark).order_by(CatalystBenchmark.created_at.desc())
    total = (await db.execute(
        select(func.count()).select_from(query.subquery())
    )).scalar()
    query = query.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(query)).scalars().all()
    return {
        "data": [
            {
                "id": r.id,
                "catalyst_type": r.catalyst_type.value if r.catalyst_type else None,
                "temperature_celsius": r.temperature_celsius,
                "nh3_conversion_pct": r.nh3_conversion_pct,
                "source_type": r.source_type.value if r.source_type else None,
                "source_doi": r.source_doi,
                "year": r.year,
                "created_at": r.created_at,
            }
            for r in rows
        ],
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


@router.delete(
    "/data/benchmarks/{benchmark_id}",
    status_code=204,
    summary="Delete a benchmark (admin only)",
)
async def delete_benchmark(
    benchmark_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    result = await db.execute(
        delete(CatalystBenchmark).where(CatalystBenchmark.id == benchmark_id)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Benchmark not found")
    await db.commit()


@router.get(
    "/audit-log",
    summary="Recent audit log entries",
)
async def audit_log(
    limit: int = Query(100, ge=1, le=500),
    user_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    rows = (await db.execute(query)).scalars().all()
    return {
        "data": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "action": r.action,
                "resource_type": r.resource_type,
                "ip_address": r.ip_address,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


@router.get(
    "/db/health",
    summary="Raw database health check",
    include_in_schema=False,
)
async def db_health(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    result = await db.execute(text("SELECT version(), NOW()"))
    row = result.first()
    return {"postgres_version": str(row[0]), "server_time": str(row[1])}


# ─── Internal helpers ────────────────────────────────────────────────────────

async def _run_scraper_cycle():
    """Called as background task from trigger endpoint."""
    from app.scrapers.runner import run_once
    await run_once()

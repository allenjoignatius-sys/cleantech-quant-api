"""
/v1/alerts — Configurable Alert System
Users set threshold conditions; the system monitors data and triggers
notifications via email, webhook, or Slack when conditions are met.

Alert types:
  - efficiency_threshold: catalyst conversion drops below X%
  - cost_movement: delivered H2 cost moves by >X% in Y days
  - regulatory_change: new filing from specified regulator
  - project_fid: tracked project reaches FID milestone
  - patent_filing: new patent from specified assignee
"""

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from typing import Optional, List, Any
from pydantic import BaseModel, Field, validator
from datetime import datetime
import json

from app.database import get_db
from app.models import Alert, AlertType, User, Webhook
from app.auth import get_current_user, require_plan

router = APIRouter()


# ─── Schemas ──────────────────────────────────────────────────────────────────

class AlertConditionBase(BaseModel):
    """Flexible condition logic stored as JSON."""
    pass


class EfficiencyThresholdCondition(BaseModel):
    catalyst_type: str
    metric: str = Field("nh3_conversion_pct", description="nh3_conversion_pct | energy_penalty_pct")
    operator: str = Field("lt", description="lt | gt | lte | gte")
    threshold: float
    consecutive_readings: int = Field(1, ge=1, le=10, description="Trigger after N consecutive readings")


class CostMovementCondition(BaseModel):
    geography: Optional[str] = None
    technology: Optional[str] = None
    direction: str = Field("any", description="up | down | any")
    pct_change: float = Field(5.0, ge=0.1, le=100, description="Minimum % change to trigger")
    window_days: int = Field(7, ge=1, le=90)


class ProjectMilestoneCondition(BaseModel):
    project_ids: Optional[List[str]] = None   # None = watch all
    milestone: str = Field("fid", description="fid | construction | operational | cancelled")
    countries: Optional[List[str]] = None


class PatentAlertCondition(BaseModel):
    assignees: Optional[List[str]] = None     # company names
    keywords: Optional[List[str]] = None      # title/abstract keywords
    ipc_codes: Optional[List[str]] = None


class AlertCreate(BaseModel):
    name: str = Field(..., max_length=255)
    alert_type: AlertType
    conditions: dict = Field(..., description="Type-specific condition object (see schemas above)")
    notification_channels: List[str] = Field(
        default=["email"],
        description="email | webhook | slack",
    )
    is_active: bool = True

    @validator("notification_channels")
    def validate_channels(cls, v):
        allowed = {"email", "webhook", "slack"}
        invalid = set(v) - allowed
        if invalid:
            raise ValueError(f"Invalid channels: {invalid}. Allowed: {allowed}")
        return v


class AlertOut(BaseModel):
    id: str
    name: str
    alert_type: str
    conditions: dict
    is_active: bool
    last_triggered: Optional[datetime]
    trigger_count: int
    notification_channels: list
    created_at: datetime

    class Config:
        from_attributes = True


class AlertEventOut(BaseModel):
    """A single trigger event for an alert (history)."""
    alert_id: str
    alert_name: str
    triggered_at: datetime
    trigger_data: dict
    channels_notified: List[str]


# ─── Plan limits ──────────────────────────────────────────────────────────────

PLAN_ALERT_LIMITS = {
    "free": 1,
    "analyst": 20,
    "enterprise": 200,
}


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get(
    "/",
    response_model=List[AlertOut],
    summary="List all alerts for the current user",
)
async def list_alerts(
    active_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(Alert).where(Alert.user_id == current_user.id)
    if active_only:
        query = query.where(Alert.is_active == True)
    query = query.order_by(Alert.created_at.desc())
    rows = (await db.execute(query)).scalars().all()
    return [AlertOut.from_orm(r) for r in rows]


@router.post(
    "/",
    response_model=AlertOut,
    status_code=201,
    summary="Create a new alert",
    description="""
    Set a threshold-based alert on any data in the system.

    **Example — alert when Ru conversion drops below 95%:**
    ```json
    {
      "name": "Ru efficiency drop",
      "alert_type": "efficiency_threshold",
      "conditions": {
        "catalyst_type": "ruthenium",
        "metric": "nh3_conversion_pct",
        "operator": "lt",
        "threshold": 95.0
      },
      "notification_channels": ["email", "webhook"]
    }
    ```

    **Example — alert on any new project FID in Japan:**
    ```json
    {
      "name": "Japan FID watch",
      "alert_type": "project_fid",
      "conditions": {
        "milestone": "fid",
        "countries": ["Japan"]
      },
      "notification_channels": ["email"]
    }
    ```
    """,
)
async def create_alert(
    payload: AlertCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Check plan limit
    limit = PLAN_ALERT_LIMITS.get(current_user.plan.value, 1)
    existing = (await db.execute(
        select(func.count(Alert.id))
        .where(and_(Alert.user_id == current_user.id, Alert.is_active == True))
    )).scalar()

    if existing >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Alert limit reached ({limit} active alerts on {current_user.plan.value} plan). "
                   "Deactivate an existing alert or upgrade your plan.",
        )

    alert = Alert(
        user_id=current_user.id,
        name=payload.name,
        alert_type=payload.alert_type,
        conditions=payload.conditions,
        notification_channels=payload.notification_channels,
        is_active=payload.is_active,
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return AlertOut.from_orm(alert)


@router.get(
    "/{alert_id}",
    response_model=AlertOut,
    summary="Get alert details",
)
async def get_alert(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = (await db.execute(
        select(Alert).where(
            and_(Alert.id == alert_id, Alert.user_id == current_user.id)
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found")
    return AlertOut.from_orm(row)


@router.patch(
    "/{alert_id}",
    response_model=AlertOut,
    summary="Update alert (toggle active, change conditions)",
)
async def update_alert(
    alert_id: str,
    is_active: Optional[bool] = None,
    conditions: Optional[dict] = None,
    notification_channels: Optional[List[str]] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = (await db.execute(
        select(Alert).where(
            and_(Alert.id == alert_id, Alert.user_id == current_user.id)
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found")

    if is_active is not None:
        row.is_active = is_active
    if conditions is not None:
        row.conditions = conditions
    if notification_channels is not None:
        row.notification_channels = notification_channels

    await db.commit()
    await db.refresh(row)
    return AlertOut.from_orm(row)


@router.delete(
    "/{alert_id}",
    status_code=204,
    summary="Delete an alert",
)
async def delete_alert(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = (await db.execute(
        select(Alert).where(
            and_(Alert.id == alert_id, Alert.user_id == current_user.id)
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found")
    await db.delete(row)
    await db.commit()


@router.post(
    "/{alert_id}/test",
    summary="Send a test notification for an alert",
    description="Triggers a synthetic test notification on all configured channels. Useful for validating webhook URLs and email delivery.",
)
async def test_alert(
    alert_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = (await db.execute(
        select(Alert).where(
            and_(Alert.id == alert_id, Alert.user_id == current_user.id)
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found")

    test_payload = {
        "alert_id": row.id,
        "alert_name": row.name,
        "type": "test",
        "triggered_at": datetime.utcnow().isoformat(),
        "test": True,
        "message": f"Test notification for alert: {row.name}",
    }

    background_tasks.add_task(_deliver_alert, row, test_payload, current_user.email)

    return {
        "status": "test_sent",
        "channels": row.notification_channels,
        "alert_id": alert_id,
    }


@router.get(
    "/templates",
    summary="Pre-built alert templates for common use cases",
)
async def alert_templates(current_user: User = Depends(get_current_user)):
    """
    Ready-to-use alert configurations for the most common analyst workflows.
    Copy the conditions block directly into POST /v1/alerts.
    """
    return {
        "templates": [
            {
                "name": "JERA Blue Point — FID / Construction Update",
                "alert_type": "project_fid",
                "conditions": {
                    "project_ids": ["jera-blue-point"],
                    "milestone": "any",
                },
                "recommended_for": "Project finance analysts tracking JERA's Louisiana supply chain",
            },
            {
                "name": "Ruthenium catalyst efficiency below 95%",
                "alert_type": "efficiency_threshold",
                "conditions": {
                    "catalyst_type": "ruthenium",
                    "metric": "nh3_conversion_pct",
                    "operator": "lt",
                    "threshold": 95.0,
                },
                "recommended_for": "Cracker operators tracking fleet performance",
            },
            {
                "name": "Delivered H₂ cost moves >5% in 7 days (Japan route)",
                "alert_type": "cost_movement",
                "conditions": {
                    "geography": "Japan",
                    "direction": "any",
                    "pct_change": 5.0,
                    "window_days": 7,
                },
                "recommended_for": "Energy procurement teams at Japanese utilities",
            },
            {
                "name": "New Amogy / Topsoe / KBR patent filing",
                "alert_type": "patent_filing",
                "conditions": {
                    "assignees": ["Amogy", "Topsoe", "KBR", "ThyssenKrupp Uhde"],
                },
                "recommended_for": "Competitive intelligence teams at technology vendors",
            },
            {
                "name": "Any new cracking project FID globally",
                "alert_type": "project_fid",
                "conditions": {
                    "milestone": "fid",
                },
                "recommended_for": "Market intelligence — track industry pace of deployment",
            },
        ]
    }


# ─── Internal delivery helper (called from Celery tasks) ──────────────────────

async def _deliver_alert(alert: Alert, payload: dict, user_email: str):
    """
    Internal: deliver a triggered alert to all configured channels.
    In production this is called from the Celery alert_checker task.
    """
    channels = alert.notification_channels or ["email"]

    if "email" in channels:
        # Integrate with SendGrid in tasks.py
        pass

    if "webhook" in channels:
        # Dispatch to all registered webhooks (see webhooks.py)
        pass

    if "slack" in channels:
        # Slack incoming webhook integration
        pass

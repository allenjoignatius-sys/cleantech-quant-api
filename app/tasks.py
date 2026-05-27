"""
Celery Task Definitions
All background work runs through these tasks:
  - scraper_cycle         : run all scrapers every 6 hours
  - check_alerts          : evaluate all active alert conditions every 15 min
  - send_weekly_digest    : auto-generate + email digest every Monday 08:00 UTC
  - deliver_webhook       : retry-able webhook delivery
  - reset_daily_counters  : reset requests_today at midnight UTC
  - send_alert_email      : SendGrid email for triggered alerts
"""

import asyncio
import logging
from datetime import datetime, timedelta
from celery import Celery
from celery.schedules import crontab

from app.config import settings

logger = logging.getLogger(__name__)

# ─── Celery app ───────────────────────────────────────────────────────────────

celery_app = Celery(
    "cleantech_quant",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,                   # ack after task completes, not on receipt
    worker_prefetch_multiplier=1,          # fair task distribution
    result_expires=86400,                  # results expire after 24h
    task_soft_time_limit=300,              # 5-min soft limit
    task_time_limit=600,                   # 10-min hard limit
)

# ─── Beat schedule ────────────────────────────────────────────────────────────

celery_app.conf.beat_schedule = {
    # Scrape all data sources every 6 hours
    "scraper-cycle-every-6h": {
        "task": "app.tasks.scraper_cycle",
        "schedule": crontab(minute=0, hour="*/6"),
    },
    # Check alert conditions every 15 minutes
    "check-alerts-every-15min": {
        "task": "app.tasks.check_all_alerts",
        "schedule": crontab(minute="*/15"),
    },
    # Send weekly digest every Monday at 08:00 UTC
    "weekly-digest-monday": {
        "task": "app.tasks.send_weekly_digest",
        "schedule": crontab(hour=8, minute=0, day_of_week=1),
    },
    # Reset daily API usage counters at midnight UTC
    "reset-daily-counters-midnight": {
        "task": "app.tasks.reset_daily_counters",
        "schedule": crontab(hour=0, minute=0),
    },
    # Send monthly usage report to all paid users on the 1st
    "monthly-usage-report": {
        "task": "app.tasks.send_monthly_usage_report",
        "schedule": crontab(hour=9, minute=0, day_of_month=1),
    },
}


# ─── Helper: run async in sync context ────────────────────────────────────────

def run_async(coro):
    """Run an async coroutine from a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── Tasks ────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="app.tasks.scraper_cycle",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def scraper_cycle(self):
    """
    Run all scrapers (Crossref, EPO, IRENA, ProjectIntelligence).
    Deduplicates and upserts new data into the database.
    """
    logger.info(f"[{datetime.utcnow().isoformat()}] Starting scraper cycle")
    try:
        from app.scrapers.runner import run_once
        stats = run_async(run_once())
        logger.info(f"Scraper cycle complete: {stats}")
        return stats
    except Exception as exc:
        logger.error(f"Scraper cycle failed: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.tasks.check_all_alerts",
    bind=True,
)
def check_all_alerts(self):
    """
    Evaluate all active alert conditions against current data.
    Triggers notification delivery for any conditions met.
    """
    logger.info("Checking alert conditions...")
    try:
        run_async(_check_alerts_async())
    except Exception as exc:
        logger.error(f"Alert check failed: {exc}")


async def _check_alerts_async():
    from app.database import AsyncSessionLocal
    from app.models import Alert, AlertType, CatalystBenchmark, CostDatapoint
    from sqlalchemy import select, and_

    async with AsyncSessionLocal() as db:
        alerts = (await db.execute(
            select(Alert).where(Alert.is_active == True)
        )).scalars().all()

        for alert in alerts:
            try:
                triggered = False
                trigger_data = {}

                if alert.alert_type == AlertType.efficiency_threshold:
                    triggered, trigger_data = await _check_efficiency_threshold(db, alert)
                elif alert.alert_type == AlertType.cost_movement:
                    triggered, trigger_data = await _check_cost_movement(db, alert)
                elif alert.alert_type == AlertType.project_fid:
                    triggered, trigger_data = await _check_project_fid(db, alert)
                elif alert.alert_type == AlertType.patent_filing:
                    triggered, trigger_data = await _check_patent_filing(db, alert)

                if triggered:
                    alert.trigger_count = (alert.trigger_count or 0) + 1
                    alert.last_triggered = datetime.utcnow()
                    await db.commit()
                    # Queue notification delivery
                    deliver_alert_notification.delay(
                        alert_id=str(alert.id),
                        trigger_data=trigger_data,
                    )
            except Exception as e:
                logger.error(f"Error checking alert {alert.id}: {e}")


async def _check_efficiency_threshold(db, alert) -> tuple[bool, dict]:
    """Check if latest catalyst efficiency data breaches threshold."""
    from app.models import CatalystBenchmark
    from sqlalchemy import select, and_

    conds = alert.conditions or {}
    cat_type = conds.get("catalyst_type", "ruthenium")
    metric = conds.get("metric", "nh3_conversion_pct")
    operator = conds.get("operator", "lt")
    threshold = conds.get("threshold", 95.0)

    latest = (await db.execute(
        select(CatalystBenchmark)
        .where(CatalystBenchmark.catalyst_type == cat_type)
        .order_by(CatalystBenchmark.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()

    if not latest:
        return False, {}

    value = getattr(latest, metric, None)
    if value is None:
        return False, {}

    triggered = False
    if operator == "lt" and value < threshold:
        triggered = True
    elif operator == "gt" and value > threshold:
        triggered = True
    elif operator == "lte" and value <= threshold:
        triggered = True
    elif operator == "gte" and value >= threshold:
        triggered = True

    return triggered, {
        "metric": metric,
        "value": value,
        "threshold": threshold,
        "operator": operator,
        "catalyst_type": cat_type,
    }


async def _check_cost_movement(db, alert) -> tuple[bool, dict]:
    """Check if delivered H2 cost has moved significantly."""
    from app.models import CostDatapoint
    from sqlalchemy import select, func

    conds = alert.conditions or {}
    geography = conds.get("geography")
    window_days = conds.get("window_days", 7)
    min_pct = conds.get("pct_change", 5.0)

    cutoff = datetime.utcnow() - timedelta(days=window_days)
    query = select(CostDatapoint)
    if geography:
        query = query.where(CostDatapoint.geography.ilike(f"%{geography}%"))

    recent = (await db.execute(
        query.where(CostDatapoint.created_at >= cutoff)
        .order_by(CostDatapoint.created_at.desc())
        .limit(2)
    )).scalars().all()

    if len(recent) < 2:
        return False, {}

    newer, older = recent[0].total_delivered_h2_cost, recent[1].total_delivered_h2_cost
    pct_change = abs(newer - older) / older * 100 if older else 0

    triggered = pct_change >= min_pct
    return triggered, {
        "geography": geography,
        "pct_change": round(pct_change, 2),
        "from_cost": round(older, 3),
        "to_cost": round(newer, 3),
    }


async def _check_project_fid(db, alert) -> tuple[bool, dict]:
    """Check for new project FID announcements since last alert trigger."""
    from app.models import Project
    from sqlalchemy import select

    conds = alert.conditions or {}
    milestone = conds.get("milestone", "fid")
    countries = conds.get("countries")
    since = alert.last_triggered or (datetime.utcnow() - timedelta(hours=1))

    query = select(Project).where(
        Project.updated_at >= since,
        Project.status == milestone,
    )
    if countries:
        query = query.where(Project.location_country.in_(countries))

    new_projects = (await db.execute(query)).scalars().all()
    if not new_projects:
        return False, {}

    return True, {
        "milestone": milestone,
        "new_projects": [{"name": p.name, "developer": p.developer} for p in new_projects],
    }


async def _check_patent_filing(db, alert) -> tuple[bool, dict]:
    """Check for new patents from specified assignees."""
    from app.models import Patent
    from sqlalchemy import select, or_

    conds = alert.conditions or {}
    assignees = conds.get("assignees", [])
    since = alert.last_triggered or (datetime.utcnow() - timedelta(hours=24))

    query = select(Patent).where(Patent.created_at >= since)
    if assignees:
        conditions = [Patent.assignee.ilike(f"%{a}%") for a in assignees]
        query = query.where(or_(*conditions))

    new_patents = (await db.execute(query.limit(10))).scalars().all()
    if not new_patents:
        return False, {}

    return True, {
        "new_patents": [{"number": p.patent_number, "title": p.title[:100]} for p in new_patents],
    }


@celery_app.task(
    name="app.tasks.deliver_alert_notification",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def deliver_alert_notification(self, alert_id: str, trigger_data: dict):
    """
    Deliver a triggered alert via email + webhooks.
    Retried up to 3 times on failure.
    """
    try:
        run_async(_deliver_notification_async(alert_id, trigger_data))
    except Exception as exc:
        raise self.retry(exc=exc)


async def _deliver_notification_async(alert_id: str, trigger_data: dict):
    from app.database import AsyncSessionLocal
    from app.models import Alert, User
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        alert = (await db.execute(
            select(Alert).where(Alert.id == alert_id)
        )).scalar_one_or_none()
        if not alert:
            return

        user = (await db.execute(
            select(User).where(User.id == alert.user_id)
        )).scalar_one_or_none()
        if not user:
            return

        channels = alert.notification_channels or ["email"]

        if "email" in channels:
            await _send_alert_email(user.email, alert.name, trigger_data)

        if "webhook" in channels:
            from app.routers.webhooks import broadcast_event
            await broadcast_event("alert.triggered", {
                "alert_id": alert_id,
                "alert_name": alert.name,
                "trigger_data": trigger_data,
            }, db)


async def _send_alert_email(to_email: str, alert_name: str, trigger_data: dict):
    """Send alert notification via SendGrid."""
    if not settings.SENDGRID_API_KEY:
        logger.warning("SendGrid not configured — skipping email")
        return
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail

        message = Mail(
            from_email=settings.FROM_EMAIL,
            to_emails=to_email,
            subject=f"[NH₃ Intelligence] Alert triggered: {alert_name}",
            html_content=f"""
            <h2 style="color:#0F6E56">Alert Triggered: {alert_name}</h2>
            <p>Your alert was triggered at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}.</p>
            <pre style="background:#f5f5f5;padding:12px;border-radius:4px">{str(trigger_data)}</pre>
            <p><a href="https://app.cleantechquant.io/alerts">View all alerts →</a></p>
            <hr/>
            <small>NH₃ Intelligence by Cleantech Quant Research · 
            <a href="https://cleantechquant.io/unsubscribe">Unsubscribe</a></small>
            """,
        )
        sg = sendgrid.SendGridAPIClient(settings.SENDGRID_API_KEY)
        sg.send(message)
    except Exception as e:
        logger.error(f"Email delivery failed: {e}")


@celery_app.task(name="app.tasks.send_weekly_digest")
def send_weekly_digest():
    """
    Auto-generate weekly intelligence digest and email to all analyst+ subscribers.
    Runs every Monday 08:00 UTC.
    """
    logger.info("Generating weekly digest...")
    run_async(_weekly_digest_async())


async def _weekly_digest_async():
    from app.database import AsyncSessionLocal
    from app.models import User, SubscriptionPlan, CatalystBenchmark, Project
    from sqlalchemy import select, and_

    async with AsyncSessionLocal() as db:
        # Get all paid subscribers
        paid_users = (await db.execute(
            select(User).where(
                and_(
                    User.is_active == True,
                    User.plan.in_([SubscriptionPlan.analyst, SubscriptionPlan.enterprise])
                )
            )
        )).scalars().all()

        # Get data from last 7 days
        cutoff = datetime.utcnow() - timedelta(days=7)
        new_benchmarks = (await db.execute(
            select(CatalystBenchmark).where(CatalystBenchmark.created_at >= cutoff)
        )).scalars().all()

        new_projects = (await db.execute(
            select(Project).where(Project.updated_at >= cutoff)
        )).scalars().all()

        digest_html = _build_digest_html(new_benchmarks, new_projects)

        for user in paid_users:
            await _send_alert_email(
                user.email,
                f"NH₃ Intelligence Weekly Digest — {datetime.utcnow().strftime('%b %d, %Y')}",
                {"html_content": digest_html},
            )

        logger.info(f"Weekly digest sent to {len(paid_users)} subscribers")


def _build_digest_html(benchmarks, projects) -> str:
    date_str = datetime.utcnow().strftime("%B %d, %Y")
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto">
      <div style="background:#0F6E56;padding:24px;border-radius:8px 8px 0 0">
        <h1 style="color:white;margin:0;font-size:22px">NH₃ Intelligence Weekly Digest</h1>
        <p style="color:#A7F3D0;margin:4px 0 0">{date_str}</p>
      </div>
      <div style="padding:24px;border:1px solid #E5E7EB;border-top:none">
        <h2 style="color:#0F6E56">This Week's Summary</h2>
        <ul>
          <li><strong>{len(benchmarks)} new catalyst benchmarks</strong> added to the database</li>
          <li><strong>{len(projects)} project updates</strong> tracked this week</li>
        </ul>
        <h3>New Catalyst Data</h3>
        {'<p>No new benchmarks this week.</p>' if not benchmarks else
          '<ul>' + ''.join(f'<li>{b.catalyst_type.value if b.catalyst_type else "Unknown"} — {b.nh3_conversion_pct:.1f}% conversion @ {b.temperature_celsius:.0f}°C ({b.year})</li>' for b in benchmarks[:5]) + '</ul>'
        }
        <h3>Project Updates</h3>
        {'<p>No project updates this week.</p>' if not projects else
          '<ul>' + ''.join(f'<li><strong>{p.name}</strong> — {p.status}</li>' for p in projects[:5]) + '</ul>'
        }
        <div style="margin-top:24px;padding-top:16px;border-top:1px solid #E5E7EB">
          <a href="https://app.cleantechquant.io" style="background:#0F6E56;color:white;padding:10px 20px;border-radius:4px;text-decoration:none">Open Dashboard →</a>
        </div>
      </div>
      <div style="padding:16px;text-align:center;color:#9CA3AF;font-size:12px">
        NH₃ Intelligence · Cleantech Quant Research · 
        <a href="https://cleantechquant.io/unsubscribe" style="color:#9CA3AF">Unsubscribe</a>
      </div>
    </div>
    """


@celery_app.task(name="app.tasks.reset_daily_counters")
def reset_daily_counters():
    """Reset requests_today counter for all users at midnight UTC."""
    run_async(_reset_counters_async())


async def _reset_counters_async():
    from app.database import AsyncSessionLocal
    from app.models import User
    from sqlalchemy import update

    async with AsyncSessionLocal() as db:
        await db.execute(update(User).values(requests_today=0))
        await db.commit()
    logger.info("Daily API usage counters reset")


@celery_app.task(name="app.tasks.send_monthly_usage_report")
def send_monthly_usage_report():
    """Email monthly usage summary to each paid subscriber on the 1st."""
    run_async(_monthly_report_async())


async def _monthly_report_async():
    from app.database import AsyncSessionLocal
    from app.models import User, SubscriptionPlan
    from sqlalchemy import select, and_

    async with AsyncSessionLocal() as db:
        paid = (await db.execute(
            select(User).where(
                and_(
                    User.is_active == True,
                    User.plan.in_([SubscriptionPlan.analyst, SubscriptionPlan.enterprise])
                )
            )
        )).scalars().all()

        for user in paid:
            await _send_alert_email(
                user.email,
                f"NH₃ Intelligence — Your Monthly Usage Summary",
                {
                    "requests_this_month": user.requests_this_month,
                    "plan": user.plan.value,
                },
            )
        logger.info(f"Monthly reports sent to {len(paid)} subscribers")

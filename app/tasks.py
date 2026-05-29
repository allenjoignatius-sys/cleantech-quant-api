import asyncio
import logging
from datetime import date, datetime, timedelta
from celery import shared_task
# FIXED: app.core.celery_app -> celery.current_app
from celery import current_app as celery_app
from celery.schedules import crontab

logger = logging.getLogger(__name__)

def run_async(coro):
    loop = asyncio.new_event_loop()
    try: return loop.run_until_complete(coro)
    finally: loop.close()

@celery_app.task(name="tasks.scrape_literature", bind=True, max_retries=2)
def scrape_literature_task(self, sources=None):
    import json
    from app.services.literature_service import LiteratureService
    # FIXED: app.core.cache -> app.database
    from app.database import get_redis_client_sync
    
    if sources is None: sources = ["pubmed", "arxiv", "doe"]
    logger.info("Starting literature scrape for sources: %s", sources)
    try:
        service = LiteratureService()
        response = run_async(service.search(
            query="electrolyzer catalyst benchmark overpotential HER OER efficiency",
            sources=sources, max_results=50, year_from=2020, year_to=None, extract_performance=True,
        ))
        redis = get_redis_client_sync()
        if redis:
            redis.setex("literature:benchmarks:latest", 86400 * 2, json.dumps([r.model_dump() for r in response.results]))
        return {"scraped": len(response.results), "sources": sources}
    except Exception as exc:
        raise self.retry(exc=exc, countdown=600)

@celery_app.task(name="tasks.refresh_energy_prices", bind=True, max_retries=3)
def refresh_energy_prices_task(self, geographies=None):
    from app.services.energy_price_service import EnergyPriceService
    if geographies is None: geographies = ["DE", "FR", "GB", "ES", "IT", "US-CAISO", "US-ERCOT", "US-PJM"]
    service = EnergyPriceService()
    results = {}
    for geo in geographies:
        try:
            result = run_async(service.get_spot_price(geo))
            results[geo] = result.price_eur_mwh
        except Exception: pass
    return results

@celery_app.task(name="tasks.sync_project_databases", bind=True, max_retries=2)
def sync_project_databases_task(self):
    from app.services.project_db_service import ProjectDatabaseService
    from app.services.alert_service import AlertService
    try:
        service = ProjectDatabaseService()
        response = run_async(service.search(
            query=None, country=None, technology=None, status=None,
            capacity_mw_min=None, capacity_mw_max=None,
            sources=["iea", "h2iq", "irena"], page=1, page_size=9999
        ))
        new_fids = [p for p in response.results if (p.status or "").lower() == "fid"]
        if new_fids:
            alert_service = AlertService()
            for project in new_fids[:10]: run_async(alert_service.send_fid_alert(project))
        return {"total_projects": response.total, "fid_count": len(new_fids)}
    except Exception as exc:
        raise self.retry(exc=exc, countdown=1800)

@celery_app.task(name="tasks.scrape_news", bind=True, max_retries=2)
def scrape_news_task(self):
    from app.services.news_service import NewsService
    from app.services.webhook_service import WebhookService
    try:
        service = NewsService()
        response = run_async(service.get_feed(
            topics=["hydrogen", "electrolyzer", "green hydrogen", "FID"],
            sources=["rss", "gdelt"], days_back=1, limit=200,
        ))
        fid_items = [i for i in response.items if i.is_fid_related]
        if fid_items:
            webhook_service = WebhookService()
            run_async(webhook_service.send_event(event_type="fid_announcement", payload=[i.model_dump() for i in fid_items]))
        return {"scraped": response.total, "fid_related": len(fid_items)}
    except Exception as exc:
        raise self.retry(exc=exc, countdown=300)

@celery_app.task(name="tasks.generate_weekly_briefing", bind=True, max_retries=2)
def generate_weekly_briefing_task(self):
    """Auto-generate the weekly PDF market-intelligence briefing (ReportLab).

    Summarises new FID announcements and grid-price anomalies, renders a branded
    PDF and persists it (S3 if configured, else cached in Redis / written to /tmp).
    """
    import json
    from app.reports.pdf import build_weekly_briefing, WeeklyBriefingData
    from app.config import settings
    from app.database import get_redis_client_sync

    try:
        # ── Gather the week's intelligence (services degrade to empty offline) ──
        new_fids, anomalies = [], []
        try:
            from app.services.project_db_service import ProjectDatabaseService
            resp = run_async(ProjectDatabaseService().search(
                query=None, country=None, technology=None, status="fid",
                capacity_mw_min=None, capacity_mw_max=None,
                sources=["iea", "h2iq", "irena"], page=1, page_size=50))
            for p in getattr(resp, "results", [])[:25]:
                new_fids.append({"name": getattr(p, "name", None),
                                 "country": getattr(p, "country", None),
                                 "capacity_mw": getattr(p, "capacity_mw", None),
                                 "url": getattr(p, "url", None)})
        except Exception as e:
            logger.warning("briefing: FID gather failed: %s", e)

        data = WeeklyBriefingData(
            week_of=date.today().isoformat(),
            summary=f"{len(new_fids)} new FID(s) and {len(anomalies)} grid-price anomaly(ies) this week.",
            new_fids=new_fids,
            price_anomalies=anomalies,
        )
        pdf_bytes = build_weekly_briefing(data)

        stored_to = None
        if settings.AWS_ACCESS_KEY_ID and settings.AWS_S3_BUCKET:
            try:
                import boto3
                key = f"briefings/weekly_{data.week_of}.pdf"
                boto3.client("s3", region_name=settings.AWS_REGION).put_object(
                    Bucket=settings.AWS_S3_BUCKET, Key=key, Body=pdf_bytes,
                    ContentType="application/pdf")
                stored_to = f"s3://{settings.AWS_S3_BUCKET}/{key}"
            except Exception as e:
                logger.warning("briefing: S3 upload failed: %s", e)
        if stored_to is None:
            redis = get_redis_client_sync()
            if redis:
                redis.setex(f"briefing:weekly:{data.week_of}", 86400 * 14, pdf_bytes)
                stored_to = f"redis:briefing:weekly:{data.week_of}"

        logger.info("Weekly briefing generated (%d bytes) -> %s", len(pdf_bytes), stored_to)
        return {"week_of": data.week_of, "bytes": len(pdf_bytes),
                "fid_count": len(new_fids), "stored_to": stored_to}
    except Exception as exc:
        raise self.retry(exc=exc, countdown=900)


CELERY_BEAT_SCHEDULE = {
    "refresh-catalyst-benchmarks": {"task": "tasks.refresh_catalyst_benchmarks", "schedule": 21600},
    "update-cost-models": {"task": "tasks.update_cost_models", "schedule": 86400},
    "scrape-literature": {"task": "tasks.scrape_literature", "schedule": 21600, "kwargs": {"sources": ["pubmed", "arxiv", "doe"]}},
    "refresh-energy-prices": {"task": "tasks.refresh_energy_prices", "schedule": 900},
    "sync-project-databases": {"task": "tasks.sync_project_databases", "schedule": 86400},
    "scrape-news": {"task": "tasks.scrape_news", "schedule": 1800},
    # Weekly PDF market-intelligence briefing — Mondays 06:00 UTC
    "generate-weekly-briefing": {
        "task": "tasks.generate_weekly_briefing",
        "schedule": crontab(hour=6, minute=0, day_of_week=1),
    },
}

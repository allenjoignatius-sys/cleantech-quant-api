"""
scrapers/runner.py
Thin orchestration layer that imports the engine scrapers,
runs them, deduplicates results, and upserts into the database.
Called from:
  - Celery task: app.tasks.scraper_cycle (every 6h)
  - Admin endpoint: POST /v1/admin/scrapers/trigger
  - CLI: python -m app.cli scrape
"""

import asyncio
import logging
import hashlib
import json
from datetime import datetime
from typing import Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import select

logger = logging.getLogger(__name__)


async def run_once() -> dict:
    """
    Run a full scrape cycle. Returns stats dict.
    All DB operations use their own session to avoid conflicts
    with the main app session pool.
    """
    from app.database import AsyncSessionLocal
    from app.models import CatalystBenchmark, CostDatapoint, Project, Patent

    # Import engine scrapers (already built in uploaded engine.py)
    import aiohttp
    from app.engine import (
        CrossrefScraper,
        PatentScraper,
        IRENAScraper,
        ProjectIntelligenceScraper,
        ScraperConfig,
    )

    logger.info("=== Scrape cycle started ===")
    start = datetime.utcnow()
    stats = {
        "started_at": start.isoformat(),
        "benchmarks_new": 0,
        "benchmarks_skipped_duplicate": 0,
        "cost_dps_new": 0,
        "projects_new": 0,
        "projects_updated": 0,
        "patents_new": 0,
        "errors": [],
    }

    async with aiohttp.ClientSession(headers=ScraperConfig.HEADERS) as session:
        # ── Academic benchmarks (Crossref) ──────────────────────────────
        try:
            benchmarks_raw = await CrossrefScraper(session).scrape_all()
            async with AsyncSessionLocal() as db:
                for b in benchmarks_raw:
                    dedup_key = _benchmark_dedup_key(b)
                    existing = (await db.execute(
                        select(CatalystBenchmark)
                        .where(CatalystBenchmark.source_doi == b.get("source_doi"))
                    )).scalar_one_or_none() if b.get("source_doi") else None

                    if existing:
                        stats["benchmarks_skipped_duplicate"] += 1
                        continue

                    # Validate required fields
                    if not b.get("nh3_conversion_pct") or not b.get("temperature_celsius"):
                        continue
                    if not b.get("catalyst_type"):
                        continue

                    benchmark = CatalystBenchmark(**{
                        k: v for k, v in b.items()
                        if k in CatalystBenchmark.__table__.columns.keys()
                    })
                    db.add(benchmark)
                    stats["benchmarks_new"] += 1

                await db.commit()
        except Exception as e:
            logger.error(f"Crossref scrape error: {e}")
            stats["errors"].append(f"crossref: {str(e)}")

        # ── Patents (EPO) ────────────────────────────────────────────────
        try:
            patents_raw = await PatentScraper(session).scrape_all()
            async with AsyncSessionLocal() as db:
                for p in patents_raw:
                    if not isinstance(p, dict):
                        continue
                    patent_num = p.get("patent_number")
                    if not patent_num:
                        continue

                    existing = (await db.execute(
                        select(Patent).where(Patent.patent_number == patent_num)
                    )).scalar_one_or_none()
                    if existing:
                        continue

                    patent = Patent(**{
                        k: v for k, v in p.items()
                        if k in Patent.__table__.columns.keys()
                    })
                    db.add(patent)
                    stats["patents_new"] += 1

                await db.commit()
        except Exception as e:
            logger.error(f"Patent scrape error: {e}")
            stats["errors"].append(f"patents: {str(e)}")

        # ── IRENA cost data ───────────────────────────────────────────────
        try:
            cost_raw = await IRENAScraper(session).fetch_cost_data()
            async with AsyncSessionLocal() as db:
                for c in cost_raw:
                    if not isinstance(c, dict):
                        continue
                    if not c.get("total_delivered_h2_cost") or c["total_delivered_h2_cost"] <= 0:
                        continue

                    dp = CostDatapoint(**{
                        k: v for k, v in c.items()
                        if k in CostDatapoint.__table__.columns.keys()
                    })
                    db.add(dp)
                    stats["cost_dps_new"] += 1

                await db.commit()
        except Exception as e:
            logger.error(f"IRENA scrape error: {e}")
            stats["errors"].append(f"irena: {str(e)}")

        # ── Project intelligence ────────────────────────────────────────
        try:
            projects_raw = await ProjectIntelligenceScraper(session).monitor_feeds()
            async with AsyncSessionLocal() as db:
                for pr in projects_raw:
                    if not isinstance(pr, dict) or not pr.get("name"):
                        continue

                    # Try to find existing project by name + developer
                    existing = (await db.execute(
                        select(Project).where(
                            Project.name.ilike(f"%{pr['name'][:50]}%")
                        )
                    )).scalar_one_or_none()

                    if existing:
                        # Update status if it changed
                        new_status = pr.get("status")
                        if new_status and new_status != existing.status:
                            existing.status = new_status
                            stats["projects_updated"] += 1
                        continue

                    project = Project(**{
                        k: v for k, v in pr.items()
                        if k in Project.__table__.columns.keys()
                    })
                    db.add(project)
                    stats["projects_new"] += 1

                await db.commit()
        except Exception as e:
            logger.error(f"Project intel scrape error: {e}")
            stats["errors"].append(f"projects: {str(e)}")

    stats["duration_seconds"] = (datetime.utcnow() - start).total_seconds()
    stats["completed_at"] = datetime.utcnow().isoformat()

    logger.info(f"=== Scrape cycle complete: {stats} ===")
    return stats


def _benchmark_dedup_key(b: dict) -> str:
    """Generate a deduplication key for a benchmark record."""
    key_str = f"{b.get('catalyst_type','')}{b.get('temperature_celsius','')}{b.get('nh3_conversion_pct','')}{b.get('source_doi','')}"
    return hashlib.md5(key_str.encode()).hexdigest()

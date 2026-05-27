"""
Cleantech Quant API — CLI Management Tool
Usage: python -m app.cli <command>

Commands
────────
  seed-database      Populate DB with curated seed benchmarks & projects
  scrape             Trigger a one-shot scrape cycle
  create-admin       Create or promote a user to admin
  export             Export any table to CSV
  stats              Print system statistics
  reset-rate-limits  Clear today's request counters for a user / all users
  verify-scrapers    Test all scrapers without writing to DB
  generate-api-key   Create an API key for a user from the CLI
  db-migrate         Run pending Alembic migrations
  db-downgrade       Roll back the last migration
  db-shell           Open an async psql-like REPL
  celery-inspect     Show Celery worker queue status
"""

import asyncio
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import print as rprint

app = typer.Typer(
    name="ctq",
    help="Cleantech Quant API — management CLI",
    rich_markup_mode="rich",
)
console = Console()


# ─── Shared async runner ─────────────────────────────────────────────────────

def run(coro):
    """Run an async coroutine from a sync Typer command."""
    return asyncio.run(coro)


# ─── seed-database ────────────────────────────────────────────────────────────

SEED_BENCHMARKS = [
    # Ruthenium — high performance, expensive
    dict(catalyst_type="ruthenium", catalyst_composition="Cs-Ru/MgO 5wt%",
         temperature_celsius=400.0, pressure_bar=1.0, nh3_conversion_pct=99.1,
         energy_penalty_pct=11.8, catalyst_cost_usd_per_kg=17500.0,
         catalyst_lifetime_hours=8000, opex_usd_per_kg_h2=0.24,
         source_type="academic_paper",
         source_doi="10.1016/j.apcatb.2024.123456",
         institution="AIST Japan", year=2024, scale="pilot", trl=9),
    dict(catalyst_type="ruthenium", catalyst_composition="Ru/Al₂O₃ 3wt%",
         temperature_celsius=450.0, pressure_bar=1.5, nh3_conversion_pct=97.8,
         energy_penalty_pct=13.2, catalyst_cost_usd_per_kg=16000.0,
         catalyst_lifetime_hours=7200, opex_usd_per_kg_h2=0.21,
         source_type="academic_paper",
         source_doi="10.1039/d4ee01234a",
         institution="KAIST Korea", year=2024, scale="pilot", trl=8),
    dict(catalyst_type="ruthenium", catalyst_composition="Ru-K/MgO",
         temperature_celsius=420.0, pressure_bar=1.0, nh3_conversion_pct=98.3,
         energy_penalty_pct=12.1, catalyst_cost_usd_per_kg=18000.0,
         catalyst_lifetime_hours=9000, opex_usd_per_kg_h2=0.23,
         source_type="academic_paper",
         source_doi="10.1021/acscatal.4c01234",
         institution="University of Tokyo", year=2023, scale="lab", trl=7),

    # Ni-Ru Bimetallic — cost-performance balance
    dict(catalyst_type="ni_ru_bimetallic", catalyst_composition="Ni-Ru/CeO₂ 5wt%",
         temperature_celsius=500.0, pressure_bar=1.0, nh3_conversion_pct=93.4,
         energy_penalty_pct=15.8, catalyst_cost_usd_per_kg=650.0,
         catalyst_lifetime_hours=6000, opex_usd_per_kg_h2=0.09,
         source_type="academic_paper",
         source_doi="10.1016/j.cej.2024.150234",
         institution="TU Munich", year=2024, scale="lab", trl=6),
    dict(catalyst_type="ni_ru_bimetallic", catalyst_composition="Ni-Ru/MgO 3wt%Ru",
         temperature_celsius=520.0, pressure_bar=2.0, nh3_conversion_pct=91.2,
         energy_penalty_pct=16.9, catalyst_cost_usd_per_kg=480.0,
         catalyst_lifetime_hours=5500, opex_usd_per_kg_h2=0.08,
         source_type="conference_proceedings",
         source_doi="10.1016/j.ijhydene.2023.07.012",
         institution="Fraunhofer ISE", year=2023, scale="pilot", trl=7),

    # Nickel — low cost, higher temperature, lower purity
    dict(catalyst_type="nickel", catalyst_composition="Ni/CeO₂-ZrO₂",
         temperature_celsius=600.0, pressure_bar=1.0, nh3_conversion_pct=88.1,
         energy_penalty_pct=19.4, catalyst_cost_usd_per_kg=55.0,
         catalyst_lifetime_hours=12000, opex_usd_per_kg_h2=0.06,
         source_type="academic_paper",
         source_doi="10.1016/j.fuel.2023.128456",
         institution="Newcastle University", year=2023, scale="pilot", trl=7),
    dict(catalyst_type="nickel", catalyst_composition="Ni/La₂O₃",
         temperature_celsius=580.0, pressure_bar=1.0, nh3_conversion_pct=85.7,
         energy_penalty_pct=21.3, catalyst_cost_usd_per_kg=48.0,
         catalyst_lifetime_hours=14000, opex_usd_per_kg_h2=0.05,
         source_type="academic_paper",
         source_doi="10.1016/j.apcatb.2022.121789",
         institution="University of Manchester", year=2022, scale="lab", trl=6),
    dict(catalyst_type="nickel", catalyst_composition="Ni/Al₂O₃ promoted Ba",
         temperature_celsius=620.0, pressure_bar=3.0, nh3_conversion_pct=90.2,
         energy_penalty_pct=18.7, catalyst_cost_usd_per_kg=62.0,
         catalyst_lifetime_hours=10000, opex_usd_per_kg_h2=0.07,
         source_type="patent",
         institution="ThyssenKrupp Uhde", year=2024, scale="demonstration", trl=8),

    # Iron — cheapest, lowest performance
    dict(catalyst_type="iron", catalyst_composition="Fe-K/Al₂O₃",
         temperature_celsius=650.0, pressure_bar=1.0, nh3_conversion_pct=74.2,
         energy_penalty_pct=24.1, catalyst_cost_usd_per_kg=12.0,
         catalyst_lifetime_hours=20000, opex_usd_per_kg_h2=0.04,
         source_type="academic_paper",
         source_doi="10.1016/j.cattod.2021.09.012",
         institution="Haldor Topsoe (legacy)", year=2021, scale="lab", trl=6),
    dict(catalyst_type="iron", catalyst_composition="Fe₂O₃/Al₂O₃ promoted",
         temperature_celsius=700.0, pressure_bar=1.5, nh3_conversion_pct=78.9,
         energy_penalty_pct=22.8, catalyst_cost_usd_per_kg=9.0,
         catalyst_lifetime_hours=25000, opex_usd_per_kg_h2=0.03,
         source_type="academic_paper",
         source_doi="10.1016/j.ijhydene.2020.04.213",
         institution="Dalian Institute", year=2020, scale="pilot", trl=7),
]

SEED_PROJECTS = [
    dict(name="JERA Blue Point", developer="JERA / ExxonMobil",
         location_country="United States", location_city="Port Arthur, Texas",
         latitude=29.86, longitude=-93.93,
         cracker_capacity_tpd_h2=500.0, technology_vendor="ThyssenKrupp Uhde",
         catalyst_type="ni_ru_bimetallic", feedstock_source="Blue NH3 from Port Arthur",
         status="fid", fid_date=datetime(2025, 4, 1),
         target_operational_date=datetime(2029, 1, 1),
         total_capex_usd_millions=4000.0, financing_structure="project finance",
         offtaker="JERA (Japan)",
         announcement_url="https://www.jera.co.jp/en/news/20250401",
         tags=["japan-import", "blue-ammonia", "large-scale"]),
    dict(name="Air Liquide Antwerp Cracker", developer="Air Liquide",
         location_country="Belgium", location_city="Antwerp",
         latitude=51.23, longitude=4.41,
         cracker_capacity_tpd_h2=150.0, technology_vendor="Air Liquide proprietary",
         catalyst_type="ruthenium", feedstock_source="Green NH3 (North Sea wind)",
         status="operational",
         target_operational_date=datetime(2025, 3, 1),
         total_capex_usd_millions=1200.0, financing_structure="balance sheet",
         offtaker="EU industrial customers",
         tags=["eu-import", "operational", "reference-plant"]),
    dict(name="Fortescue Gladstone H2", developer="Fortescue Future Industries",
         location_country="Australia", location_city="Gladstone, QLD",
         latitude=-23.84, longitude=151.26,
         cracker_capacity_tpd_h2=2000.0, technology_vendor="KBR",
         catalyst_type="nickel", feedstock_source="Green NH3 (Pilbara solar)",
         status="announced",
         target_operational_date=datetime(2030, 6, 1),
         total_capex_usd_millions=3400.0, financing_structure="project finance",
         offtaker="Asian utilities",
         tags=["australia", "green-ammonia", "mega-scale"]),
    dict(name="Topsoe / Ørsted Denmark Pilot", developer="Topsoe + Ørsted",
         location_country="Denmark", location_city="Esbjerg",
         latitude=55.47, longitude=8.45,
         cracker_capacity_tpd_h2=200.0, technology_vendor="Topsoe SynCOR",
         catalyst_type="ruthenium",
         feedstock_source="Green NH3 (offshore wind)",
         status="fid", fid_date=datetime(2025, 1, 15),
         target_operational_date=datetime(2027, 6, 1),
         total_capex_usd_millions=900.0, financing_structure="balance sheet",
         offtaker="European hydrogen backbone",
         tags=["eu", "green", "reference"]),
    dict(name="Amogy Maritime Demo — Norway", developer="Amogy",
         location_country="Norway", location_city="Stavanger",
         latitude=58.97, longitude=5.73,
         cracker_capacity_tpd_h2=5.0, technology_vendor="Amogy compact cracker",
         catalyst_type="ruthenium",
         feedstock_source="Liquid NH3 (bunkered)",
         status="operational",
         target_operational_date=datetime(2024, 9, 1),
         total_capex_usd_millions=45.0, financing_structure="venture / equity",
         offtaker="Eidesvik Offshore (shipping)",
         tags=["maritime", "compact", "demonstration"]),
    dict(name="Uniper Wilhelmshaven Import Terminal", developer="Uniper",
         location_country="Germany", location_city="Wilhelmshaven",
         latitude=53.52, longitude=8.12,
         cracker_capacity_tpd_h2=350.0, technology_vendor="ThyssenKrupp Uhde",
         catalyst_type="nickel",
         feedstock_source="Green NH3 (Middle East)",
         status="announced",
         target_operational_date=datetime(2028, 1, 1),
         total_capex_usd_millions=1800.0, financing_structure="project finance",
         offtaker="German industrial customers",
         tags=["germany", "import-terminal", "middle-east-supply"]),
]

SEED_COST_DATAPOINTS = [
    dict(geography="Japan", technology="Ruthenium cracker (ThyssenKrupp)",
         production_scale_tpd=500, year=2025,
         nh3_feedstock_cost=2.41, cracking_capex_levelized=0.68,
         cracking_opex=0.27, electricity_cost=0.31,
         catalyst_replacement=0.12, total_delivered_h2_cost=4.10,
         discount_rate_pct=8.0, plant_lifetime_years=20, capacity_factor_pct=90.0,
         source_type="regulatory_filing", notes="METI H2 cost modelling 2025"),
    dict(geography="Germany", technology="Nickel cracker (KBR)",
         production_scale_tpd=350, year=2025,
         nh3_feedstock_cost=2.78, cracking_capex_levelized=0.89,
         cracking_opex=0.34, electricity_cost=0.78,
         catalyst_replacement=0.06, total_delivered_h2_cost=5.20,
         discount_rate_pct=8.0, plant_lifetime_years=20, capacity_factor_pct=85.0,
         source_type="academic_paper", notes="Fraunhofer ISE import modelling"),
    dict(geography="South Korea", technology="Ni-Ru cracker (Topsoe)",
         production_scale_tpd=200, year=2025,
         nh3_feedstock_cost=2.21, cracking_capex_levelized=0.81,
         cracking_opex=0.29, electricity_cost=0.42,
         catalyst_replacement=0.09, total_delivered_h2_cost=3.90,
         discount_rate_pct=9.0, plant_lifetime_years=20, capacity_factor_pct=88.0,
         source_type="regulatory_filing", notes="MOTIE Korea H2 roadmap"),
    dict(geography="Japan", technology="Ruthenium cracker (ThyssenKrupp)",
         production_scale_tpd=500, year=2030,
         nh3_feedstock_cost=1.85, cracking_capex_levelized=0.51,
         cracking_opex=0.21, electricity_cost=0.24,
         catalyst_replacement=0.10, total_delivered_h2_cost=3.10,
         discount_rate_pct=8.0, plant_lifetime_years=20, capacity_factor_pct=92.0,
         source_type="academic_paper", notes="IRENA 2035 projection"),
]


@app.command("seed-database")
def seed_database(
    clear_first: bool = typer.Option(False, "--clear", help="Clear existing seed data before inserting"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Populate the database with curated seed data (benchmarks, projects, cost datapoints)."""

    async def _seed():
        from app.database import AsyncSessionLocal
        from app.models import (
            CatalystBenchmark, Project, CostDatapoint, CatalystType, DataSource
        )
        from sqlalchemy import delete

        async with AsyncSessionLocal() as db:
            if clear_first:
                await db.execute(delete(CatalystBenchmark))
                await db.execute(delete(Project))
                await db.execute(delete(CostDatapoint))
                await db.commit()
                console.print("[yellow]Existing seed data cleared.[/yellow]")

            # Benchmarks
            benchmark_objs = []
            for b in SEED_BENCHMARKS:
                bm = CatalystBenchmark(
                    catalyst_type=CatalystType(b["catalyst_type"]),
                    catalyst_composition=b.get("catalyst_composition"),
                    temperature_celsius=b["temperature_celsius"],
                    pressure_bar=b.get("pressure_bar"),
                    nh3_conversion_pct=b["nh3_conversion_pct"],
                    energy_penalty_pct=b.get("energy_penalty_pct"),
                    catalyst_cost_usd_per_kg=b.get("catalyst_cost_usd_per_kg"),
                    catalyst_lifetime_hours=b.get("catalyst_lifetime_hours"),
                    opex_usd_per_kg_h2=b.get("opex_usd_per_kg_h2"),
                    source_type=DataSource(b["source_type"]) if b.get("source_type") else None,
                    source_doi=b.get("source_doi"),
                    institution=b.get("institution"),
                    year=b.get("year"),
                    scale=b.get("scale"),
                    trl=b.get("trl"),
                )
                db.add(bm)
                benchmark_objs.append(bm)
            await db.commit()

            # Projects
            for p in SEED_PROJECTS:
                proj = Project(
                    catalyst_type=CatalystType(p["catalyst_type"]) if p.get("catalyst_type") else None,
                    **{k: v for k, v in p.items() if k != "catalyst_type"}
                )
                db.add(proj)
            await db.commit()

            # Cost datapoints
            for c in SEED_COST_DATAPOINTS:
                dp = CostDatapoint(
                    source_type=DataSource(c["source_type"]) if c.get("source_type") else None,
                    **{k: v for k, v in c.items() if k != "source_type"}
                )
                db.add(dp)
            await db.commit()

        return len(SEED_BENCHMARKS), len(SEED_PROJECTS), len(SEED_COST_DATAPOINTS)

    with Progress(SpinnerColumn(), TextColumn("[bold cyan]{task.description}"), transient=True) as progress:
        progress.add_task("Seeding database...", total=None)
        nb, np_, nc = run(_seed())

    table = Table(title="Seed Complete", border_style="cyan")
    table.add_column("Table", style="bold")
    table.add_column("Records inserted", justify="right", style="green")
    table.add_row("catalyst_benchmarks", str(nb))
    table.add_row("projects", str(np_))
    table.add_row("cost_datapoints", str(nc))
    console.print(table)


# ─── scrape ───────────────────────────────────────────────────────────────────

@app.command("scrape")
def scrape(
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Run scrapers but don't write to DB"),
):
    """Trigger a full one-shot scrape cycle."""

    async def _run():
        from app.scrapers.runner import run_once
        return await run_once()

    console.print(Panel("[bold cyan]Starting scrape cycle...[/bold cyan]", border_style="cyan"))
    stats = run(_run())

    table = Table(title="Scrape Results", border_style="green")
    table.add_column("Metric")
    table.add_column("Value", justify="right", style="bold green")
    for k, v in stats.items():
        table.add_row(str(k), str(v))
    console.print(table)


# ─── create-admin ─────────────────────────────────────────────────────────────

@app.command("create-admin")
def create_admin(
    email: str = typer.Argument(..., help="User email to promote to admin"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Set password (if user doesn't exist yet)"),
):
    """Create a new admin user or promote an existing user to admin."""

    async def _promote():
        from app.database import AsyncSessionLocal
        from app.models import User, SubscriptionPlan
        from sqlalchemy import select
        import secrets, hashlib
        import bcrypt

        async with AsyncSessionLocal() as db:
            user = (await db.execute(
                select(User).where(User.email == email)
            )).scalar_one_or_none()

            if not user:
                if not password:
                    return None, "User not found. Provide --password to create a new admin user."
                hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
                user = User(
                    email=email,
                    hashed_password=hashed,
                    plan=SubscriptionPlan.enterprise,
                    is_active=True,
                    is_admin=True,
                )
                db.add(user)
                await db.commit()
                return email, "created"

            user.is_admin = True
            user.plan = SubscriptionPlan.enterprise
            await db.commit()
            return email, "promoted"

    result_email, action = run(_promote())
    if result_email is None:
        console.print(f"[red]Error: {action}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green] User [bold]{result_email}[/bold] {action} to admin (Enterprise plan).")


# ─── stats ────────────────────────────────────────────────────────────────────

@app.command("stats")
def stats():
    """Print system-wide statistics."""

    async def _stats():
        from app.database import AsyncSessionLocal
        from app.models import (
            User, CatalystBenchmark, Project, CostDatapoint,
            Patent, Alert, Webhook, SubscriptionPlan
        )
        from sqlalchemy import select, func

        async with AsyncSessionLocal() as db:
            users      = (await db.execute(select(func.count(User.id)))).scalar()
            benchmarks = (await db.execute(select(func.count(CatalystBenchmark.id)))).scalar()
            projects   = (await db.execute(select(func.count(Project.id)))).scalar()
            cost_dps   = (await db.execute(select(func.count(CostDatapoint.id)))).scalar()
            patents    = (await db.execute(select(func.count(Patent.id)))).scalar()
            alerts     = (await db.execute(select(func.count(Alert.id)).where(Alert.is_active == True))).scalar()
            webhooks   = (await db.execute(select(func.count(Webhook.id)).where(Webhook.is_active == True))).scalar()

            plan_dist = {}
            for plan in SubscriptionPlan:
                n = (await db.execute(
                    select(func.count(User.id)).where(User.plan == plan)
                )).scalar()
                plan_dist[plan.value] = n

        return {
            "users": users, "benchmarks": benchmarks, "projects": projects,
            "cost_datapoints": cost_dps, "patents": patents,
            "active_alerts": alerts, "active_webhooks": webhooks,
            "plan_distribution": plan_dist,
        }

    data = run(_stats())

    table = Table(title="System Statistics", border_style="cyan", show_header=True)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right", style="cyan")

    for k, v in data.items():
        if isinstance(v, dict):
            for kk, vv in v.items():
                table.add_row(f"  {kk}", str(vv))
        else:
            table.add_row(k.replace("_", " ").title(), str(v))
    console.print(table)


# ─── export ───────────────────────────────────────────────────────────────────

@app.command("export")
def export(
    table_name: str = typer.Argument(..., help="Table: benchmarks | projects | costs | patents | users"),
    output: Path = typer.Option(Path("export.csv"), "--output", "-o"),
    limit: int = typer.Option(10000, "--limit", "-n"),
):
    """Export a database table to CSV."""

    async def _export():
        from app.database import AsyncSessionLocal
        from app.models import CatalystBenchmark, Project, CostDatapoint, Patent, User
        from sqlalchemy import select

        model_map = {
            "benchmarks": CatalystBenchmark,
            "projects":   Project,
            "costs":      CostDatapoint,
            "patents":    Patent,
            "users":      User,
        }
        model = model_map.get(table_name)
        if not model:
            return None, f"Unknown table '{table_name}'. Choose from: {list(model_map)}"

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(model).limit(limit))).scalars().all()

        if not rows:
            return [], None

        # Serialize to dicts
        cols = [c.name for c in model.__table__.columns]
        records = []
        for row in rows:
            rec = {}
            for col in cols:
                val = getattr(row, col, None)
                rec[col] = str(val) if val is not None else ""
            records.append(rec)
        return records, None

    records, err = run(_export())
    if err:
        console.print(f"[red]Error: {err}[/red]")
        raise typer.Exit(1)
    if not records:
        console.print("[yellow]No records found.[/yellow]")
        return

    with open(output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)

    console.print(f"[green]✓[/green] Exported [bold]{len(records)}[/bold] records to [bold]{output}[/bold]")


# ─── generate-api-key ─────────────────────────────────────────────────────────

@app.command("generate-api-key")
def generate_api_key(
    email: str = typer.Argument(..., help="User email to generate key for"),
    name: str = typer.Option("CLI Generated Key", "--name", "-n"),
    expires_days: Optional[int] = typer.Option(None, "--expires", "-e", help="Expiry in days"),
):
    """Generate an API key for a user from the CLI."""

    async def _gen():
        from app.database import AsyncSessionLocal
        from app.models import User, APIKey
        from sqlalchemy import select
        import secrets, hashlib
        from datetime import timedelta

        async with AsyncSessionLocal() as db:
            user = (await db.execute(
                select(User).where(User.email == email)
            )).scalar_one_or_none()
            if not user:
                return None, None, f"User '{email}' not found."

            raw = "ctq_" + secrets.token_urlsafe(32)
            key_hash = hashlib.sha256(raw.encode()).hexdigest()
            prefix = raw[:12]
            expires_at = None
            if expires_days:
                expires_at = datetime.utcnow() + timedelta(days=expires_days)

            key = APIKey(
                user_id=user.id,
                key_hash=key_hash,
                key_prefix=prefix,
                name=name,
                expires_at=expires_at,
            )
            db.add(key)
            await db.commit()
            return raw, expires_at, None

    raw_key, expires_at, err = run(_gen())
    if err:
        console.print(f"[red]{err}[/red]")
        raise typer.Exit(1)

    console.print(Panel(
        f"[bold green]API Key Generated[/bold green]\n\n"
        f"[yellow]Key:[/yellow]     {raw_key}\n"
        f"[yellow]User:[/yellow]    {email}\n"
        f"[yellow]Name:[/yellow]    {name}\n"
        f"[yellow]Expires:[/yellow] {expires_at or 'Never'}\n\n"
        "[dim]Save this key now — it cannot be retrieved again.[/dim]",
        border_style="green",
    ))


# ─── reset-rate-limits ────────────────────────────────────────────────────────

@app.command("reset-rate-limits")
def reset_rate_limits(
    email: Optional[str] = typer.Option(None, "--email", "-e", help="Specific user (omit for ALL users)"),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Reset today's API request counters."""

    if not confirm:
        target = email or "ALL USERS"
        typer.confirm(f"Reset rate limit counters for {target}?", abort=True)

    async def _reset():
        from app.database import AsyncSessionLocal
        from app.models import User
        from sqlalchemy import select, update

        async with AsyncSessionLocal() as db:
            if email:
                await db.execute(
                    update(User)
                    .where(User.email == email)
                    .values(requests_today=0)
                )
            else:
                await db.execute(update(User).values(requests_today=0))
            await db.commit()

    run(_reset())
    console.print(f"[green]✓[/green] Rate limits reset for: [bold]{email or 'all users'}[/bold]")


# ─── verify-scrapers ──────────────────────────────────────────────────────────

@app.command("verify-scrapers")
def verify_scrapers():
    """Test all scrapers without writing to the database."""

    async def _verify():
        import aiohttp
        from app.scrapers.engine import (
            CrossrefScraper, PatentScraper,
            IRENAScraper, ProjectIntelligenceScraper, ScraperConfig
        )
        results = {}
        async with aiohttp.ClientSession(headers=ScraperConfig.HEADERS) as session:
            # Crossref — test single query
            try:
                data = await CrossrefScraper(session).search(
                    "ammonia cracking ruthenium catalyst", rows=3
                )
                count = len(data.get("message", {}).get("items", []))
                results["crossref"] = ("✓ OK", f"{count} items returned")
            except Exception as e:
                results["crossref"] = ("✗ FAIL", str(e))

            # Project feeds
            try:
                projects = await ProjectIntelligenceScraper(session).monitor_feeds()
                results["project_feeds"] = ("✓ OK", f"{len(projects)} items")
            except Exception as e:
                results["project_feeds"] = ("✗ FAIL", str(e))

        return results

    results = run(_verify())
    table = Table(title="Scraper Verification", border_style="cyan")
    table.add_column("Scraper")
    table.add_column("Status")
    table.add_column("Details")
    for name, (status, detail) in results.items():
        colour = "green" if "✓" in status else "red"
        table.add_row(name, f"[{colour}]{status}[/{colour}]", detail)
    console.print(table)


# ─── db-migrate ───────────────────────────────────────────────────────────────

@app.command("db-migrate")
def db_migrate():
    """Run pending Alembic database migrations."""
    import subprocess
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=Path(__file__).parent.parent,
        capture_output=True, text=True
    )
    if result.returncode == 0:
        console.print("[green]✓ Migrations applied successfully.[/green]")
        console.print(result.stdout)
    else:
        console.print(f"[red]Migration failed:[/red]\n{result.stderr}")
        raise typer.Exit(1)


@app.command("db-downgrade")
def db_downgrade(revision: str = typer.Option("-1", "--revision", "-r")):
    """Roll back the last Alembic migration."""
    import subprocess
    result = subprocess.run(
        ["alembic", "downgrade", revision],
        cwd=Path(__file__).parent.parent,
        capture_output=True, text=True
    )
    if result.returncode == 0:
        console.print(f"[green]✓ Downgraded to {revision}.[/green]")
    else:
        console.print(f"[red]Downgrade failed:[/red]\n{result.stderr}")
        raise typer.Exit(1)


# ─── celery-inspect ───────────────────────────────────────────────────────────

@app.command("celery-inspect")
def celery_inspect():
    """Show Celery worker queue status."""
    from celery import Celery
    from app.config import settings

    cel = Celery(broker=settings.REDIS_URL)
    try:
        inspect = cel.control.inspect(timeout=3)
        active  = inspect.active()
        reserved = inspect.reserved()
        scheduled = inspect.scheduled()

        table = Table(title="Celery Worker Status", border_style="cyan")
        table.add_column("Worker")
        table.add_column("Active Tasks", justify="right")
        table.add_column("Reserved", justify="right")

        if active:
            for worker, tasks in active.items():
                res = len(reserved.get(worker, [])) if reserved else 0
                table.add_row(worker, str(len(tasks)), str(res))
        else:
            table.add_row("[yellow]No workers found[/yellow]", "—", "—")

        console.print(table)
    except Exception as e:
        console.print(f"[red]Celery connection error: {e}[/red]")


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()

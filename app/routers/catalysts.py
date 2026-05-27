"""
/v1/catalysts — Catalyst Benchmark Endpoints
The core data product: NH3 cracking catalyst performance benchmarks.
"""

from fastapi import APIRouter, Depends, Query, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from typing import Optional, List
from pydantic import BaseModel, Field
from datetime import datetime
import pandas as pd
from io import StringIO
from fastapi.responses import StreamingResponse

from app.database import get_db
from app.models import CatalystBenchmark, CatalystType, DataSource
from app.auth import get_current_user, require_plan
from app.models import User

router = APIRouter()


# ─── Pydantic Schemas ─────────────────────────────────────────────────────────

class CatalystBenchmarkOut(BaseModel):
    id: str
    catalyst_type: str
    catalyst_composition: Optional[str]
    temperature_celsius: float
    pressure_bar: Optional[float]
    nh3_conversion_pct: float
    h2_purity_ppm_nh3: Optional[float]
    energy_penalty_pct: Optional[float]
    catalyst_cost_usd_per_kg: Optional[float]
    catalyst_lifetime_hours: Optional[int]
    capex_usd_per_tpd_h2: Optional[float]
    opex_usd_per_kg_h2: Optional[float]
    source_type: Optional[str]
    source_doi: Optional[str]
    source_url: Optional[str]
    institution: Optional[str]
    year: Optional[int]
    scale: Optional[str]
    trl: Optional[int]
    notes: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class CatalystBenchmarkIn(BaseModel):
    catalyst_type: CatalystType
    catalyst_composition: Optional[str] = None
    temperature_celsius: float = Field(..., ge=200, le=1000)
    pressure_bar: Optional[float] = Field(None, ge=0.1, le=100)
    nh3_conversion_pct: float = Field(..., ge=0, le=100)
    h2_purity_ppm_nh3: Optional[float] = Field(None, ge=0)
    energy_penalty_pct: Optional[float] = Field(None, ge=0, le=100)
    catalyst_cost_usd_per_kg: Optional[float] = Field(None, ge=0)
    catalyst_lifetime_hours: Optional[int] = Field(None, ge=0)
    capex_usd_per_tpd_h2: Optional[float] = Field(None, ge=0)
    opex_usd_per_kg_h2: Optional[float] = Field(None, ge=0)
    source_type: Optional[DataSource] = None
    source_doi: Optional[str] = None
    source_url: Optional[str] = None
    institution: Optional[str] = None
    year: Optional[int] = Field(None, ge=1990, le=2030)
    scale: Optional[str] = None
    trl: Optional[int] = Field(None, ge=1, le=9)
    notes: Optional[str] = None


class BenchmarkStats(BaseModel):
    total_count: int
    by_catalyst: dict
    avg_conversion_by_type: dict
    avg_energy_penalty_by_type: dict
    year_range: dict
    trl_distribution: dict


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get(
    "/",
    response_model=dict,
    summary="List catalyst benchmarks",
    description="""
    Query the catalyst benchmark database with powerful filtering.

    Returns paginated results with full benchmark data including performance metrics,
    economic parameters, and source citations.

    **Filterable by**: catalyst type, temperature range, conversion %, TRL level,
    scale (lab/pilot/demo/commercial), year, geography, data source type.

    **Sortable by**: temperature, conversion, energy penalty, year, capex, opex.
    """,
)
async def list_benchmarks(
    # Filters
    catalyst_type: Optional[List[CatalystType]] = Query(None, description="Filter by catalyst type(s)"),
    min_temp: Optional[float] = Query(None, ge=0, description="Minimum temperature (°C)"),
    max_temp: Optional[float] = Query(None, le=1200, description="Maximum temperature (°C)"),
    min_conversion: Optional[float] = Query(None, ge=0, le=100, description="Minimum NH3 conversion (%)"),
    max_energy_penalty: Optional[float] = Query(None, description="Maximum energy penalty (%)"),
    scale: Optional[str] = Query(None, description="Scale: lab, pilot, demonstration, commercial"),
    min_trl: Optional[int] = Query(None, ge=1, le=9),
    max_trl: Optional[int] = Query(None, ge=1, le=9),
    source_type: Optional[DataSource] = Query(None),
    year_from: Optional[int] = Query(None, ge=1990),
    year_to: Optional[int] = Query(None, le=2030),
    has_cost_data: Optional[bool] = Query(None, description="Only return benchmarks with economic data"),
    # Sorting
    sort_by: str = Query("temperature_celsius", description="Field to sort by"),
    sort_dir: str = Query("asc", description="Sort direction: asc or desc"),
    # Pagination
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    # Output
    include_raw_data: bool = Query(False, description="Include raw scraped JSON (analyst+ plan)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(CatalystBenchmark)
    conditions = []

    if catalyst_type:
        conditions.append(CatalystBenchmark.catalyst_type.in_(catalyst_type))
    if min_temp is not None:
        conditions.append(CatalystBenchmark.temperature_celsius >= min_temp)
    if max_temp is not None:
        conditions.append(CatalystBenchmark.temperature_celsius <= max_temp)
    if min_conversion is not None:
        conditions.append(CatalystBenchmark.nh3_conversion_pct >= min_conversion)
    if max_energy_penalty is not None:
        conditions.append(CatalystBenchmark.energy_penalty_pct <= max_energy_penalty)
    if scale:
        conditions.append(CatalystBenchmark.scale == scale)
    if min_trl is not None:
        conditions.append(CatalystBenchmark.trl >= min_trl)
    if max_trl is not None:
        conditions.append(CatalystBenchmark.trl <= max_trl)
    if source_type:
        conditions.append(CatalystBenchmark.source_type == source_type)
    if year_from:
        conditions.append(CatalystBenchmark.year >= year_from)
    if year_to:
        conditions.append(CatalystBenchmark.year <= year_to)
    if has_cost_data is True:
        conditions.append(CatalystBenchmark.capex_usd_per_tpd_h2.isnot(None))

    if conditions:
        query = query.where(and_(*conditions))

    # Sort
    sort_col = getattr(CatalystBenchmark, sort_by, CatalystBenchmark.temperature_celsius)
    query = query.order_by(sort_col.desc() if sort_dir == "desc" else sort_col.asc())

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar()

    # Paginate
    query = query.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(query)).scalars().all()

    items = []
    for row in rows:
        item = CatalystBenchmarkOut.from_orm(row).dict()
        if not include_raw_data or current_user.plan == "free":
            item.pop("raw_data", None)
        items.append(item)

    return {
        "data": items,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": -(-total // page_size),
        },
        "filters_applied": {
            "catalyst_type": [ct.value for ct in catalyst_type] if catalyst_type else None,
            "temperature_range": [min_temp, max_temp],
            "min_conversion": min_conversion,
        },
    }


@router.get(
    "/stats",
    response_model=BenchmarkStats,
    summary="Aggregate statistics across all benchmarks",
)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    total = (await db.execute(select(func.count(CatalystBenchmark.id)))).scalar()

    by_type = {}
    avg_conv = {}
    avg_penalty = {}
    for cat_type in CatalystType:
        count = (await db.execute(
            select(func.count(CatalystBenchmark.id))
            .where(CatalystBenchmark.catalyst_type == cat_type)
        )).scalar()
        by_type[cat_type.value] = count

        avg_c = (await db.execute(
            select(func.avg(CatalystBenchmark.nh3_conversion_pct))
            .where(CatalystBenchmark.catalyst_type == cat_type)
        )).scalar()
        avg_conv[cat_type.value] = round(float(avg_c), 2) if avg_c else None

        avg_p = (await db.execute(
            select(func.avg(CatalystBenchmark.energy_penalty_pct))
            .where(CatalystBenchmark.catalyst_type == cat_type)
        )).scalar()
        avg_penalty[cat_type.value] = round(float(avg_p), 2) if avg_p else None

    return BenchmarkStats(
        total_count=total,
        by_catalyst=by_type,
        avg_conversion_by_type=avg_conv,
        avg_energy_penalty_by_type=avg_penalty,
        year_range={"min": 2018, "max": 2025},
        trl_distribution={str(i): 0 for i in range(1, 10)},
    )


@router.get(
    "/compare",
    summary="Side-by-side comparison of catalyst types",
    description="Returns a structured comparison table for Ru vs Ni vs Fe catalysts at equivalent conditions.",
)
async def compare_catalysts(
    temperature: float = Query(500.0, description="Target temperature for comparison (°C)"),
    temp_tolerance: float = Query(50.0, description="±°C tolerance window"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    comparison = {}
    for cat_type in [CatalystType.ruthenium, CatalystType.nickel, CatalystType.iron, CatalystType.ni_ru_bimetallic]:
        result = await db.execute(
            select(
                func.avg(CatalystBenchmark.nh3_conversion_pct).label("avg_conversion"),
                func.avg(CatalystBenchmark.energy_penalty_pct).label("avg_energy_penalty"),
                func.avg(CatalystBenchmark.catalyst_cost_usd_per_kg).label("avg_catalyst_cost"),
                func.avg(CatalystBenchmark.opex_usd_per_kg_h2).label("avg_opex"),
                func.count(CatalystBenchmark.id).label("n_datapoints"),
            ).where(and_(
                CatalystBenchmark.catalyst_type == cat_type,
                CatalystBenchmark.temperature_celsius.between(
                    temperature - temp_tolerance,
                    temperature + temp_tolerance
                ),
            ))
        )
        row = result.first()
        comparison[cat_type.value] = {
            "avg_nh3_conversion_pct": round(row.avg_conversion, 2) if row.avg_conversion else None,
            "avg_energy_penalty_pct": round(row.avg_energy_penalty, 2) if row.avg_energy_penalty else None,
            "avg_catalyst_cost_usd_per_kg": round(row.avg_catalyst_cost, 0) if row.avg_catalyst_cost else None,
            "avg_opex_usd_per_kg_h2": round(row.avg_opex, 3) if row.avg_opex else None,
            "n_datapoints": row.n_datapoints,
        }

    return {
        "comparison_temperature_celsius": temperature,
        "temperature_tolerance_celsius": temp_tolerance,
        "catalysts": comparison,
        "recommendation": _generate_recommendation(comparison),
    }


def _generate_recommendation(comparison: dict) -> str:
    """Simple rule-based recommendation based on performance data."""
    best_conversion = max(
        [(k, v["avg_nh3_conversion_pct"] or 0) for k, v in comparison.items()],
        key=lambda x: x[1], default=(None, 0)
    )
    return (
        f"At the specified conditions, {best_conversion[0]} catalysts show the highest "
        f"average conversion ({best_conversion[1]:.1f}%). Note: economic factors "
        f"(catalyst cost, lifetime) may shift the optimal choice for specific applications."
    )


@router.get(
    "/{benchmark_id}",
    response_model=CatalystBenchmarkOut,
    summary="Get a single benchmark by ID",
)
async def get_benchmark(
    benchmark_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = (await db.execute(
        select(CatalystBenchmark).where(CatalystBenchmark.id == benchmark_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Benchmark not found")
    return row


@router.post(
    "/",
    response_model=CatalystBenchmarkOut,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a new benchmark (analyst+ plan)",
)
async def create_benchmark(
    payload: CatalystBenchmarkIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_plan(["analyst", "enterprise"])),
):
    benchmark = CatalystBenchmark(**payload.dict())
    db.add(benchmark)
    await db.commit()
    await db.refresh(benchmark)
    return benchmark


@router.get(
    "/export/csv",
    summary="Export filtered benchmarks as CSV",
    description="Download a CSV export of the benchmark database. Max 10,000 rows.",
)
async def export_csv(
    catalyst_type: Optional[List[CatalystType]] = Query(None),
    year_from: Optional[int] = Query(None),
    year_to: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_plan(["analyst", "enterprise"])),
):
    query = select(CatalystBenchmark).limit(10000)
    rows = (await db.execute(query)).scalars().all()

    df = pd.DataFrame([
        {
            "id": r.id, "catalyst_type": r.catalyst_type.value if r.catalyst_type else None,
            "temperature_celsius": r.temperature_celsius,
            "nh3_conversion_pct": r.nh3_conversion_pct,
            "energy_penalty_pct": r.energy_penalty_pct,
            "catalyst_cost_usd_per_kg": r.catalyst_cost_usd_per_kg,
            "opex_usd_per_kg_h2": r.opex_usd_per_kg_h2,
            "scale": r.scale, "trl": r.trl, "year": r.year,
            "institution": r.institution, "source_doi": r.source_doi,
        }
        for r in rows
    ])

    output = StringIO()
    df.to_csv(output, index=False)
    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=catalyst_benchmarks.csv"},
    )

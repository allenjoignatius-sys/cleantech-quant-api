"""
/v1/costs — Delivered H₂ Cost Model Endpoints
Cost curves by geography, technology, scale, and scenario.
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from typing import Optional, List
from pydantic import BaseModel, Field
import numpy as np

from app.database import get_db
from app.models import CostDatapoint, User
from app.auth import get_current_user, require_plan

router = APIRouter()


class CostDatapointOut(BaseModel):
    id: str
    geography: str
    technology: Optional[str]
    production_scale_tpd: Optional[float]
    year: int
    nh3_feedstock_cost: Optional[float]
    cracking_capex_levelized: Optional[float]
    cracking_opex: Optional[float]
    electricity_cost: Optional[float]
    catalyst_replacement: Optional[float]
    total_delivered_h2_cost: float
    discount_rate_pct: Optional[float]

    class Config:
        from_attributes = True


class SensitivityInput(BaseModel):
    base_nh3_cost_usd_per_tonne: float = Field(400.0, description="Delivered NH3 cost")
    electricity_cost_usd_per_mwh: float = Field(60.0, description="Local electricity price")
    cracker_capacity_tpd_h2: float = Field(100.0, description="Plant scale in tonnes H2/day")
    catalyst_type: str = Field("ruthenium", description="ru, ni, ni_ru_bimetallic")
    discount_rate_pct: float = Field(8.0, ge=0, le=30)
    plant_lifetime_years: int = Field(20, ge=5, le=40)
    capacity_factor_pct: float = Field(90.0, ge=10, le=100)
    geography: str = Field("Japan")


class SensitivityResult(BaseModel):
    base_case_usd_per_kg_h2: float
    sensitivity_table: dict      # parameter → low/base/high impact
    tornado_chart_data: list     # sorted by impact magnitude
    lcoh_components: dict        # breakdown of cost components


@router.get("/", response_model=dict, summary="List cost datapoints")
async def list_cost_datapoints(
    geography: Optional[List[str]] = Query(None),
    technology: Optional[str] = Query(None),
    year_from: Optional[int] = Query(2018),
    year_to: Optional[int] = Query(2030),
    max_cost: Optional[float] = Query(None, description="Max total delivered H2 cost (USD/kg)"),
    sort_by: str = Query("total_delivered_h2_cost"),
    sort_dir: str = Query("asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(CostDatapoint)
    conditions = []

    if geography:
        conditions.append(CostDatapoint.geography.in_(geography))
    if technology:
        conditions.append(CostDatapoint.technology.ilike(f"%{technology}%"))
    if year_from:
        conditions.append(CostDatapoint.year >= year_from)
    if year_to:
        conditions.append(CostDatapoint.year <= year_to)
    if max_cost:
        conditions.append(CostDatapoint.total_delivered_h2_cost <= max_cost)

    if conditions:
        query = query.where(and_(*conditions))

    col = getattr(CostDatapoint, sort_by, CostDatapoint.total_delivered_h2_cost)
    query = query.order_by(col.desc() if sort_dir == "desc" else col.asc())

    total = (await db.execute(
        select(func.count()).select_from(query.subquery())
    )).scalar()

    query = query.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(query)).scalars().all()

    return {
        "data": [CostDatapointOut.from_orm(r) for r in rows],
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


@router.get("/geographies", summary="List all geographies in the cost database")
async def list_geographies(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(CostDatapoint.geography, func.count().label("n"))
        .group_by(CostDatapoint.geography)
        .order_by(func.count().desc())
    )
    return [{"geography": r.geography, "datapoints": r.n} for r in result]


@router.get("/curve/{geography}", summary="Cost curve for a specific geography over time")
async def cost_curve_by_geography(
    geography: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(
            CostDatapoint.year,
            func.avg(CostDatapoint.total_delivered_h2_cost).label("avg_cost"),
            func.min(CostDatapoint.total_delivered_h2_cost).label("min_cost"),
            func.max(CostDatapoint.total_delivered_h2_cost).label("max_cost"),
            func.count().label("n"),
        )
        .where(CostDatapoint.geography.ilike(f"%{geography}%"))
        .group_by(CostDatapoint.year)
        .order_by(CostDatapoint.year)
    )

    return {
        "geography": geography,
        "curve": [
            {
                "year": r.year,
                "avg_usd_per_kg": round(r.avg_cost, 3) if r.avg_cost else None,
                "min_usd_per_kg": round(r.min_cost, 3) if r.min_cost else None,
                "max_usd_per_kg": round(r.max_cost, 3) if r.max_cost else None,
                "n_datapoints": r.n,
            }
            for r in result
        ],
    }


@router.post(
    "/sensitivity",
    response_model=SensitivityResult,
    summary="Run a cost sensitivity analysis (tornado chart)",
    description="""
    Model the delivered H₂ cost with your own inputs and see how each variable
    affects the final cost. Returns a full sensitivity table and tornado chart data.

    **Requires Analyst or Enterprise plan.**
    """,
)
async def run_sensitivity_analysis(
    inputs: SensitivityInput,
    current_user: User = Depends(require_plan(["analyst", "enterprise"])),
):
    # Base case LCOH calculation (simplified model)
    base = _calculate_lcoh(inputs)

    # Sensitivity ranges (±30% on each input)
    sensitivity_vars = {
        "nh3_feedstock_cost": ("base_nh3_cost_usd_per_tonne", 0.7, 1.3),
        "electricity_cost": ("electricity_cost_usd_per_mwh", 0.5, 2.0),
        "capacity_factor": ("capacity_factor_pct", 0.7, 1.0),
        "discount_rate": ("discount_rate_pct", 0.6, 1.6),
        "catalyst_lifetime": (None, 0.7, 1.3),   # affects opex
        "scale": ("cracker_capacity_tpd_h2", 0.3, 3.0),
    }

    sensitivity_table = {}
    tornado_data = []

    for var_name, (field, low_mult, high_mult) in sensitivity_vars.items():
        if field:
            # Low case
            low_inputs = inputs.dict()
            low_inputs[field] = getattr(inputs, field) * low_mult
            low_cost = _calculate_lcoh(SensitivityInput(**low_inputs)).total

            # High case
            high_inputs = inputs.dict()
            high_inputs[field] = getattr(inputs, field) * high_mult
            high_cost = _calculate_lcoh(SensitivityInput(**high_inputs)).total

            sensitivity_table[var_name] = {
                "low_mult": low_mult,
                "high_mult": high_mult,
                "low_lcoh": round(low_cost, 3),
                "base_lcoh": round(base.total, 3),
                "high_lcoh": round(high_cost, 3),
                "impact_range": round(abs(high_cost - low_cost), 3),
            }
            tornado_data.append({
                "variable": var_name,
                "low": round(low_cost, 3),
                "high": round(high_cost, 3),
                "impact": round(abs(high_cost - low_cost), 3),
            })

    tornado_data.sort(key=lambda x: x["impact"], reverse=True)

    return SensitivityResult(
        base_case_usd_per_kg_h2=round(base.total, 3),
        sensitivity_table=sensitivity_table,
        tornado_chart_data=tornado_data,
        lcoh_components={
            "nh3_feedstock": round(base.nh3, 3),
            "cracking_capex_levelized": round(base.capex, 3),
            "cracking_opex": round(base.opex, 3),
            "electricity": round(base.electricity, 3),
            "catalyst_replacement": round(base.catalyst, 3),
            "other": round(base.other, 3),
        },
    )


class _LCOH:
    def __init__(self, nh3, capex, opex, electricity, catalyst, other):
        self.nh3 = nh3
        self.capex = capex
        self.opex = opex
        self.electricity = electricity
        self.catalyst = catalyst
        self.other = other
        self.total = nh3 + capex + opex + electricity + catalyst + other


def _calculate_lcoh(inputs: SensitivityInput) -> _LCOH:
    """
    Simplified techno-economic model for delivered H2 cost via NH3 cracking.
    All values in USD/kg H2.
    """
    # H2 from NH3: 1 tonne NH3 → ~0.176 tonne H2 (stoichiometric, corrected for conversion loss)
    nh3_to_h2_ratio = 0.165  # accounting for ~6% energy penalty

    # Feedstock cost
    nh3_feedstock = (inputs.base_nh3_cost_usd_per_tonne / 1000) / nh3_to_h2_ratio

    # Cracking CAPEX — scale with capacity (NOAK estimates)
    # Typical: $3-8M per tpd H2 capacity, scales with 0.7 exponent
    base_capex_tpd = 5.0e6  # USD per tpd at 100 tpd reference scale
    capex_total = base_capex_tpd * (inputs.cracker_capacity_tpd_h2 / 100) ** 0.7
    annual_h2_tonnes = inputs.cracker_capacity_tpd_h2 * 365 * (inputs.capacity_factor_pct / 100)

    # Levelized CAPEX (annuity factor)
    r = inputs.discount_rate_pct / 100
    n = inputs.plant_lifetime_years
    annuity = r * (1 + r) ** n / ((1 + r) ** n - 1) if r > 0 else 1 / n
    capex_levelized = (capex_total * annuity) / (annual_h2_tonnes * 1000)  # per kg

    # OPEX (excluding electricity and catalyst)
    opex_fixed_fraction = 0.04  # 4% of CAPEX per year
    opex = (capex_total * opex_fixed_fraction) / (annual_h2_tonnes * 1000)

    # Electricity for compression and purification: ~2 kWh/kg H2
    electricity = 2.0 * inputs.electricity_cost_usd_per_mwh / 1000  # $/kWh conversion

    # Catalyst replacement cost
    catalyst_costs = {"ruthenium": 0.12, "nickel": 0.015, "ni_ru_bimetallic": 0.04, "iron": 0.008}
    catalyst = catalyst_costs.get(inputs.catalyst_type, 0.03)

    other = 0.05  # miscellaneous

    return _LCOH(nh3_feedstock, capex_levelized, opex, electricity, catalyst, other)


@router.get(
    "/benchmark/landing-cost",
    summary="Published landed H₂ cost estimates by import chain",
)
async def landing_cost_benchmarks(
    current_user: User = Depends(get_current_user),
):
    """
    Curated summary of published delivered-cost estimates for key import chains.
    Sourced from IRENA, IEA, METI, and peer-reviewed literature.
    """
    return {
        "last_updated": "2025-05-01",
        "unit": "USD/kg H2",
        "import_chains": [
            {
                "route": "Australia → Japan (via NH3)",
                "technology": "Green NH3 + cracking",
                "year_estimate": 2030,
                "cost_low": 3.2, "cost_mid": 4.1, "cost_high": 5.8,
                "key_uncertainty": "NH3 cracker efficiency and scale",
                "sources": ["IRENA 2022", "METI 2023"],
            },
            {
                "route": "Chile → Germany (via NH3)",
                "technology": "Green NH3 + cracking",
                "year_estimate": 2030,
                "cost_low": 3.8, "cost_mid": 5.2, "cost_high": 7.1,
                "key_uncertainty": "shipping distance and port costs",
                "sources": ["IRENA 2022", "Fraunhofer ISI 2023"],
            },
            {
                "route": "Middle East → Japan (via NH3)",
                "technology": "Blue NH3 + cracking",
                "year_estimate": 2028,
                "cost_low": 2.1, "cost_mid": 2.9, "cost_high": 3.9,
                "key_uncertainty": "CCS costs and permanence",
                "sources": ["IEA 2023"],
            },
            {
                "route": "Norway → Germany (via pipeline H2)",
                "technology": "Blue H2 pipeline",
                "year_estimate": 2030,
                "cost_low": 2.4, "cost_mid": 3.1, "cost_high": 4.2,
                "key_uncertainty": "Pipeline infrastructure cost",
                "sources": ["Rystad 2023"],
            },
        ],
    }

"""
/v1/quant — Advanced financial & quantitative engineering endpoints.

LCOH with a policy/subsidy engine, stochastic Monte-Carlo risk, derived
commodity costs (NH3/MeOH/SAF), carbon-market abatement, and a working Excel
financial-model download. Pure maths lives in :mod:`app.quant`; this layer is
validation + auth + I/O.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, require_plan
from app.database import get_db
from app.models import User, CarbonPrice
from app.quant.lcoh import LCOHInputs, PolicyConfig, calculate_lcoh, POLICY_CATALOG
from app.quant.commodities import derive_all, COMMODITY_SPECS
from app.quant.carbon import carbon_abatement, DEFAULT_EU_ETS_EUR_PER_TONNE
from app.quant.monte_carlo import (
    Distribution, MonteCarloSpec, run_monte_carlo,
)
from app.quant.excel_export import build_financial_model, ModelAssumptions

router = APIRouter()


# ── Request schemas ───────────────────────────────────────────────────────────
class LCOHInputModel(BaseModel):
    capex_usd_per_kw: float = 1200.0
    electrolyzer_capacity_mw: float = 100.0
    stack_replacement_pct_capex: float = 0.40
    stack_lifetime_hours: float = 80_000.0
    electricity_price_usd_per_mwh: float = 45.0
    efficiency_kwh_per_kg: float = 52.0
    capacity_factor: float = Field(0.50, gt=0, le=1)
    fixed_om_pct_capex: float = 0.03
    water_usd_per_kg: float = 0.02
    other_opex_usd_per_kg: float = 0.05
    plant_life_years: int = Field(20, ge=1, le=40)
    discount_rate: float = Field(0.08, ge=0, le=0.5)

    def to_dataclass(self) -> LCOHInputs:
        try:
            return LCOHInputs(**self.model_dump())
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))


class PolicyModel(BaseModel):
    ira_45v_ptc: bool = False
    ira_45v_credit_usd_per_kg: float = 3.00
    ira_45v_duration_years: int = 10
    ira_itc: bool = False
    ira_itc_pct: float = Field(0.30, ge=0, le=0.7)
    eu_hydrogen_bank: bool = False
    eu_hydrogen_bank_premium_eur_per_kg: float = 0.40
    eu_hydrogen_bank_duration_years: int = 10
    eur_usd: float = 1.08

    def to_dataclass(self) -> PolicyConfig:
        return PolicyConfig(**self.model_dump())


class DistributionModel(BaseModel):
    kind: str = "normal"
    std_pct: float = 0.15
    low_mult: float = 0.7
    high_mult: float = 1.4

    def to_dataclass(self) -> Distribution:
        return Distribution(**self.model_dump())


class MonteCarloRequest(BaseModel):
    inputs: LCOHInputModel = Field(default_factory=LCOHInputModel)
    policy: Optional[PolicyModel] = None
    n_runs: int = Field(10_000, ge=100, le=200_000)
    capex_dist: DistributionModel = Field(default_factory=lambda: DistributionModel(kind="normal", std_pct=0.15))
    electricity_dist: DistributionModel = Field(default_factory=lambda: DistributionModel(kind="normal", std_pct=0.25))
    vary_capacity_factor: bool = False
    vary_efficiency: bool = False
    thresholds: Optional[List[float]] = None
    seed: Optional[int] = None


class CommodityRequest(BaseModel):
    inputs: LCOHInputModel = Field(default_factory=LCOHInputModel)
    policy: Optional[PolicyModel] = None
    co2_feedstock_usd_per_tonne: float = 50.0
    commodities: Optional[List[str]] = None


class CarbonRequest(BaseModel):
    carbon_price_eur_per_tonne: Optional[float] = None
    reference: str = "grey"
    green_intensity: Optional[float] = None
    eur_usd: float = 1.08
    annual_h2_tonnes: float = 0.0


class ExcelRequest(BaseModel):
    inputs: LCOHInputModel = Field(default_factory=LCOHInputModel)
    policy: Optional[PolicyModel] = None
    h2_sale_price_usd_per_kg: float = 5.50
    debt_fraction: float = Field(0.60, ge=0, le=0.95)
    debt_interest_rate: float = 0.06
    tax_rate: float = Field(0.21, ge=0, le=0.6)
    pnl_years: int = Field(10, ge=1, le=40)


# ── Endpoints ─────────────────────────────────────────────────────────────────
@router.get("/policies", summary="List supported incentive programmes")
async def list_policies(current_user: User = Depends(get_current_user)):
    return {"policies": POLICY_CATALOG, "commodities": list(COMMODITY_SPECS.keys())}


@router.post("/lcoh", summary="Compute LCOH with optional policy/subsidy adjustments")
async def compute_lcoh(
    inputs: LCOHInputModel,
    policy: Optional[PolicyModel] = None,
    current_user: User = Depends(get_current_user),
):
    p = policy.to_dataclass() if policy else None
    return calculate_lcoh(inputs.to_dataclass(), p).to_dict()


@router.post(
    "/monte-carlo",
    summary="Run a 10k-trial stochastic LCOH simulation",
    description="Vectorised Monte-Carlo over CAPEX & electricity (optionally capacity "
                "factor & efficiency). Returns percentiles + histogram/bell-curve. "
                "**Requires Analyst or Enterprise plan.**",
)
async def monte_carlo(
    req: MonteCarloRequest,
    current_user: User = Depends(require_plan(["analyst", "enterprise"])),
):
    base = req.inputs.to_dataclass()
    spec = MonteCarloSpec(
        base=base,
        policy=req.policy.to_dataclass() if req.policy else None,
        n_runs=req.n_runs,
        capex_dist=req.capex_dist.to_dataclass(),
        electricity_dist=req.electricity_dist.to_dataclass(),
        capacity_factor_dist=Distribution("normal", std_pct=0.12) if req.vary_capacity_factor else None,
        efficiency_dist=Distribution("normal", std_pct=0.06) if req.vary_efficiency else None,
        seed=req.seed,
    )
    return run_monte_carlo(spec, thresholds=req.thresholds).to_dict()


@router.post("/commodities", summary="Derive Green NH3 / MeOH / SAF cost from LCOH")
async def commodities(
    req: CommodityRequest,
    current_user: User = Depends(get_current_user),
):
    res = calculate_lcoh(req.inputs.to_dataclass(),
                         req.policy.to_dataclass() if req.policy else None)
    derived = derive_all(res.lcoh_after_policy_usd_per_kg,
                         req.co2_feedstock_usd_per_tonne, req.commodities)
    return {
        "lcoh_usd_per_kg": round(res.lcoh_after_policy_usd_per_kg, 5),
        "co2_feedstock_usd_per_tonne": req.co2_feedstock_usd_per_tonne,
        "commodities": [d.to_dict() for d in derived],
    }


async def _latest_carbon_price(db: AsyncSession, market: str = "EU_ETS") -> Dict:
    row = (await db.execute(
        select(CarbonPrice).where(CarbonPrice.market == market)
        .order_by(CarbonPrice.captured_at.desc()).limit(1)
    )).scalar_one_or_none()
    if row:
        return {"price_eur_per_tonne": row.price, "source": row.source or market,
                "captured_at": row.captured_at, "live": True}
    return {"price_eur_per_tonne": DEFAULT_EU_ETS_EUR_PER_TONNE,
            "source": "fallback", "captured_at": None, "live": False}


@router.get("/carbon/price/latest", summary="Latest stored EU-ETS carbon price")
async def latest_carbon_price(
    market: str = "EU_ETS",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _latest_carbon_price(db, market)


@router.post("/carbon", summary="Carbon abatement value: green vs grey/blue H2")
async def carbon(
    req: CarbonRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    price = req.carbon_price_eur_per_tonne
    live_meta = None
    if price is None:
        live_meta = await _latest_carbon_price(db)
        price = live_meta["price_eur_per_tonne"]
    try:
        result = carbon_abatement(
            carbon_price_eur_per_tonne=price,
            reference=req.reference,
            green_intensity=req.green_intensity,
            eur_usd=req.eur_usd,
            annual_h2_tonnes=req.annual_h2_tonnes,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    out = result.to_dict()
    out["price_source"] = live_meta if live_meta else {"source": "user_supplied", "live": False}
    return out


@router.post(
    "/excel",
    summary="Download a working Excel financial model (.xlsx)",
    description="Generates a formatted workbook with live formulas, P&L and a "
                "balance sheet that ties out. **Requires Analyst or Enterprise plan.**",
)
async def excel_export(
    req: ExcelRequest,
    current_user: User = Depends(require_plan(["analyst", "enterprise"])),
):
    data = build_financial_model(
        req.inputs.to_dataclass(),
        ModelAssumptions(
            h2_sale_price_usd_per_kg=req.h2_sale_price_usd_per_kg,
            debt_fraction=req.debt_fraction,
            debt_interest_rate=req.debt_interest_rate,
            tax_rate=req.tax_rate,
        ),
        req.policy.to_dataclass() if req.policy else None,
        pnl_years=req.pnl_years,
    )
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    filename = f"cleantech_quant_h2_model_{ts}.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

"""
/v1/scenarios — Save, load and compare LCOH scenarios.

Scenarios are organization-scoped: every member of a workspace sees the team's
saved models, enabling the multi-scenario side-by-side comparison that desks use
to defend an investment case. Creating/editing requires the Analyst role; viewing
is open to any member.
"""
from __future__ import annotations

from dataclasses import fields as dc_fields
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import User, Scenario, ProductType
from app.rbac import require_org_member, require_analyst
from app.quant.lcoh import LCOHInputs, PolicyConfig, calculate_lcoh

router = APIRouter()

_INPUT_KEYS = {f.name for f in dc_fields(LCOHInputs)}
_POLICY_KEYS = {f.name for f in dc_fields(PolicyConfig)}


def _compute(inputs: dict, policy: Optional[dict]):
    """Run the LCOH model from raw dicts, ignoring unknown keys, mapping errors to 422."""
    try:
        li = LCOHInputs(**{k: v for k, v in (inputs or {}).items() if k in _INPUT_KEYS})
        pc = PolicyConfig(**{k: v for k, v in (policy or {}).items() if k in _POLICY_KEYS}) if policy else None
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Invalid scenario inputs: {e}")
    return calculate_lcoh(li, pc)


# ── Schemas ───────────────────────────────────────────────────────────────────
class ScenarioCreate(BaseModel):
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    inputs: dict
    policy: Optional[dict] = None
    product_type: ProductType = ProductType.hydrogen
    is_shared: bool = True


class ScenarioUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    inputs: Optional[dict] = None
    policy: Optional[dict] = None
    product_type: Optional[ProductType] = None
    is_shared: Optional[bool] = None


class InlineScenario(BaseModel):
    name: str
    inputs: dict
    policy: Optional[dict] = None


class CompareRequest(BaseModel):
    scenario_ids: Optional[List[str]] = Field(None, description="2-4 saved scenario IDs")
    scenarios: Optional[List[InlineScenario]] = Field(None, description="Ad-hoc scenarios")


def _to_out(s: Scenario, result: Optional[dict] = None) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "description": s.description,
        "scenario_type": s.scenario_type,
        "product_type": s.product_type.value if s.product_type else "hydrogen",
        "inputs": s.inputs,
        "policy": s.policy,
        "result": result if result is not None else s.result_cache,
        "is_shared": s.is_shared,
        "created_by": str(s.created_by) if s.created_by else None,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


# ── CRUD ──────────────────────────────────────────────────────────────────────
@router.get("/", summary="List the organization's saved scenarios")
async def list_scenarios(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_org_member),
):
    rows = (await db.execute(
        select(Scenario).where(Scenario.organization_id == current_user.organization_id)
        .order_by(Scenario.created_at.desc())
    )).scalars().all()
    return {"data": [_to_out(s) for s in rows], "count": len(rows)}


@router.post("/", status_code=201, summary="Save a new scenario (Analyst+)")
async def create_scenario(
    payload: ScenarioCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_analyst),
):
    result = _compute(payload.inputs, payload.policy)
    scenario = Scenario(
        organization_id=current_user.organization_id,
        created_by=current_user.id,
        name=payload.name,
        description=payload.description,
        inputs=payload.inputs,
        policy=payload.policy,
        product_type=payload.product_type,
        is_shared=payload.is_shared,
        result_cache=result.to_dict(),
    )
    db.add(scenario)
    await db.commit()
    await db.refresh(scenario)
    return _to_out(scenario)


async def _get_owned(db: AsyncSession, scenario_id: str, user: User) -> Scenario:
    s = (await db.execute(select(Scenario).where(Scenario.id == scenario_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Scenario not found")
    if not user.is_admin and s.organization_id != user.organization_id:
        raise HTTPException(status_code=403, detail="Scenario belongs to another organization")
    return s


@router.get("/{scenario_id}", summary="Get a scenario")
async def get_scenario(
    scenario_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_org_member),
):
    return _to_out(await _get_owned(db, scenario_id, current_user))


@router.patch("/{scenario_id}", summary="Update a scenario (Analyst+)")
async def update_scenario(
    scenario_id: str,
    payload: ScenarioUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_analyst),
):
    s = await _get_owned(db, scenario_id, current_user)
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(s, k, v)
    # Recompute the cached result if the model changed
    if "inputs" in data or "policy" in data:
        s.result_cache = _compute(s.inputs, s.policy).to_dict()
    await db.commit()
    await db.refresh(s)
    return _to_out(s)


@router.delete("/{scenario_id}", status_code=204, summary="Delete a scenario (Analyst+)")
async def delete_scenario(
    scenario_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_analyst),
):
    s = await _get_owned(db, scenario_id, current_user)
    await db.delete(s)
    await db.commit()


# ── Comparison ────────────────────────────────────────────────────────────────
@router.post("/compare", summary="Compare 2-4 scenarios side by side")
async def compare_scenarios(
    req: CompareRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items: List[dict] = []

    if req.scenario_ids:
        if not (2 <= len(req.scenario_ids) <= 4):
            raise HTTPException(status_code=422, detail="Provide between 2 and 4 scenario_ids")
        for sid in req.scenario_ids:
            s = await _get_owned(db, sid, current_user)
            res = _compute(s.inputs, s.policy)
            items.append({"name": s.name, "id": str(s.id), "result": res.to_dict()})
    elif req.scenarios:
        if not (2 <= len(req.scenarios) <= 4):
            raise HTTPException(status_code=422, detail="Provide between 2 and 4 scenarios")
        for sc in req.scenarios:
            res = _compute(sc.inputs, sc.policy)
            items.append({"name": sc.name, "id": None, "result": res.to_dict()})
    else:
        raise HTTPException(status_code=422, detail="Provide scenario_ids or scenarios")

    # Build a comparison matrix keyed by metric for easy table rendering.
    metrics = ["lcoh_usd_per_kg", "lcoh_after_policy_usd_per_kg"]
    matrix = {m: [round(i["result"][m], 4) for i in items] for m in metrics}
    component_keys = list(items[0]["result"]["components"].keys())
    matrix_components = {
        c: [round(i["result"]["components"].get(c, 0.0), 4) for i in items]
        for c in component_keys
    }
    best_idx = min(range(len(items)), key=lambda k: items[k]["result"]["lcoh_after_policy_usd_per_kg"])

    return {
        "scenarios": [{"name": i["name"], "id": i["id"]} for i in items],
        "comparison": matrix,
        "components": matrix_components,
        "best_scenario_index": best_idx,
        "results": items,
    }

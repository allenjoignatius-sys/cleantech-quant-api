"""
Levelised Cost of Hydrogen (LCOH) techno-economic model + Policy/Subsidy engine.

The model is a transparent, component-wise discounted-cash-flow representation of
a grid- or PPA-powered electrolytic green-hydrogen plant. Every component is
returned in USD/kg H2 so the breakdown sums exactly to the headline figure
(important for audit and for the Excel export which mirrors this maths).

Policy engine
-------------
Real-world incentives materially change project economics. We model the headline
programmes that energy-transition desks actually underwrite against:

* **US IRA §45V** Production Tax Credit — up to $3.00/kg of clean H2 for the
  first 10 years (tiered by lifecycle carbon intensity).
* **US IRA §48E ITC** — investment tax credit reducing eligible CAPEX (mutually
  exclusive with 45V in practice; we let the caller pick).
* **EU Hydrogen Bank** — fixed-premium (€/kg) subsidy awarded by auction,
  typically over a 10-year contract.

Credits that apply for fewer years than the plant life are *levelised* over the
plant's discounted production so the per-kg figure is directly comparable to the
LCOH itself, rather than naively subtracting the nominal credit.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, Optional

HOURS_PER_YEAR = 8760.0


# ──────────────────────────────────────────────────────────────────────────────
# Inputs
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class LCOHInputs:
    """Inputs for an electrolytic green-hydrogen plant. SI-ish units noted inline."""

    # CAPEX
    capex_usd_per_kw: float = 1200.0          # installed electrolyzer system $/kW
    electrolyzer_capacity_mw: float = 100.0   # nameplate electrical input, MW
    stack_replacement_pct_capex: float = 0.40  # cost of a stack swap as % of CAPEX
    stack_lifetime_hours: float = 80_000.0     # operating hours before stack swap

    # Energy / performance
    electricity_price_usd_per_mwh: float = 45.0  # blended PPA / grid price
    efficiency_kwh_per_kg: float = 52.0          # system electricity per kg H2 (LHV ~33.3)
    capacity_factor: float = 0.50                # 0..1 (annual utilisation)

    # OPEX
    fixed_om_pct_capex: float = 0.03   # annual fixed O&M as % of CAPEX
    water_usd_per_kg: float = 0.02     # demineralised water per kg H2
    other_opex_usd_per_kg: float = 0.05

    # Finance
    plant_life_years: int = 20
    discount_rate: float = 0.08        # WACC (real)

    def __post_init__(self) -> None:
        if self.electrolyzer_capacity_mw <= 0:
            raise ValueError("electrolyzer_capacity_mw must be > 0")
        if self.efficiency_kwh_per_kg <= 0:
            raise ValueError("efficiency_kwh_per_kg must be > 0")
        if not 0 < self.capacity_factor <= 1:
            raise ValueError("capacity_factor must be in (0, 1]")
        if self.plant_life_years <= 0:
            raise ValueError("plant_life_years must be > 0")
        if self.discount_rate < 0:
            raise ValueError("discount_rate must be >= 0")


# ──────────────────────────────────────────────────────────────────────────────
# Policy / subsidy engine
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class PolicyConfig:
    """Toggleable incentive programmes. All amounts are configurable."""

    # US IRA §45V Production Tax Credit (clean hydrogen)
    ira_45v_ptc: bool = False
    ira_45v_credit_usd_per_kg: float = 3.00   # tier-1 (<0.45 kgCO2e/kgH2)
    ira_45v_duration_years: int = 10

    # US IRA §48E Investment Tax Credit (reduces CAPEX)
    ira_itc: bool = False
    ira_itc_pct: float = 0.30                 # 6% base, up to 30% with labour reqs, +bonus

    # EU Hydrogen Bank fixed premium (auction-awarded)
    eu_hydrogen_bank: bool = False
    eu_hydrogen_bank_premium_eur_per_kg: float = 0.40
    eu_hydrogen_bank_duration_years: int = 10
    eur_usd: float = 1.08                     # FX for €→$ conversion

    def any_active(self) -> bool:
        return bool(self.ira_45v_ptc or self.ira_itc or self.eu_hydrogen_bank)


# Catalog surfaced to the API/UI so toggles are self-describing.
POLICY_CATALOG = [
    {
        "id": "ira_45v_ptc",
        "name": "US IRA §45V Production Tax Credit",
        "region": "United States",
        "kind": "production_credit",
        "default_value_usd_per_kg": 3.00,
        "duration_years": 10,
        "description": (
            "Up to $3.00/kg of clean hydrogen for the first 10 years of operation, "
            "tiered by lifecycle carbon intensity (<0.45 kgCO2e/kg qualifies for the "
            "full credit). Levelised over discounted lifetime production."
        ),
        "citation": "Inflation Reduction Act of 2022, §13204 (26 U.S.C. §45V)",
    },
    {
        "id": "ira_itc",
        "name": "US IRA §48E Investment Tax Credit",
        "region": "United States",
        "kind": "capex_credit",
        "default_value_pct": 0.30,
        "description": (
            "Investment tax credit of up to 30% of eligible CAPEX (6% base + bonus for "
            "prevailing-wage / domestic-content). Mutually exclusive with §45V in practice."
        ),
        "citation": "Inflation Reduction Act of 2022 (26 U.S.C. §48E)",
    },
    {
        "id": "eu_hydrogen_bank",
        "name": "EU Hydrogen Bank Fixed Premium",
        "region": "European Union",
        "kind": "production_premium",
        "default_value_eur_per_kg": 0.40,
        "duration_years": 10,
        "description": (
            "Fixed premium (€/kg) awarded competitively via the European Hydrogen Bank "
            "auctions (e.g. the €0.37–0.48/kg clearing bids of the 2024 pilot), paid for "
            "a 10-year contract on top of the market offtake price."
        ),
        "citation": "European Hydrogen Bank, Innovation Fund auction (Reg. (EU) 2023/1184)",
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# Results
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class LCOHResult:
    """Full, auditable LCOH breakdown (all USD/kg H2 unless noted)."""

    lcoh_usd_per_kg: float                 # gross, before policy
    lcoh_after_policy_usd_per_kg: float    # net, after incentives
    components: Dict[str, float] = field(default_factory=dict)
    policy_adjustments: Dict[str, float] = field(default_factory=dict)  # signed $/kg
    annual_h2_kg: float = 0.0
    annual_h2_tonnes: float = 0.0
    capex_total_usd: float = 0.0
    crf: float = 0.0                       # capital recovery factor

    def to_dict(self) -> dict:
        d = asdict(self)
        # round for transport / display stability
        for k in ("lcoh_usd_per_kg", "lcoh_after_policy_usd_per_kg", "crf"):
            d[k] = round(d[k], 6)
        d["components"] = {k: round(v, 6) for k, v in self.components.items()}
        d["policy_adjustments"] = {k: round(v, 6) for k, v in self.policy_adjustments.items()}
        return d


# ──────────────────────────────────────────────────────────────────────────────
# Core maths
# ──────────────────────────────────────────────────────────────────────────────
def capital_recovery_factor(discount_rate: float, years: int) -> float:
    """Annuity factor that converts an up-front cost into a level annual payment."""
    r, n = discount_rate, years
    if r == 0:
        return 1.0 / n
    return (r * (1 + r) ** n) / ((1 + r) ** n - 1)


def _discounted_unit_years(discount_rate: float, years: int) -> float:
    """Sum_{t=1..years} 1/(1+r)^t  — present-value of a unit annuity."""
    r = discount_rate
    if r == 0:
        return float(years)
    return sum(1.0 / (1.0 + r) ** t for t in range(1, years + 1))


def _levelize_time_limited_credit(
    credit_per_kg: float,
    credit_years: int,
    plant_life_years: int,
    discount_rate: float,
) -> float:
    """
    Levelise a credit that is only paid for `credit_years` of a `plant_life_years`
    plant, assuming constant annual production. Returns the equivalent flat $/kg
    reduction over the *whole* plant life.

        levelised = credit * PV(annuity, credit_years) / PV(annuity, plant_life)
    """
    credit_years = min(credit_years, plant_life_years)
    pv_credit = _discounted_unit_years(discount_rate, credit_years)
    pv_life = _discounted_unit_years(discount_rate, plant_life_years)
    if pv_life == 0:
        return 0.0
    return credit_per_kg * (pv_credit / pv_life)


def calculate_lcoh(
    inputs: LCOHInputs,
    policy: Optional[PolicyConfig] = None,
) -> LCOHResult:
    """Compute the component-wise LCOH and apply the active policy adjustments."""
    capacity_kw = inputs.electrolyzer_capacity_mw * 1000.0

    # Annual production
    annual_kwh = capacity_kw * HOURS_PER_YEAR * inputs.capacity_factor
    annual_kg = annual_kwh / inputs.efficiency_kwh_per_kg
    annual_tonnes = annual_kg / 1000.0

    # CAPEX (optionally reduced by an investment tax credit)
    capex_total = inputs.capex_usd_per_kw * capacity_kw
    itc_capex_total = capex_total
    if policy and policy.ira_itc:
        itc_capex_total = capex_total * (1.0 - policy.ira_itc_pct)

    crf = capital_recovery_factor(inputs.discount_rate, inputs.plant_life_years)
    annualized_capex = itc_capex_total * crf
    capex_per_kg = annualized_capex / annual_kg

    # Fixed O&M (% of *gross* CAPEX — the kit still needs maintaining)
    fixed_om_per_kg = (capex_total * inputs.fixed_om_pct_capex) / annual_kg

    # Stack replacement: number of swaps over plant life, levelised
    operating_hours_total = HOURS_PER_YEAR * inputs.capacity_factor * inputs.plant_life_years
    n_replacements = max(0.0, (operating_hours_total / inputs.stack_lifetime_hours) - 1.0)
    stack_cost_total = capex_total * inputs.stack_replacement_pct_capex * n_replacements
    stack_per_kg = (stack_cost_total * crf) / annual_kg if annual_kg else 0.0

    # Electricity — the dominant driver
    electricity_per_kg = inputs.efficiency_kwh_per_kg * (inputs.electricity_price_usd_per_mwh / 1000.0)

    water_per_kg = inputs.water_usd_per_kg
    other_per_kg = inputs.other_opex_usd_per_kg

    components = {
        "capex": capex_per_kg,
        "fixed_om": fixed_om_per_kg,
        "stack_replacement": stack_per_kg,
        "electricity": electricity_per_kg,
        "water": water_per_kg,
        "other": other_per_kg,
    }
    gross_lcoh = sum(components.values())

    # ── Policy adjustments (signed: negative = saving) ──────────────────────────
    adjustments: Dict[str, float] = {}
    if policy:
        if policy.ira_itc:
            # Express the ITC's effect on the per-kg CAPEX line for transparency.
            full_capex_per_kg = (capex_total * crf) / annual_kg
            adjustments["ira_itc"] = capex_per_kg - full_capex_per_kg  # negative
        if policy.ira_45v_ptc:
            adjustments["ira_45v_ptc"] = -_levelize_time_limited_credit(
                policy.ira_45v_credit_usd_per_kg,
                policy.ira_45v_duration_years,
                inputs.plant_life_years,
                inputs.discount_rate,
            )
        if policy.eu_hydrogen_bank:
            premium_usd = policy.eu_hydrogen_bank_premium_eur_per_kg * policy.eur_usd
            adjustments["eu_hydrogen_bank"] = -_levelize_time_limited_credit(
                premium_usd,
                policy.eu_hydrogen_bank_duration_years,
                inputs.plant_life_years,
                inputs.discount_rate,
            )

    # The ITC already changed `capex_per_kg`, so it is *baked into* gross_lcoh.
    # Production credits (45V, EU) are subtracted from gross to get the net figure.
    production_adjustments = sum(
        v for k, v in adjustments.items() if k in ("ira_45v_ptc", "eu_hydrogen_bank")
    )
    net_lcoh = gross_lcoh + production_adjustments

    return LCOHResult(
        lcoh_usd_per_kg=gross_lcoh,
        lcoh_after_policy_usd_per_kg=net_lcoh,
        components=components,
        policy_adjustments=adjustments,
        annual_h2_kg=annual_kg,
        annual_h2_tonnes=annual_tonnes,
        capex_total_usd=capex_total,
        crf=crf,
    )


def levelized_lcoh(inputs: LCOHInputs, policy: Optional[PolicyConfig] = None) -> float:
    """Convenience: just the net headline LCOH (USD/kg)."""
    return calculate_lcoh(inputs, policy).lcoh_after_policy_usd_per_kg

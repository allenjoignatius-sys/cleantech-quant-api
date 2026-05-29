"""
Derived green-molecule economics.

Given an LCOH (USD/kg H2) we derive the levelised cost of the downstream
power-to-X products that hydrogen offtake desks actually trade:

    * Green Ammonia (NH3)        — Haber-Bosch from green H2 + N2 (ASU)
    * Green Methanol (CH3OH)     — CO2 hydrogenation
    * Sustainable Aviation Fuel  — e-kerosene via Fischer-Tropsch (PtL)

Each product carries a hydrogen *mass intensity* (t H2 per t product, from
reaction stoichiometry adjusted for realistic process yields), an optional CO2
feedstock intensity, and a synthesis cost adder (capex+opex of the synloop
expressed per tonne of product). The result decomposes cost into its
hydrogen / CO2 / synthesis parts so analysts can see what drives the number.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

MJ_PER_MWH = 3600.0


@dataclass(frozen=True)
class CommoditySpec:
    id: str
    name: str
    formula: str
    h2_tonnes_per_tonne: float        # t H2 consumed per t product
    co2_tonnes_per_tonne: float       # t CO2 feedstock per t product
    synthesis_usd_per_tonne: float    # synloop capex+opex adder, $/t product
    energy_density_mj_per_kg: float   # LHV
    citation: str = ""


# Stoichiometry + process notes are documented in each citation string.
COMMODITY_SPECS: Dict[str, CommoditySpec] = {
    "ammonia": CommoditySpec(
        id="ammonia",
        name="Green Ammonia",
        formula="NH3",
        # NH3 is 17.76 wt% H2; real plants run ~0.18 t H2/t NH3 incl. losses.
        h2_tonnes_per_tonne=0.180,
        co2_tonnes_per_tonne=0.0,         # N2 from air (ASU), no carbon feedstock
        synthesis_usd_per_tonne=150.0,    # Haber-Bosch + ASU levelised adder
        energy_density_mj_per_kg=18.6,
        citation="IRENA, Innovation Outlook: Renewable Ammonia (2022)",
    ),
    "methanol": CommoditySpec(
        id="methanol",
        name="Green Methanol",
        formula="CH3OH",
        # CO2 + 3H2 -> CH3OH + H2O : 0.189 t H2 & 1.37 t CO2 per t MeOH.
        h2_tonnes_per_tonne=0.189,
        co2_tonnes_per_tonne=1.37,
        synthesis_usd_per_tonne=180.0,
        energy_density_mj_per_kg=19.9,
        citation="IRENA/Methanol Institute, Innovation Outlook: Renewable Methanol (2021)",
    ),
    "saf": CommoditySpec(
        id="saf",
        name="Sustainable Aviation Fuel (e-kerosene)",
        formula="CnH2n+2",
        # Power-to-Liquid via FT: ~0.50 t H2 & ~3.0 t CO2 per t e-kerosene.
        h2_tonnes_per_tonne=0.50,
        co2_tonnes_per_tonne=3.0,
        synthesis_usd_per_tonne=600.0,    # RWGS + FT + upgrading
        energy_density_mj_per_kg=43.0,
        citation="Concawe/Aramco e-fuels review (2022); IEA PtL assessments",
    ),
}


@dataclass
class CommodityResult:
    commodity_id: str
    name: str
    formula: str
    cost_usd_per_tonne: float
    cost_usd_per_kg: float
    cost_usd_per_mwh: float
    breakdown_usd_per_tonne: Dict[str, float]   # hydrogen / co2 / synthesis
    h2_tonnes_per_tonne: float
    co2_tonnes_per_tonne: float
    lcoh_usd_per_kg: float
    co2_feedstock_usd_per_tonne: float

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("cost_usd_per_tonne", "cost_usd_per_kg", "cost_usd_per_mwh"):
            d[k] = round(d[k], 4)
        d["breakdown_usd_per_tonne"] = {k: round(v, 2) for k, v in self.breakdown_usd_per_tonne.items()}
        return d


def derive_commodity_cost(
    lcoh_usd_per_kg: float,
    commodity: str,
    co2_feedstock_usd_per_tonne: float = 0.0,
) -> CommodityResult:
    """
    Levelised cost of a green molecule given an upstream LCOH.

    Parameters
    ----------
    lcoh_usd_per_kg
        Delivered hydrogen cost feeding the synloop (USD/kg H2).
    commodity
        One of ``COMMODITY_SPECS`` keys (ammonia / methanol / saf).
    co2_feedstock_usd_per_tonne
        Cost of captured CO2 (biogenic / DAC). Ignored for ammonia.
    """
    spec = COMMODITY_SPECS.get(commodity)
    if spec is None:
        raise ValueError(
            f"Unknown commodity '{commodity}'. Options: {sorted(COMMODITY_SPECS)}"
        )
    if lcoh_usd_per_kg < 0:
        raise ValueError("lcoh_usd_per_kg must be >= 0")

    lcoh_per_tonne_h2 = lcoh_usd_per_kg * 1000.0

    hydrogen_cost = spec.h2_tonnes_per_tonne * lcoh_per_tonne_h2
    co2_cost = spec.co2_tonnes_per_tonne * max(0.0, co2_feedstock_usd_per_tonne)
    synthesis_cost = spec.synthesis_usd_per_tonne

    total_per_tonne = hydrogen_cost + co2_cost + synthesis_cost
    per_kg = total_per_tonne / 1000.0
    per_mwh = per_kg / (spec.energy_density_mj_per_kg / MJ_PER_MWH)

    return CommodityResult(
        commodity_id=spec.id,
        name=spec.name,
        formula=spec.formula,
        cost_usd_per_tonne=total_per_tonne,
        cost_usd_per_kg=per_kg,
        cost_usd_per_mwh=per_mwh,
        breakdown_usd_per_tonne={
            "hydrogen": hydrogen_cost,
            "co2_feedstock": co2_cost,
            "synthesis": synthesis_cost,
        },
        h2_tonnes_per_tonne=spec.h2_tonnes_per_tonne,
        co2_tonnes_per_tonne=spec.co2_tonnes_per_tonne,
        lcoh_usd_per_kg=lcoh_usd_per_kg,
        co2_feedstock_usd_per_tonne=co2_feedstock_usd_per_tonne,
    )


def derive_all(
    lcoh_usd_per_kg: float,
    co2_feedstock_usd_per_tonne: float = 0.0,
    commodities: Optional[List[str]] = None,
) -> List[CommodityResult]:
    """Derive every (or a subset of) commodity from a single LCOH."""
    keys = commodities or list(COMMODITY_SPECS.keys())
    return [derive_commodity_cost(lcoh_usd_per_kg, k, co2_feedstock_usd_per_tonne) for k in keys]

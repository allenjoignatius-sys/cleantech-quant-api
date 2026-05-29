"""
Carbon-market integration.

Quantifies the carbon-abatement value of green hydrogen versus the grey (SMR)
and blue (SMR+CCS) incumbents, priced off a carbon market such as the EU
Emissions Trading System (EU-ETS). The live price feed lives in a service layer;
this module is the pure maths and the production-pathway emission factors so it
stays unit-testable offline.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict

# Lifecycle CO2e intensity of H2 production pathways (kg CO2e / kg H2).
# Grey = unabated steam-methane reforming; blue = SMR + ~90% CCS;
# green = renewable electrolysis (small residual from construction/grid).
EMISSION_FACTORS_KG_CO2_PER_KG_H2: Dict[str, float] = {
    "grey": 9.5,
    "blue": 1.5,
    "green": 0.5,
}

# Conservative fallback used when no live EU-ETS quote is available.
DEFAULT_EU_ETS_EUR_PER_TONNE = 75.0


@dataclass
class CarbonResult:
    pathway_reference: str          # the incumbent we compare against (grey/blue)
    carbon_price_eur_per_tonne: float
    carbon_price_usd_per_tonne: float
    eur_usd: float
    reference_intensity_kg_per_kg: float
    green_intensity_kg_per_kg: float
    abated_intensity_kg_per_kg: float
    # Per-kg economics
    carbon_cost_reference_usd_per_kg_h2: float   # carbon liability of the incumbent
    carbon_cost_green_usd_per_kg_h2: float
    abatement_value_usd_per_kg_h2: float         # saving green captures vs reference
    # Plant-level economics (optional)
    annual_h2_tonnes: float
    annual_abatement_tonnes_co2: float
    annual_abatement_value_usd: float

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, float):
                d[k] = round(v, 4)
        return d


def carbon_cost_per_kg_h2(
    intensity_kg_co2_per_kg_h2: float,
    carbon_price_eur_per_tonne: float,
    eur_usd: float = 1.08,
) -> float:
    """USD/kg H2 carbon liability for a given production intensity."""
    price_usd_per_tonne = carbon_price_eur_per_tonne * eur_usd
    return intensity_kg_co2_per_kg_h2 * (price_usd_per_tonne / 1000.0)


def carbon_abatement(
    carbon_price_eur_per_tonne: float = DEFAULT_EU_ETS_EUR_PER_TONNE,
    reference: str = "grey",
    green_intensity: float | None = None,
    eur_usd: float = 1.08,
    annual_h2_tonnes: float = 0.0,
) -> CarbonResult:
    """
    Value of switching from a fossil reference pathway to green hydrogen.

    Parameters
    ----------
    carbon_price_eur_per_tonne
        Carbon price (EU-ETS EUA or equivalent), €/tCO2.
    reference
        Incumbent pathway to displace: ``"grey"`` (default) or ``"blue"``.
    green_intensity
        Override the green H2 emission factor (kg CO2e/kg H2).
    annual_h2_tonnes
        If supplied, scales the per-kg value up to an annual plant figure.
    """
    if reference not in EMISSION_FACTORS_KG_CO2_PER_KG_H2:
        raise ValueError(f"reference must be one of {list(EMISSION_FACTORS_KG_CO2_PER_KG_H2)}")
    if carbon_price_eur_per_tonne < 0:
        raise ValueError("carbon_price_eur_per_tonne must be >= 0")

    ref_intensity = EMISSION_FACTORS_KG_CO2_PER_KG_H2[reference]
    green = EMISSION_FACTORS_KG_CO2_PER_KG_H2["green"] if green_intensity is None else green_intensity
    abated_intensity = max(0.0, ref_intensity - green)

    cost_ref = carbon_cost_per_kg_h2(ref_intensity, carbon_price_eur_per_tonne, eur_usd)
    cost_green = carbon_cost_per_kg_h2(green, carbon_price_eur_per_tonne, eur_usd)
    abatement_per_kg = cost_ref - cost_green

    annual_kg = annual_h2_tonnes * 1000.0
    annual_abated_tonnes = abated_intensity * annual_kg / 1000.0
    annual_value = abatement_per_kg * annual_kg

    return CarbonResult(
        pathway_reference=reference,
        carbon_price_eur_per_tonne=carbon_price_eur_per_tonne,
        carbon_price_usd_per_tonne=carbon_price_eur_per_tonne * eur_usd,
        eur_usd=eur_usd,
        reference_intensity_kg_per_kg=ref_intensity,
        green_intensity_kg_per_kg=green,
        abated_intensity_kg_per_kg=abated_intensity,
        carbon_cost_reference_usd_per_kg_h2=cost_ref,
        carbon_cost_green_usd_per_kg_h2=cost_green,
        abatement_value_usd_per_kg_h2=abatement_per_kg,
        annual_h2_tonnes=annual_h2_tonnes,
        annual_abatement_tonnes_co2=annual_abated_tonnes,
        annual_abatement_value_usd=annual_value,
    )

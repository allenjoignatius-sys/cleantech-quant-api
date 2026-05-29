"""
Stochastic LCOH simulation.

Runs N (default 10,000) Monte-Carlo trials of the LCOH model, sampling the two
dominant value drivers — CAPEX and electricity price — plus optional uncertainty
on capacity factor and electrolyzer efficiency. The LCOH maths is re-expressed in
a fully *vectorised* numpy form so 10k+ trials evaluate in microseconds rather
than a Python loop, returning a probabilistic distribution (percentiles + a
histogram / fitted bell curve) suitable for an institutional risk dashboard.

The vectorised maths intentionally mirrors :func:`app.quant.lcoh.calculate_lcoh`
so the deterministic base case lands at the centre of the distribution.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

import numpy as np

from app.quant.lcoh import (
    LCOHInputs,
    PolicyConfig,
    HOURS_PER_YEAR,
    capital_recovery_factor,
    _levelize_time_limited_credit,
)


@dataclass
class Distribution:
    """A parametric distribution for a varied input, expressed as multipliers of
    the deterministic base value (so it scales with whatever base the user sets)."""

    kind: str = "normal"          # normal | triangular | uniform | lognormal
    std_pct: float = 0.15         # for normal/lognormal: 1σ as a fraction of base
    low_mult: float = 0.7         # for triangular/uniform
    high_mult: float = 1.4

    def sample(self, base: float, n: int, rng: np.random.Generator) -> np.ndarray:
        if self.kind == "normal":
            return rng.normal(base, abs(base) * self.std_pct, n)
        if self.kind == "lognormal":
            # preserve mean ≈ base
            sigma = self.std_pct
            mu = np.log(max(base, 1e-9)) - 0.5 * sigma ** 2
            return rng.lognormal(mu, sigma, n)
        if self.kind == "uniform":
            return rng.uniform(base * self.low_mult, base * self.high_mult, n)
        if self.kind == "triangular":
            return rng.triangular(base * self.low_mult, base, base * self.high_mult, n)
        raise ValueError(f"unknown distribution kind: {self.kind}")


@dataclass
class MonteCarloSpec:
    base: LCOHInputs
    policy: Optional[PolicyConfig] = None
    n_runs: int = 10_000
    capex_dist: Distribution = field(default_factory=lambda: Distribution("normal", std_pct=0.15))
    electricity_dist: Distribution = field(default_factory=lambda: Distribution("normal", std_pct=0.25))
    capacity_factor_dist: Optional[Distribution] = None
    efficiency_dist: Optional[Distribution] = None
    seed: Optional[int] = None


@dataclass
class MonteCarloResult:
    n_runs: int
    base_case_usd_per_kg: float
    mean: float
    std: float
    min: float
    max: float
    percentiles: Dict[str, float]                 # P5..P95
    histogram: Dict[str, List[float]]             # {"bin_centers": [...], "counts": [...]}
    bell_curve: Dict[str, List[float]]            # fitted normal pdf for a smooth overlay
    prob_below: Dict[str, float] = field(default_factory=dict)  # threshold -> probability

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("base_case_usd_per_kg", "mean", "std", "min", "max"):
            d[k] = round(d[k], 5)
        d["percentiles"] = {k: round(v, 5) for k, v in self.percentiles.items()}
        return d


def _lcoh_vectorized(
    capex_per_kw: np.ndarray,
    elec_price: np.ndarray,
    efficiency: np.ndarray,
    capacity_factor: np.ndarray,
    base: LCOHInputs,
    policy: Optional[PolicyConfig],
) -> np.ndarray:
    """Vectorised LCOH (USD/kg) net of policy. Inputs are numpy arrays of length N."""
    capacity_kw = base.electrolyzer_capacity_mw * 1000.0
    annual_kwh = capacity_kw * HOURS_PER_YEAR * capacity_factor
    annual_kg = annual_kwh / efficiency

    crf = capital_recovery_factor(base.discount_rate, base.plant_life_years)

    capex_total = capex_per_kw * capacity_kw
    itc_mult = 1.0
    if policy and policy.ira_itc:
        itc_mult = 1.0 - policy.ira_itc_pct
    capex_per_kg = (capex_total * itc_mult * crf) / annual_kg

    fixed_om_per_kg = (capex_total * base.fixed_om_pct_capex) / annual_kg

    operating_hours_total = HOURS_PER_YEAR * capacity_factor * base.plant_life_years
    n_repl = np.maximum(0.0, (operating_hours_total / base.stack_lifetime_hours) - 1.0)
    stack_per_kg = (capex_total * base.stack_replacement_pct_capex * n_repl * crf) / annual_kg

    electricity_per_kg = efficiency * (elec_price / 1000.0)
    water_per_kg = base.water_usd_per_kg
    other_per_kg = base.other_opex_usd_per_kg

    gross = capex_per_kg + fixed_om_per_kg + stack_per_kg + electricity_per_kg + water_per_kg + other_per_kg

    # Production credits are scalar (don't depend on the sampled drivers).
    production_adj = 0.0
    if policy:
        if policy.ira_45v_ptc:
            production_adj -= _levelize_time_limited_credit(
                policy.ira_45v_credit_usd_per_kg,
                policy.ira_45v_duration_years,
                base.plant_life_years,
                base.discount_rate,
            )
        if policy.eu_hydrogen_bank:
            premium_usd = policy.eu_hydrogen_bank_premium_eur_per_kg * policy.eur_usd
            production_adj -= _levelize_time_limited_credit(
                premium_usd,
                policy.eu_hydrogen_bank_duration_years,
                base.plant_life_years,
                base.discount_rate,
            )
    return gross + production_adj


def run_monte_carlo(
    spec: MonteCarloSpec,
    thresholds: Optional[List[float]] = None,
    n_bins: int = 40,
) -> MonteCarloResult:
    """Execute the vectorised Monte-Carlo simulation and summarise the distribution."""
    if spec.n_runs < 100:
        raise ValueError("n_runs must be >= 100 for a meaningful distribution")
    if spec.n_runs > 1_000_000:
        raise ValueError("n_runs capped at 1,000,000")

    rng = np.random.default_rng(spec.seed)
    n = spec.n_runs
    base = spec.base

    capex = np.clip(spec.capex_dist.sample(base.capex_usd_per_kw, n, rng), 1.0, None)
    elec = np.clip(spec.electricity_dist.sample(base.electricity_price_usd_per_mwh, n, rng), 0.0, None)

    if spec.efficiency_dist:
        eff = np.clip(spec.efficiency_dist.sample(base.efficiency_kwh_per_kg, n, rng), 30.0, None)
    else:
        eff = np.full(n, base.efficiency_kwh_per_kg)

    if spec.capacity_factor_dist:
        cf = np.clip(spec.capacity_factor_dist.sample(base.capacity_factor, n, rng), 0.05, 1.0)
    else:
        cf = np.full(n, base.capacity_factor)

    samples = _lcoh_vectorized(capex, elec, eff, cf, base, spec.policy)
    samples = samples[np.isfinite(samples)]

    # Deterministic base case at the distribution centre.
    base_case = float(
        _lcoh_vectorized(
            np.array([base.capex_usd_per_kw]),
            np.array([base.electricity_price_usd_per_mwh]),
            np.array([base.efficiency_kwh_per_kg]),
            np.array([base.capacity_factor]),
            base,
            spec.policy,
        )[0]
    )

    pct_levels = [5, 10, 25, 50, 75, 90, 95]
    pct_vals = np.percentile(samples, pct_levels)
    percentiles = {f"P{p}": float(v) for p, v in zip(pct_levels, pct_vals)}

    counts, edges = np.histogram(samples, bins=n_bins)
    centers = (edges[:-1] + edges[1:]) / 2.0

    mean = float(np.mean(samples))
    std = float(np.std(samples))
    # Smooth normal overlay scaled to the histogram area for a clean bell curve.
    bin_width = float(edges[1] - edges[0]) if len(edges) > 1 else 1.0
    pdf = (
        (1.0 / (std * np.sqrt(2 * np.pi))) * np.exp(-0.5 * ((centers - mean) / std) ** 2)
        if std > 0 else np.zeros_like(centers)
    )
    bell_y = pdf * len(samples) * bin_width

    prob_below: Dict[str, float] = {}
    for t in (thresholds or []):
        prob_below[str(t)] = float(np.mean(samples < t))

    return MonteCarloResult(
        n_runs=int(samples.size),
        base_case_usd_per_kg=base_case,
        mean=mean,
        std=std,
        min=float(np.min(samples)),
        max=float(np.max(samples)),
        percentiles=percentiles,
        histogram={"bin_centers": [round(float(c), 5) for c in centers],
                   "counts": [int(c) for c in counts]},
        bell_curve={"x": [round(float(c), 5) for c in centers],
                    "y": [round(float(y), 3) for y in bell_y]},
        prob_below=prob_below,
    )

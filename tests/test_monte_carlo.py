"""Unit tests for the vectorised Monte-Carlo LCOH simulation (no DB/network)."""
import numpy as np
import pytest

from app.quant.lcoh import LCOHInputs, PolicyConfig, calculate_lcoh
from app.quant.monte_carlo import (
    Distribution, MonteCarloSpec, run_monte_carlo,
)


def _spec(**kw):
    return MonteCarloSpec(base=LCOHInputs(), seed=42, **kw)


class TestMonteCarlo:
    def test_runs_ten_thousand(self):
        res = run_monte_carlo(_spec(n_runs=10_000))
        assert res.n_runs == 10_000
        assert len(res.histogram["counts"]) == 40
        assert sum(res.histogram["counts"]) == 10_000

    def test_base_case_matches_deterministic(self):
        det = calculate_lcoh(LCOHInputs()).lcoh_after_policy_usd_per_kg
        res = run_monte_carlo(_spec(n_runs=5_000))
        assert res.base_case_usd_per_kg == pytest.approx(det, rel=1e-6)

    def test_base_case_within_distribution(self):
        res = run_monte_carlo(_spec(n_runs=20_000))
        assert res.percentiles["P5"] <= res.base_case_usd_per_kg <= res.percentiles["P95"]

    def test_percentiles_monotonic(self):
        res = run_monte_carlo(_spec(n_runs=10_000))
        p = res.percentiles
        assert p["P5"] <= p["P10"] <= p["P25"] <= p["P50"] <= p["P75"] <= p["P90"] <= p["P95"]

    def test_mean_near_base_for_symmetric(self):
        # Symmetric normal draws -> mean close to deterministic base case.
        res = run_monte_carlo(_spec(n_runs=50_000))
        assert res.mean == pytest.approx(res.base_case_usd_per_kg, rel=0.03)

    def test_reproducible_with_seed(self):
        a = run_monte_carlo(_spec(n_runs=5_000))
        b = run_monte_carlo(_spec(n_runs=5_000))
        assert a.mean == b.mean and a.percentiles == b.percentiles

    def test_wider_dispersion_increases_std(self):
        narrow = run_monte_carlo(_spec(n_runs=10_000,
                                       electricity_dist=Distribution("normal", std_pct=0.05)))
        wide = run_monte_carlo(_spec(n_runs=10_000,
                                     electricity_dist=Distribution("normal", std_pct=0.40)))
        assert wide.std > narrow.std

    def test_policy_shifts_distribution_down(self):
        no_policy = run_monte_carlo(_spec(n_runs=10_000)).mean
        with_policy = run_monte_carlo(
            MonteCarloSpec(base=LCOHInputs(), seed=42, n_runs=10_000,
                           policy=PolicyConfig(ira_45v_ptc=True))
        ).mean
        assert with_policy < no_policy

    def test_prob_below_threshold(self):
        res = run_monte_carlo(_spec(n_runs=20_000), thresholds=[2.0, 100.0])
        assert res.prob_below["100.0"] == pytest.approx(1.0, abs=1e-6)
        assert 0.0 <= res.prob_below["2.0"] <= 1.0

    def test_triangular_and_lognormal(self):
        for kind in ("triangular", "uniform", "lognormal"):
            res = run_monte_carlo(_spec(n_runs=5_000,
                                        capex_dist=Distribution(kind)))
            assert np.isfinite(res.mean)

    def test_too_few_runs_raises(self):
        with pytest.raises(ValueError):
            run_monte_carlo(_spec(n_runs=10))

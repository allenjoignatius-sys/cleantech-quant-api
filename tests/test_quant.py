"""
Unit tests for the techno-economic engine (LCOH + policy, commodities, carbon).
These are pure-Python and require no database or network.
"""
import pytest

from app.quant.lcoh import (
    LCOHInputs, PolicyConfig, calculate_lcoh, levelized_lcoh,
    capital_recovery_factor, POLICY_CATALOG,
)
from app.quant.commodities import derive_commodity_cost, derive_all, COMMODITY_SPECS
from app.quant.carbon import carbon_abatement, carbon_cost_per_kg_h2, EMISSION_FACTORS_KG_CO2_PER_KG_H2


# ── LCOH core ───────────────────────────────────────────────────────────────
class TestLCOH:
    def test_components_sum_to_gross(self):
        res = calculate_lcoh(LCOHInputs())
        assert res.lcoh_usd_per_kg == pytest.approx(sum(res.components.values()), rel=1e-9)

    def test_lcoh_in_realistic_range(self):
        # A 2024-ish base case should land in a sane $/kg band.
        res = calculate_lcoh(LCOHInputs())
        assert 2.0 < res.lcoh_usd_per_kg < 12.0

    def test_electricity_dominates_when_power_expensive(self):
        cheap = calculate_lcoh(LCOHInputs(electricity_price_usd_per_mwh=10))
        dear = calculate_lcoh(LCOHInputs(electricity_price_usd_per_mwh=120))
        assert dear.components["electricity"] > cheap.components["electricity"]
        assert dear.lcoh_usd_per_kg > cheap.lcoh_usd_per_kg

    def test_higher_capacity_factor_lowers_capex_per_kg(self):
        low = calculate_lcoh(LCOHInputs(capacity_factor=0.3))
        high = calculate_lcoh(LCOHInputs(capacity_factor=0.9))
        assert high.components["capex"] < low.components["capex"]

    def test_crf_known_value(self):
        # 8% over 20y annuity factor ≈ 0.10185
        assert capital_recovery_factor(0.08, 20) == pytest.approx(0.10185, abs=1e-4)
        assert capital_recovery_factor(0.0, 10) == pytest.approx(0.1)

    def test_invalid_inputs_raise(self):
        with pytest.raises(ValueError):
            LCOHInputs(capacity_factor=0)
        with pytest.raises(ValueError):
            LCOHInputs(efficiency_kwh_per_kg=0)


# ── Policy / subsidy engine ──────────────────────────────────────────────────
class TestPolicyEngine:
    def test_45v_reduces_net_lcoh(self):
        base = calculate_lcoh(LCOHInputs())
        with_ptc = calculate_lcoh(LCOHInputs(), PolicyConfig(ira_45v_ptc=True))
        assert with_ptc.lcoh_after_policy_usd_per_kg < base.lcoh_after_policy_usd_per_kg
        # gross (pre-policy) unchanged by a production credit
        assert with_ptc.lcoh_usd_per_kg == pytest.approx(base.lcoh_usd_per_kg)

    def test_45v_levelised_below_nominal(self):
        # $3/kg for 10y of a 20y plant must levelise to < $3/kg.
        res = calculate_lcoh(LCOHInputs(), PolicyConfig(ira_45v_ptc=True))
        adj = res.policy_adjustments["ira_45v_ptc"]
        assert -3.0 < adj < 0.0
        assert abs(adj) == pytest.approx(2.1, abs=0.4)  # ~ $2.0-2.2/kg levelised

    def test_itc_reduces_capex_component(self):
        base = calculate_lcoh(LCOHInputs())
        with_itc = calculate_lcoh(LCOHInputs(), PolicyConfig(ira_itc=True, ira_itc_pct=0.30))
        assert with_itc.components["capex"] < base.components["capex"]
        # ITC is folded into the gross number (it changes CAPEX), so gross drops too
        assert with_itc.lcoh_usd_per_kg < base.lcoh_usd_per_kg

    def test_eu_hydrogen_bank_premium(self):
        res = calculate_lcoh(LCOHInputs(), PolicyConfig(eu_hydrogen_bank=True,
                                                        eu_hydrogen_bank_premium_eur_per_kg=0.40))
        assert res.policy_adjustments["eu_hydrogen_bank"] < 0
        assert res.lcoh_after_policy_usd_per_kg < res.lcoh_usd_per_kg

    def test_stacked_policies(self):
        net = levelized_lcoh(LCOHInputs(), PolicyConfig(ira_45v_ptc=True, eu_hydrogen_bank=True))
        gross = levelized_lcoh(LCOHInputs())
        assert net < gross

    def test_catalog_shape(self):
        ids = {p["id"] for p in POLICY_CATALOG}
        assert {"ira_45v_ptc", "ira_itc", "eu_hydrogen_bank"} <= ids
        for p in POLICY_CATALOG:
            assert p["citation"]

    def test_to_dict_roundtrip(self):
        d = calculate_lcoh(LCOHInputs(), PolicyConfig(ira_45v_ptc=True)).to_dict()
        assert "components" in d and "policy_adjustments" in d
        assert isinstance(d["lcoh_after_policy_usd_per_kg"], float)


# ── Commodities ──────────────────────────────────────────────────────────────
class TestCommodities:
    @pytest.mark.parametrize("cid", ["ammonia", "methanol", "saf"])
    def test_breakdown_sums(self, cid):
        r = derive_commodity_cost(4.0, cid, co2_feedstock_usd_per_tonne=50.0)
        assert r.cost_usd_per_tonne == pytest.approx(sum(r.breakdown_usd_per_tonne.values()))
        assert r.cost_usd_per_kg == pytest.approx(r.cost_usd_per_tonne / 1000.0)
        assert r.cost_usd_per_tonne > 0

    def test_higher_lcoh_raises_commodity_cost(self):
        lo = derive_commodity_cost(2.0, "ammonia").cost_usd_per_tonne
        hi = derive_commodity_cost(8.0, "ammonia").cost_usd_per_tonne
        assert hi > lo

    def test_ammonia_ignores_co2(self):
        with_co2 = derive_commodity_cost(4.0, "ammonia", co2_feedstock_usd_per_tonne=200.0)
        assert with_co2.breakdown_usd_per_tonne["co2_feedstock"] == 0.0

    def test_saf_is_h2_intensive(self):
        # SAF needs far more H2 per tonne than ammonia
        assert COMMODITY_SPECS["saf"].h2_tonnes_per_tonne > COMMODITY_SPECS["ammonia"].h2_tonnes_per_tonne

    def test_derive_all(self):
        results = derive_all(4.5, co2_feedstock_usd_per_tonne=60.0)
        assert {r.commodity_id for r in results} == set(COMMODITY_SPECS)

    def test_unknown_commodity_raises(self):
        with pytest.raises(ValueError):
            derive_commodity_cost(4.0, "kerosene")


# ── Carbon market ─────────────────────────────────────────────────────────────
class TestCarbon:
    def test_abatement_positive_vs_grey(self):
        r = carbon_abatement(carbon_price_eur_per_tonne=80.0, reference="grey")
        assert r.abatement_value_usd_per_kg_h2 > 0
        # equals (grey - green) * price * fx / 1000
        expected = (9.5 - 0.5) * (80.0 * 1.08) / 1000.0
        assert r.abatement_value_usd_per_kg_h2 == pytest.approx(expected, rel=1e-6)

    def test_annual_scaling(self):
        r = carbon_abatement(carbon_price_eur_per_tonne=80.0, annual_h2_tonnes=10_000)
        assert r.annual_abatement_value_usd == pytest.approx(
            r.abatement_value_usd_per_kg_h2 * 10_000 * 1000.0, rel=1e-9
        )
        assert r.annual_abatement_tonnes_co2 > 0

    def test_carbon_cost_helper(self):
        assert carbon_cost_per_kg_h2(9.5, 100.0, 1.0) == pytest.approx(0.95)

    def test_blue_reference_lower_abatement(self):
        grey = carbon_abatement(80.0, reference="grey").abatement_value_usd_per_kg_h2
        blue = carbon_abatement(80.0, reference="blue").abatement_value_usd_per_kg_h2
        assert blue < grey

    def test_invalid_reference(self):
        with pytest.raises(ValueError):
            carbon_abatement(80.0, reference="purple")

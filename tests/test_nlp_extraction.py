"""Unit tests for the resilient NLP/LLM extraction pipeline (no DB/network)."""
import json

import pytest

from app.nlp.extraction import (
    normalize_text, QuantityParser, RuleBasedExtractor, ResilientExtractor,
    SourceRef, extract_catalyst_metrics, extract_fid_signals,
)


def _field_map(result):
    return {v.field: v for v in result.values}


class TestQuantityGrammar:
    def test_range_midpoint(self):
        qs = QuantityParser().find("overpotential of 250-300 mV", r"m?V")
        assert len(qs) == 1
        assert qs[0].value == pytest.approx(275.0)
        assert qs[0].is_range

    def test_unicode_minus_and_superscript(self):
        raw = "current density of 10 mA cm⁻²"   # mA cm⁻²
        norm = normalize_text(raw)
        assert "cm-2" in norm
        qs = QuantityParser().find(norm, r"(?:mA|A)\s*[\/ ]?\s*cm-?2")
        assert qs and qs[0].value == pytest.approx(10.0)


class TestCatalystExtraction:
    SRC = SourceRef(url="https://doi.org/10.1/x", doi="10.1/x", source_type="academic_paper")

    def test_overpotential_mv(self):
        r = extract_catalyst_metrics("A low overpotential of 280 mV was achieved.", self.SRC)
        fm = _field_map(r)
        assert "overpotential_mv" in fm
        assert fm["overpotential_mv"].value == pytest.approx(280.0)

    def test_overpotential_volts_converted(self):
        r = extract_catalyst_metrics("The overpotential η = 0.30 V vs RHE.", self.SRC)
        fm = _field_map(r)
        assert fm["overpotential_mv"].value == pytest.approx(300.0)
        assert fm["overpotential_mv"].unit == "mV"

    def test_current_density_and_efficiency(self):
        text = "Faradaic efficiency of 95% at a current density of 10 mA/cm2."
        r = extract_catalyst_metrics(text, self.SRC)
        fm = _field_map(r)
        assert fm["faradaic_efficiency_pct"].value == pytest.approx(95.0)
        assert fm["current_density_ma_cm2"].value == pytest.approx(10.0)

    def test_nh3_conversion(self):
        r = extract_catalyst_metrics("NH3 conversion reached 99.1% over the Ru catalyst.", self.SRC)
        fm = _field_map(r)
        assert fm["nh3_conversion_pct"].value == pytest.approx(99.1)

    def test_durability_hours(self):
        r = extract_catalyst_metrics("Stable operation for 1000 h with no decay.", self.SRC)
        fm = _field_map(r)
        assert fm["durability_h"].value == pytest.approx(1000.0)

    def test_provenance_offsets_valid(self):
        text = "An overpotential of 280 mV was recorded."
        r = extract_catalyst_metrics(text, self.SRC)
        norm = normalize_text(text)
        for v in r.values:
            assert norm[v.start:v.end] == v.raw_text
            assert v.source.url == "https://doi.org/10.1/x"
            assert v.source.doi == "10.1/x"

    def test_unanchored_percent_dropped(self):
        # A stray percentage with no metric anchor should not be mis-extracted.
        r = extract_catalyst_metrics("Sales grew 95% year over year.", self.SRC)
        assert "faradaic_efficiency_pct" not in _field_map(r)

    def test_implausible_filtered(self):
        # 99999 mV overpotential is implausible -> filtered out
        r = extract_catalyst_metrics("overpotential of 99999 mV", self.SRC)
        assert "overpotential_mv" not in _field_map(r)


class TestFIDExtraction:
    SRC = SourceRef(url="https://news.example/x", source_type="news")

    def test_fid_capacity_and_investment(self):
        text = ("Developer reached a final investment decision (FID) on its 200 MW "
                "green hydrogen plant, a $500 million project.")
        r = extract_fid_signals(text, self.SRC)
        fm = _field_map(r)
        assert r.flags["is_fid_related"] is True
        assert fm["capacity_mw"].value == pytest.approx(200.0)
        assert fm["investment_usd_millions"].value == pytest.approx(500.0)

    def test_gw_to_mw(self):
        r = extract_fid_signals("A 1.2 GW electrolyzer was sanctioned.", self.SRC)
        fm = _field_map(r)
        assert fm["capacity_mw"].value == pytest.approx(1200.0)
        assert r.flags["is_fid_related"] is True

    def test_billion_investment(self):
        r = extract_fid_signals("The $2.5 billion facility took FID today.", self.SRC)
        fm = _field_map(r)
        assert fm["investment_usd_millions"].value == pytest.approx(2500.0)

    def test_not_fid(self):
        r = extract_fid_signals("Company announced plans to study a 50 MW concept.", self.SRC)
        assert r.flags["is_fid_related"] is False


class TestLLMBackend:
    SRC = SourceRef(url="https://doi.org/10.2/y")

    def _client(self, payload):
        return lambda prompt: json.dumps(payload)

    def test_llm_path_used_and_offsets_recovered(self):
        text = "Catalyst showed an overpotential of 240 mV at high rates."
        client = self._client({
            "values": [{"field": "overpotential_mv", "value": 240, "unit": "mV",
                        "quote": "overpotential of 240 mV", "confidence": 0.95}],
            "is_fid_related": False,
        })
        ext = ResilientExtractor(llm_client=client)
        r = ext.extract_catalyst_metrics(text, self.SRC)
        fm = _field_map(r)
        assert "overpotential_mv" in fm
        v = fm["overpotential_mv"]
        # offsets recovered from the verbatim quote
        assert text[v.start:v.end] == "overpotential of 240 mV"
        # both backends agreed on this field
        assert v.method == "llm+rule"
        assert "llm" in r.methods_used

    def test_llm_failure_falls_back_to_rules(self):
        def broken(_prompt):
            raise RuntimeError("llm down")
        ext = ResilientExtractor(llm_client=broken)
        r = ext.extract_catalyst_metrics("overpotential of 280 mV", self.SRC)
        assert "overpotential_mv" in _field_map(r)   # rules still deliver

    def test_no_client_is_rule_only(self):
        ext = ResilientExtractor(llm_client=None)
        assert ext.llm.available is False
        r = ext.extract_fid_signals("Reached FID on a 300 MW plant.", self.SRC)
        assert r.flags["is_fid_related"] is True
        assert r.methods_used == ["rule"]

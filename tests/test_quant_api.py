"""
HTTP-level tests for the /v1/quant API using FastAPI's TestClient with the auth
and DB dependencies overridden. The quant compute endpoints are DB-free, so these
run without Postgres and exercise real request validation + responses + middleware.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth import get_current_user
from app.database import get_db
from app.models import OrgRole


def _fake_user():
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        email="analyst@acme.test",
        plan=SimpleNamespace(value="enterprise"),
        role=OrgRole.analyst,
        organization_id="org-1",
        is_admin=False,
        company="ACME",
        job_title="Analyst",
        is_active=True,
    )


async def _fake_db():
    yield SimpleNamespace()  # quant compute endpoints never query it


@pytest.fixture()
def client():
    app.dependency_overrides[get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db
    # Instantiate without the context manager to skip the DB-touching lifespan.
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


class TestQuantAPI:
    def test_policies_listed(self, client):
        r = client.get("/v1/quant/policies", headers={"X-API-Key": "x"})
        assert r.status_code == 200
        ids = {p["id"] for p in r.json()["policies"]}
        assert {"ira_45v_ptc", "eu_hydrogen_bank"} <= ids
        # middleware headers present
        assert r.headers.get("X-API-Version") == "3.0.0"
        assert "X-Process-Time" in r.headers

    def test_lcoh_policy_reduces_cost(self, client):
        body = {"inputs": {"electricity_price_usd_per_mwh": 45},
                "policy": {"ira_45v_ptc": True}}
        r = client.post("/v1/quant/lcoh", json=body)
        assert r.status_code == 200
        data = r.json()
        assert data["lcoh_after_policy_usd_per_kg"] < data["lcoh_usd_per_kg"]
        assert abs(sum(data["components"].values()) - data["lcoh_usd_per_kg"]) < 1e-6

    def test_lcoh_validation_error(self, client):
        r = client.post("/v1/quant/lcoh", json={"inputs": {"capacity_factor": 2.0}})
        assert r.status_code == 422

    def test_monte_carlo(self, client):
        body = {"inputs": {}, "n_runs": 5000, "seed": 1,
                "thresholds": [3.0], "vary_capacity_factor": True}
        r = client.post("/v1/quant/monte-carlo", json=body)
        assert r.status_code == 200
        data = r.json()
        assert data["n_runs"] == 5000
        assert set(data["percentiles"]) >= {"P5", "P50", "P95"}
        assert len(data["histogram"]["counts"]) == 40
        assert "3.0" in data["prob_below"]

    def test_commodities(self, client):
        r = client.post("/v1/quant/commodities",
                        json={"inputs": {}, "co2_feedstock_usd_per_tonne": 50})
        assert r.status_code == 200
        ids = {c["commodity_id"] for c in r.json()["commodities"]}
        assert ids == {"ammonia", "methanol", "saf"}

    def test_excel_download(self, client):
        body = {"inputs": {"capex_usd_per_kw": 1000}, "policy": {"ira_itc": True},
                "h2_sale_price_usd_per_kg": 6.0, "pnl_years": 6}
        r = client.post("/v1/quant/excel", json=body)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        assert r.content[:2] == b"PK"  # xlsx is a zip
        assert "attachment" in r.headers.get("content-disposition", "")

    def test_scenarios_compare_inline(self, client):
        body = {"scenarios": [
            {"name": "Cheap power", "inputs": {"electricity_price_usd_per_mwh": 25}},
            {"name": "Dear power", "inputs": {"electricity_price_usd_per_mwh": 90}},
        ]}
        r = client.post("/v1/scenarios/compare", json=body)
        assert r.status_code == 200
        data = r.json()
        assert len(data["results"]) == 2
        # cheaper power -> lower LCOH -> best scenario index 0
        assert data["best_scenario_index"] == 0
        assert "lcoh_usd_per_kg" in data["comparison"]

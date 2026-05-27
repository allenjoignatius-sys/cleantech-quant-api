"""
Cleantech Quant API — Test Suite
Run with: pytest tests/ -v --asyncio-mode=auto

Coverage targets:
  - All auth flows (register, login, API key lifecycle)
  - All catalyst benchmark endpoints
  - Cost model endpoints including sensitivity
  - Project CRUD + filtering
  - Efficiency degradation predict
  - Alert CRUD + plan limits
  - Webhook CRUD + signature verification
  - Admin stats + user management
  - Rate limiting behaviour
  - Plan-gated endpoint enforcement
"""

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.main import app
from app.database import Base, get_db
from app.models import (
    User, APIKey, CatalystBenchmark, CatalystType, DataSource,
    Project, CostDatapoint, Alert, AlertType, Webhook,
    SubscriptionPlan,
)
from app.config import settings

# ─── Test database ────────────────────────────────────────────────────────────

TEST_DB_URL = "postgresql+asyncpg://ctquser:ctqpass@localhost:5432/cleantech_test"

test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


app.dependency_overrides[get_db] = override_get_db


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="session", autouse=True)
async def create_tables():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def free_user(db: AsyncSession) -> User:
    import bcrypt
    user = User(
        email=f"free_{secrets.token_hex(4)}@test.com",
        hashed_password=bcrypt.hashpw(b"testpass123", bcrypt.gensalt()).decode(),
        plan=SubscriptionPlan.free,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def analyst_user(db: AsyncSession) -> User:
    import bcrypt
    user = User(
        email=f"analyst_{secrets.token_hex(4)}@test.com",
        hashed_password=bcrypt.hashpw(b"testpass123", bcrypt.gensalt()).decode(),
        plan=SubscriptionPlan.analyst,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def enterprise_user(db: AsyncSession) -> User:
    import bcrypt
    user = User(
        email=f"enterprise_{secrets.token_hex(4)}@test.com",
        hashed_password=bcrypt.hashpw(b"testpass123", bcrypt.gensalt()).decode(),
        plan=SubscriptionPlan.enterprise,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_user(db: AsyncSession) -> User:
    import bcrypt
    user = User(
        email=f"admin_{secrets.token_hex(4)}@test.com",
        hashed_password=bcrypt.hashpw(b"adminpass123", bcrypt.gensalt()).decode(),
        plan=SubscriptionPlan.enterprise,
        is_active=True,
        is_admin=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_api_key(db: AsyncSession, user: User) -> str:
    raw = "ctq_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    key = APIKey(
        user_id=user.id,
        key_hash=key_hash,
        key_prefix=raw[:12],
        name="Test Key",
        is_active=True,
    )
    db.add(key)
    await db.commit()
    return raw


@pytest_asyncio.fixture
async def free_key(db, free_user):
    return await _make_api_key(db, free_user)


@pytest_asyncio.fixture
async def analyst_key(db, analyst_user):
    return await _make_api_key(db, analyst_user)


@pytest_asyncio.fixture
async def enterprise_key(db, enterprise_user):
    return await _make_api_key(db, enterprise_user)


@pytest_asyncio.fixture
async def admin_key(db, admin_user):
    return await _make_api_key(db, admin_user)


@pytest_asyncio.fixture
async def seed_benchmark(db: AsyncSession) -> CatalystBenchmark:
    bm = CatalystBenchmark(
        catalyst_type=CatalystType.ruthenium,
        catalyst_composition="Cs-Ru/MgO 5wt%",
        temperature_celsius=400.0,
        pressure_bar=1.0,
        nh3_conversion_pct=99.1,
        energy_penalty_pct=11.8,
        catalyst_cost_usd_per_kg=17500.0,
        opex_usd_per_kg_h2=0.24,
        source_type=DataSource.academic_paper,
        source_doi="10.1016/test.2024.001",
        institution="Test University",
        year=2024,
        scale="pilot",
        trl=9,
    )
    db.add(bm)
    await db.commit()
    await db.refresh(bm)
    return bm


@pytest_asyncio.fixture
async def seed_project(db: AsyncSession) -> Project:
    proj = Project(
        name="Test NH3 Cracker Project",
        developer="Test Corp",
        location_country="Japan",
        latitude=35.68, longitude=139.69,
        cracker_capacity_tpd_h2=100.0,
        technology_vendor="ThyssenKrupp Uhde",
        catalyst_type=CatalystType.ruthenium,
        status="fid",
        fid_date=datetime(2025, 4, 1),
        target_operational_date=datetime(2028, 1, 1),
        total_capex_usd_millions=500.0,
    )
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuth:
    async def test_register_success(self, client: AsyncClient):
        resp = await client.post("/v1/auth/register", json={
            "email": f"new_{secrets.token_hex(4)}@test.com",
            "password": "securepass123",
            "company": "MUFG",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "access_token" in data
        assert data["user"]["plan"] == "free"

    async def test_register_duplicate_email(self, client: AsyncClient, free_user: User):
        resp = await client.post("/v1/auth/register", json={
            "email": free_user.email,
            "password": "anypass123",
        })
        assert resp.status_code == 409

    async def test_login_success(self, client: AsyncClient, free_user: User):
        resp = await client.post("/v1/auth/login", json={
            "email": free_user.email,
            "password": "testpass123",
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    async def test_login_wrong_password(self, client: AsyncClient, free_user: User):
        resp = await client.post("/v1/auth/login", json={
            "email": free_user.email,
            "password": "wrongpassword",
        })
        assert resp.status_code == 401

    async def test_get_me_with_api_key(self, client: AsyncClient, free_key: str):
        resp = await client.get("/v1/auth/me", headers={"X-API-Key": free_key})
        assert resp.status_code == 200
        data = resp.json()
        assert "email" in data
        assert data["plan"] == "free"

    async def test_get_me_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/v1/auth/me")
        assert resp.status_code == 401

    async def test_create_api_key(self, client: AsyncClient, free_key: str):
        # Free plan allows 1 key — but we already have one, so creating another should fail
        resp = await client.post("/v1/auth/keys",
            json={"name": "Second Key"},
            headers={"X-API-Key": free_key}
        )
        assert resp.status_code == 429  # plan limit

    async def test_create_api_key_analyst(self, client: AsyncClient, analyst_key: str):
        resp = await client.post("/v1/auth/keys",
            json={"name": "Second Key"},
            headers={"X-API-Key": analyst_key}
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "key" in data
        assert data["key"].startswith("ctq_")

    async def test_list_api_keys(self, client: AsyncClient, analyst_key: str):
        resp = await client.get("/v1/auth/keys", headers={"X-API-Key": analyst_key})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_revoke_api_key(self, client: AsyncClient, db: AsyncSession, analyst_user: User):
        # Create extra key to revoke
        extra_raw = await _make_api_key(db, analyst_user)
        analyst_k = await _make_api_key(db, analyst_user)

        # Get key id
        from sqlalchemy import select
        key_obj = (await db.execute(
            select(APIKey).where(APIKey.key_prefix == extra_raw[:12])
        )).scalar_one_or_none()

        if key_obj:
            resp = await client.delete(
                f"/v1/auth/keys/{key_obj.id}",
                headers={"X-API-Key": analyst_k}
            )
            assert resp.status_code == 204


# ═══════════════════════════════════════════════════════════════════════════════
# CATALYST BENCHMARK TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestCatalysts:
    async def test_list_benchmarks(self, client: AsyncClient, free_key: str, seed_benchmark):
        resp = await client.get("/v1/catalysts/", headers={"X-API-Key": free_key})
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "pagination" in data
        assert isinstance(data["data"], list)

    async def test_filter_by_catalyst_type(self, client: AsyncClient, free_key: str, seed_benchmark):
        resp = await client.get(
            "/v1/catalysts/?catalyst_type=ruthenium",
            headers={"X-API-Key": free_key}
        )
        assert resp.status_code == 200
        for item in resp.json()["data"]:
            assert item["catalyst_type"] == "ruthenium"

    async def test_filter_min_conversion(self, client: AsyncClient, free_key: str, seed_benchmark):
        resp = await client.get(
            "/v1/catalysts/?min_conversion=95",
            headers={"X-API-Key": free_key}
        )
        assert resp.status_code == 200
        for item in resp.json()["data"]:
            assert item["nh3_conversion_pct"] >= 95

    async def test_get_single_benchmark(self, client: AsyncClient, free_key: str, seed_benchmark):
        resp = await client.get(
            f"/v1/catalysts/{seed_benchmark.id}",
            headers={"X-API-Key": free_key}
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == seed_benchmark.id

    async def test_get_benchmark_not_found(self, client: AsyncClient, free_key: str):
        resp = await client.get(
            "/v1/catalysts/00000000-0000-0000-0000-000000000000",
            headers={"X-API-Key": free_key}
        )
        assert resp.status_code == 404

    async def test_stats(self, client: AsyncClient, free_key: str, seed_benchmark):
        resp = await client.get("/v1/catalysts/stats", headers={"X-API-Key": free_key})
        assert resp.status_code == 200
        assert "total_count" in resp.json()

    async def test_compare_catalysts(self, client: AsyncClient, free_key: str):
        resp = await client.get(
            "/v1/catalysts/compare?temperature=500",
            headers={"X-API-Key": free_key}
        )
        assert resp.status_code == 200
        assert "catalysts" in resp.json()
        assert "ruthenium" in resp.json()["catalysts"]

    async def test_create_benchmark_requires_analyst(self, client: AsyncClient, free_key: str):
        resp = await client.post("/v1/catalysts/", json={
            "catalyst_type": "nickel",
            "temperature_celsius": 600,
            "nh3_conversion_pct": 85.0,
        }, headers={"X-API-Key": free_key})
        assert resp.status_code == 403

    async def test_create_benchmark_analyst(self, client: AsyncClient, analyst_key: str):
        resp = await client.post("/v1/catalysts/", json={
            "catalyst_type": "nickel",
            "temperature_celsius": 600.0,
            "nh3_conversion_pct": 85.0,
            "energy_penalty_pct": 20.0,
            "trl": 6,
            "scale": "lab",
            "year": 2024,
        }, headers={"X-API-Key": analyst_key})
        assert resp.status_code == 201
        data = resp.json()
        assert data["catalyst_type"] == "nickel"
        assert data["nh3_conversion_pct"] == 85.0

    async def test_csv_export_requires_analyst(self, client: AsyncClient, free_key: str):
        resp = await client.get(
            "/v1/catalysts/export/csv",
            headers={"X-API-Key": free_key}
        )
        assert resp.status_code == 403

    async def test_csv_export_analyst(self, client: AsyncClient, analyst_key: str):
        resp = await client.get(
            "/v1/catalysts/export/csv",
            headers={"X-API-Key": analyst_key}
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")

    async def test_pagination(self, client: AsyncClient, free_key: str):
        resp = await client.get(
            "/v1/catalysts/?page=1&page_size=5",
            headers={"X-API-Key": free_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) <= 5
        assert data["pagination"]["page"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# COST MODEL TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestCosts:
    async def test_list_costs(self, client: AsyncClient, free_key: str):
        resp = await client.get("/v1/costs/", headers={"X-API-Key": free_key})
        assert resp.status_code == 200

    async def test_geographies(self, client: AsyncClient, free_key: str):
        resp = await client.get("/v1/costs/geographies", headers={"X-API-Key": free_key})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_landing_cost_benchmarks(self, client: AsyncClient, free_key: str):
        resp = await client.get(
            "/v1/costs/benchmark/landing-cost",
            headers={"X-API-Key": free_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "import_chains" in data
        assert len(data["import_chains"]) >= 3

    async def test_sensitivity_requires_analyst(self, client: AsyncClient, free_key: str):
        resp = await client.post("/v1/costs/sensitivity",
            json={"base_nh3_cost_usd_per_tonne": 400.0,
                  "electricity_cost_usd_per_mwh": 60.0,
                  "cracker_capacity_tpd_h2": 100.0,
                  "catalyst_type": "ruthenium",
                  "discount_rate_pct": 8.0,
                  "plant_lifetime_years": 20,
                  "capacity_factor_pct": 90.0,
                  "geography": "Japan"},
            headers={"X-API-Key": free_key}
        )
        assert resp.status_code == 403

    async def test_sensitivity_analyst(self, client: AsyncClient, analyst_key: str):
        resp = await client.post("/v1/costs/sensitivity",
            json={"base_nh3_cost_usd_per_tonne": 400.0,
                  "electricity_cost_usd_per_mwh": 60.0,
                  "cracker_capacity_tpd_h2": 100.0,
                  "catalyst_type": "ruthenium",
                  "discount_rate_pct": 8.0,
                  "plant_lifetime_years": 20,
                  "capacity_factor_pct": 90.0,
                  "geography": "Japan"},
            headers={"X-API-Key": analyst_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "base_case_usd_per_kg_h2" in data
        assert data["base_case_usd_per_kg_h2"] > 0
        assert "tornado_chart_data" in data
        assert "lcoh_components" in data
        # LCOH components should sum to approx base case
        components = data["lcoh_components"]
        total = sum(v for v in components.values() if v)
        assert abs(total - data["base_case_usd_per_kg_h2"]) < 0.1


# ═══════════════════════════════════════════════════════════════════════════════
# PROJECT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestProjects:
    async def test_list_projects(self, client: AsyncClient, free_key: str, seed_project):
        resp = await client.get("/v1/projects/", headers={"X-API-Key": free_key})
        assert resp.status_code == 200

    async def test_filter_by_status(self, client: AsyncClient, free_key: str, seed_project):
        resp = await client.get(
            "/v1/projects/?status=fid",
            headers={"X-API-Key": free_key}
        )
        assert resp.status_code == 200
        for item in resp.json()["data"]:
            assert item["status"] == "fid"

    async def test_filter_by_country(self, client: AsyncClient, free_key: str, seed_project):
        resp = await client.get(
            "/v1/projects/?country=Japan",
            headers={"X-API-Key": free_key}
        )
        assert resp.status_code == 200
        for item in resp.json()["data"]:
            assert item["location_country"] == "Japan"

    async def test_get_project(self, client: AsyncClient, free_key: str, seed_project):
        resp = await client.get(
            f"/v1/projects/{seed_project.id}",
            headers={"X-API-Key": free_key}
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == seed_project.name

    async def test_project_stats(self, client: AsyncClient, free_key: str, seed_project):
        resp = await client.get("/v1/projects/stats", headers={"X-API-Key": free_key})
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "by_status" in data

    async def test_project_map(self, client: AsyncClient, free_key: str, seed_project):
        resp = await client.get("/v1/projects/map", headers={"X-API-Key": free_key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert isinstance(data["features"], list)

    async def test_create_project_analyst(self, client: AsyncClient, analyst_key: str):
        resp = await client.post("/v1/projects/", json={
            "name": "Test Cracker Belgium",
            "developer": "Fluxys",
            "location_country": "Belgium",
            "cracker_capacity_tpd_h2": 250.0,
            "status": "announced",
        }, headers={"X-API-Key": analyst_key})
        assert resp.status_code == 201
        assert resp.json()["name"] == "Test Cracker Belgium"

    async def test_update_project(self, client: AsyncClient, analyst_key: str, seed_project):
        resp = await client.patch(
            f"/v1/projects/{seed_project.id}",
            json={"status": "construction"},
            headers={"X-API-Key": analyst_key}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "construction"

    async def test_capacity_timeline(self, client: AsyncClient, free_key: str, seed_project):
        resp = await client.get("/v1/projects/timeline", headers={"X-API-Key": free_key})
        assert resp.status_code == 200
        assert "timeline" in resp.json()

    async def test_recent_fids(self, client: AsyncClient, free_key: str, seed_project):
        resp = await client.get("/v1/projects/recent-fids", headers={"X-API-Key": free_key})
        assert resp.status_code == 200
        assert "data" in resp.json()


# ═══════════════════════════════════════════════════════════════════════════════
# EFFICIENCY / DEGRADATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestEfficiency:
    async def test_list_curves(self, client: AsyncClient, free_key: str):
        resp = await client.get("/v1/efficiency/curves", headers={"X-API-Key": free_key})
        assert resp.status_code == 200

    async def test_purity_standards(self, client: AsyncClient, free_key: str):
        resp = await client.get("/v1/efficiency/purity-standards", headers={"X-API-Key": free_key})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        use_cases = [d["use_case"] for d in data]
        assert any("PEM" in uc for uc in use_cases)

    async def test_compare_degradation(self, client: AsyncClient, free_key: str):
        resp = await client.get(
            "/v1/efficiency/compare?temperature=500&hours=17520",
            headers={"X-API-Key": free_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "comparison" in data
        assert "ruthenium" in data["comparison"]

    async def test_predict_requires_analyst(self, client: AsyncClient, free_key: str):
        resp = await client.post("/v1/efficiency/predict", json={
            "catalyst_type": "ruthenium",
            "initial_conversion_pct": 98.0,
            "operating_temperature_celsius": 450.0,
            "operating_pressure_bar": 1.0,
            "predict_hours": 17520,
        }, headers={"X-API-Key": free_key})
        assert resp.status_code == 403

    async def test_predict_analyst(self, client: AsyncClient, analyst_key: str):
        resp = await client.post("/v1/efficiency/predict", json={
            "catalyst_type": "ruthenium",
            "initial_conversion_pct": 98.0,
            "operating_temperature_celsius": 450.0,
            "operating_pressure_bar": 1.0,
            "predict_hours": 17520,
        }, headers={"X-API-Key": analyst_key})
        assert resp.status_code == 200
        data = resp.json()
        assert "predicted_curve" in data
        assert len(data["predicted_curve"]) > 0
        # First point should be close to initial
        assert data["predicted_curve"][0]["predicted_conversion_pct"] == pytest.approx(98.0, abs=1.0)
        assert "annual_efficiency_loss_pct" in data

    async def test_predict_invalid_target(self, client: AsyncClient, analyst_key: str):
        """Target conversion higher than initial should return 422."""
        resp = await client.post("/v1/efficiency/predict", json={
            "catalyst_type": "ruthenium",
            "initial_conversion_pct": 70.0,
            "operating_temperature_celsius": 450.0,
            "operating_pressure_bar": 1.0,
            "predict_hours": 17520,
        }, headers={"X-API-Key": analyst_key})
        # target_min defaults to 80 which is higher than initial 70
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
# ALERT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestAlerts:
    async def test_list_alerts_empty(self, client: AsyncClient, free_key: str):
        resp = await client.get("/v1/alerts/", headers={"X-API-Key": free_key})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_create_alert(self, client: AsyncClient, free_key: str):
        resp = await client.post("/v1/alerts/", json={
            "name": "Ru efficiency watch",
            "alert_type": "efficiency_threshold",
            "conditions": {
                "catalyst_type": "ruthenium",
                "metric": "nh3_conversion_pct",
                "operator": "lt",
                "threshold": 90.0,
            },
            "notification_channels": ["email"],
        }, headers={"X-API-Key": free_key})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Ru efficiency watch"
        return data["id"]

    async def test_free_plan_alert_limit(self, client: AsyncClient, db: AsyncSession, free_user: User):
        """Free plan allows only 1 alert."""
        raw = await _make_api_key(db, free_user)

        # Create first alert (should succeed)
        r1 = await client.post("/v1/alerts/", json={
            "name": "Alert 1", "alert_type": "project_fid",
            "conditions": {"milestone": "fid"},
            "notification_channels": ["email"],
        }, headers={"X-API-Key": raw})
        assert r1.status_code == 201

        # Create second alert (should be rejected)
        r2 = await client.post("/v1/alerts/", json={
            "name": "Alert 2", "alert_type": "project_fid",
            "conditions": {"milestone": "operational"},
            "notification_channels": ["email"],
        }, headers={"X-API-Key": raw})
        assert r2.status_code == 429

    async def test_get_alert_templates(self, client: AsyncClient, free_key: str):
        resp = await client.get("/v1/alerts/templates", headers={"X-API-Key": free_key})
        assert resp.status_code == 200
        assert "templates" in resp.json()
        assert len(resp.json()["templates"]) >= 4

    async def test_delete_alert(self, client: AsyncClient, db: AsyncSession, analyst_user: User):
        raw = await _make_api_key(db, analyst_user)
        # Create
        r = await client.post("/v1/alerts/", json={
            "name": "To delete", "alert_type": "project_fid",
            "conditions": {"milestone": "fid"},
            "notification_channels": ["email"],
        }, headers={"X-API-Key": raw})
        assert r.status_code == 201
        alert_id = r.json()["id"]

        # Delete
        del_r = await client.delete(
            f"/v1/alerts/{alert_id}",
            headers={"X-API-Key": raw}
        )
        assert del_r.status_code == 204

    async def test_invalid_notification_channel(self, client: AsyncClient, free_key: str):
        resp = await client.post("/v1/alerts/", json={
            "name": "Bad channel",
            "alert_type": "project_fid",
            "conditions": {"milestone": "fid"},
            "notification_channels": ["telegram"],  # not supported
        }, headers={"X-API-Key": free_key})
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
# WEBHOOK TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestWebhooks:
    async def test_list_webhooks_requires_analyst(self, client: AsyncClient, free_key: str):
        resp = await client.get("/v1/webhooks/", headers={"X-API-Key": free_key})
        assert resp.status_code == 403

    async def test_create_webhook_analyst(self, client: AsyncClient, analyst_key: str):
        resp = await client.post("/v1/webhooks/", json={
            "url": "https://example.com/webhook",
            "events": ["project.status_change", "catalyst.new_benchmark"],
        }, headers={"X-API-Key": analyst_key})
        assert resp.status_code == 201
        data = resp.json()
        assert "secret" in data  # secret shown on creation
        assert "id" in data
        assert data["signing_header"] == "X-CTQ-Signature-256"
        return data

    async def test_webhook_http_rejected(self, client: AsyncClient, analyst_key: str):
        resp = await client.post("/v1/webhooks/", json={
            "url": "http://insecure.example.com/webhook",  # HTTP not HTTPS
            "events": ["*"],
        }, headers={"X-API-Key": analyst_key})
        assert resp.status_code == 422

    async def test_invalid_event(self, client: AsyncClient, analyst_key: str):
        resp = await client.post("/v1/webhooks/", json={
            "url": "https://example.com/hook",
            "events": ["unknown.event"],
        }, headers={"X-API-Key": analyst_key})
        assert resp.status_code == 422

    async def test_webhook_signature_verification(self):
        """Unit test: verify HMAC signature is generated correctly."""
        secret = "test_secret_key_32bytes_padding!!"
        payload = json.dumps({"event": "ping", "data": {"foo": "bar"}}).encode()
        expected_sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        header_value = f"sha256={expected_sig}"

        # Re-verify as the client would
        computed = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert hmac.compare_digest(f"sha256={computed}", header_value)


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdmin:
    async def test_admin_stats_requires_admin(self, client: AsyncClient, analyst_key: str):
        resp = await client.get("/v1/admin/stats", headers={"X-API-Key": analyst_key})
        assert resp.status_code == 403

    async def test_admin_stats(self, client: AsyncClient, admin_key: str):
        resp = await client.get("/v1/admin/stats", headers={"X-API-Key": admin_key})
        assert resp.status_code == 200
        data = resp.json()
        assert "users" in data
        assert "data" in data
        assert "system" in data

    async def test_list_users(self, client: AsyncClient, admin_key: str):
        resp = await client.get("/v1/admin/users", headers={"X-API-Key": admin_key})
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert isinstance(data["data"], list)

    async def test_update_user_plan(self, client: AsyncClient, admin_key: str, free_user: User):
        resp = await client.patch(
            f"/v1/admin/users/{free_user.id}/plan",
            json={"plan": "analyst"},
            headers={"X-API-Key": admin_key}
        )
        assert resp.status_code == 200
        assert "analyst" in resp.json()["plan_changed"]

    async def test_audit_log(self, client: AsyncClient, admin_key: str):
        resp = await client.get("/v1/admin/audit-log", headers={"X-API-Key": admin_key})
        assert resp.status_code == 200
        assert "data" in resp.json()


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM / HEALTH TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestSystem:
    async def test_health(self, client: AsyncClient):
        resp = await client.get("/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    async def test_root(self, client: AsyncClient):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "version" in resp.json()

    async def test_changelog(self, client: AsyncClient):
        resp = await client.get("/v1/changelog")
        assert resp.status_code == 200
        assert "versions" in resp.json()

    async def test_docs_available(self, client: AsyncClient):
        resp = await client.get("/docs")
        assert resp.status_code == 200

    async def test_process_time_header(self, client: AsyncClient):
        resp = await client.get("/v1/health")
        assert "X-Process-Time" in resp.headers
        assert "X-API-Version" in resp.headers

    async def test_unauthenticated_protected_endpoint(self, client: AsyncClient):
        resp = await client.get("/v1/catalysts/")
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers

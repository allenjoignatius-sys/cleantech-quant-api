"""
Tests that the literature/news services now use the resilient extraction pipeline,
and that the RBAC role hierarchy + dependency behave correctly. No DB required.
"""
import pytest
from types import SimpleNamespace

from app.services.literature_service import LiteratureService
from app.services.news_service import NewsService
from app.schemas.news import NewsItem
from app.models import OrgRole
from app.rbac import role_at_least, require_role, require_org_member
from fastapi import HTTPException


# ── Service extraction (regex replaced by NLP pipeline) ───────────────────────
class TestServiceExtraction:
    def test_literature_extracts_performance(self):
        svc = LiteratureService()
        pd = svc.extract_performance(
            "The catalyst showed an overpotential of 230 mV and a Faradaic "
            "efficiency of 96% with stable operation for 500 h.",
            url="https://doi.org/10.9/z", doi="10.9/z",
        )
        assert pd.overpotential_mv == pytest.approx(230.0)
        assert pd.faradaic_efficiency_pct == pytest.approx(96.0)
        assert pd.durability_h == pytest.approx(500.0)

    def test_literature_empty_abstract(self):
        pd = LiteratureService().extract_performance("")
        assert pd.overpotential_mv is None

    def test_news_analyze_detects_fid(self):
        alert = NewsService().analyze(
            "Company took a final investment decision on its 250 MW plant.",
            url="https://news.example/a", title="FID reached",
        )
        assert alert.is_confirmed_fid is True
        assert alert.detected_capacity_mw == pytest.approx(250.0)

    def test_news_classify_item(self):
        item = NewsItem(source="rss", title="Feasibility study launched",
                        url="https://x.test", summary="early-stage concept")
        out = NewsService().classify_item(item)
        assert out.is_fid_related is False


# ── RBAC ──────────────────────────────────────────────────────────────────────
class TestRBAC:
    def test_role_hierarchy(self):
        assert role_at_least(OrgRole.owner, OrgRole.admin)
        assert role_at_least(OrgRole.admin, OrgRole.analyst)
        assert role_at_least(OrgRole.analyst, OrgRole.analyst)
        assert not role_at_least(OrgRole.viewer, OrgRole.analyst)
        assert not role_at_least(None, OrgRole.viewer)

    def test_role_accepts_string(self):
        assert role_at_least("admin", OrgRole.analyst)

    async def test_require_role_allows_sufficient(self):
        checker = require_role(OrgRole.analyst)
        user = SimpleNamespace(is_admin=False, organization_id="o1", role=OrgRole.admin)
        assert await checker(current_user=user) is user

    async def test_require_role_blocks_insufficient(self):
        checker = require_role(OrgRole.admin)
        user = SimpleNamespace(is_admin=False, organization_id="o1", role=OrgRole.analyst)
        with pytest.raises(HTTPException) as exc:
            await checker(current_user=user)
        assert exc.value.status_code == 403

    async def test_require_role_blocks_no_org(self):
        checker = require_role(OrgRole.viewer)
        user = SimpleNamespace(is_admin=False, organization_id=None, role=OrgRole.viewer)
        with pytest.raises(HTTPException):
            await checker(current_user=user)

    async def test_platform_admin_bypasses(self):
        checker = require_role(OrgRole.owner)
        user = SimpleNamespace(is_admin=True, organization_id=None, role=OrgRole.viewer)
        assert await checker(current_user=user) is user

    async def test_require_org_member(self):
        ok = SimpleNamespace(is_admin=False, organization_id="o1")
        assert await require_org_member(current_user=ok) is ok
        with pytest.raises(HTTPException):
            await require_org_member(current_user=SimpleNamespace(is_admin=False, organization_id=None))

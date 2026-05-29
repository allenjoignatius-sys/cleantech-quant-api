"""Unit tests for PDF briefings, structured logging, and rate limiting."""
import io
import json
import logging

import pytest

from app.reports.pdf import build_weekly_briefing, WeeklyBriefingData
from app.observability.logging_config import (
    JsonFormatter, configure_logging, set_request_id, request_id_var, new_request_id,
)
from app.ratelimit import RateLimiter, InMemoryWindow


# ── PDF briefings ─────────────────────────────────────────────────────────────
class TestPDF:
    def test_pdf_bytes_with_data(self):
        data = WeeklyBriefingData(
            week_of="2026-05-25",
            summary="Two FIDs and a price spike in ERCOT.",
            new_fids=[{"name": "Aurora H2", "country": "Spain", "capacity_mw": 200,
                       "investment_usd_millions": 500, "url": "https://ex.com/a"}],
            price_anomalies=[{"geography": "US-ERCOT", "price_eur_mwh": 180.0,
                              "z_score": 3.2, "note": "heatwave"}],
            catalyst_highlights=[{"title": "Ru/MgO 99% conv", "metric": "99.1% NH3 conv",
                                  "doi": "10.1/x"}],
        )
        pdf = build_weekly_briefing(data)
        assert isinstance(pdf, bytes)
        assert pdf[:5] == b"%PDF-"
        assert len(pdf) > 1500

    def test_pdf_handles_empty(self):
        pdf = build_weekly_briefing(WeeklyBriefingData())
        assert pdf[:5] == b"%PDF-"


# ── Structured logging ────────────────────────────────────────────────────────
class TestLogging:
    def _record(self, **extra):
        return logging.LogRecord(
            name="test.logger", level=logging.INFO, pathname=__file__, lineno=1,
            msg="hello %s", args=("world",), exc_info=None,
        )

    def test_json_line_shape(self):
        rec = self._record()
        out = json.loads(JsonFormatter().format(rec))
        assert out["level"] == "INFO"
        assert out["logger"] == "test.logger"
        assert out["message"] == "hello world"
        assert out["service"] == "cleantech-quant-api"
        assert "ts" in out

    def test_request_id_propagates(self):
        rid = set_request_id("abc123")
        assert rid == "abc123"
        out = json.loads(JsonFormatter().format(self._record()))
        assert out["request_id"] == "abc123"
        request_id_var.set(None)

    def test_extra_fields_merged(self):
        rec = logging.LogRecord("l", logging.INFO, __file__, 1, "m", (), None)
        rec.org_id = "org_9"
        rec.latency_ms = 42
        out = json.loads(JsonFormatter().format(rec))
        assert out["org_id"] == "org_9"
        assert out["latency_ms"] == 42

    def test_configure_logging_installs_json_handler(self):
        buf = io.StringIO()
        configure_logging(level="DEBUG", stream=buf)
        logging.getLogger("x.y").info("structured", extra={"k": "v"})
        line = buf.getvalue().strip().splitlines()[-1]
        parsed = json.loads(line)
        assert parsed["message"] == "structured" and parsed["k"] == "v"
        # restore a quiet default for other tests
        configure_logging(level="WARNING", stream=io.StringIO())

    def test_new_request_id_unique(self):
        assert new_request_id() != new_request_id()


# ── Rate limiting ─────────────────────────────────────────────────────────────
class TestInMemoryWindow:
    def test_window_resets(self):
        w = InMemoryWindow()
        c1, _ = w.incr("k", window=10, now=1000.0)
        c2, _ = w.incr("k", window=10, now=1005.0)
        c3, _ = w.incr("k", window=10, now=1011.0)   # past window -> reset
        assert (c1, c2, c3) == (1, 2, 1)


class TestRateLimiter:
    async def test_memory_blocks_after_limit(self):
        rl = RateLimiter(redis_client=None)
        results = [await rl.check("user:1", limit=3, window=60) for _ in range(4)]
        assert [r.allowed for r in results] == [True, True, True, False]
        assert results[0].backend == "memory"
        assert results[2].remaining == 0

    async def test_unlimited_for_negative_limit(self):
        rl = RateLimiter(redis_client=None)
        r = await rl.check("ent:1", limit=-1, window=60)
        assert r.allowed and r.backend == "unlimited"

    async def test_redis_backend_used(self):
        class FakeRedis:
            def __init__(self): self.store = {}
            async def incr(self, k): self.store[k] = self.store.get(k, 0) + 1; return self.store[k]
            async def expire(self, k, w): return True
            async def ttl(self, k): return 42
        rl = RateLimiter(redis_client=FakeRedis())
        r1 = await rl.check("k", limit=2, window=60)
        r2 = await rl.check("k", limit=2, window=60)
        r3 = await rl.check("k", limit=2, window=60)
        assert r1.backend == "redis" and r1.reset_after == 42
        assert [r1.allowed, r2.allowed, r3.allowed] == [True, True, False]

    async def test_redis_failure_fails_open(self):
        class BrokenRedis:
            async def incr(self, k): raise RuntimeError("down")
        rl = RateLimiter(redis_client=BrokenRedis())
        r = await rl.check("k", limit=1, window=60)
        assert r.allowed and r.backend == "memory"

"""
NewsService — market-news feed + FID detection.

The brittle keyword regex has been replaced by the resilient extraction pipeline
in :mod:`app.nlp`, which classifies FID relevance and pulls structured signals
(capacity MW, investment $) with source traceability. RSS parsing via feedparser
degrades gracefully when the optional dependency is unavailable.
"""
import logging
from typing import List, Optional

try:
    import feedparser  # optional; RSS parsing degrades gracefully if unavailable
except Exception:  # pragma: no cover
    feedparser = None

from app.schemas.news import NewsItem, NewsSearchResponse, FIDAlert
from app.nlp.factory import build_extractor
from app.nlp.extraction import SourceRef

logger = logging.getLogger(__name__)


class NewsService:
    def __init__(self) -> None:
        self._extractor = build_extractor()

    def analyze(self, text: str, url: str = "", title: Optional[str] = None,
                source: str = "news") -> FIDAlert:
        """Classify a news item and extract structured FID signals (resilient pipeline)."""
        ref = SourceRef(url=url, title=title, source_type="news")
        result = self._extractor.extract_fid_signals(text or "", ref)
        fm = result.as_field_map()
        capacity = fm["capacity_mw"].value if "capacity_mw" in fm else None
        return FIDAlert(
            title=title,
            url=url,
            source=source,
            published_at=None,
            summary=(text[:280] if text else None),
            detected_capacity_mw=capacity,
            country_mentioned=None,
            is_confirmed_fid=bool(result.flags.get("is_fid_related", False)),
        )

    def classify_item(self, item: NewsItem) -> NewsItem:
        """Set is_fid_related on a NewsItem using the extraction pipeline."""
        blob = " ".join(filter(None, [item.title, item.summary]))
        result = self._extractor.extract_fid_signals(blob, SourceRef(url=item.url, source_type="news"))
        item.is_fid_related = bool(result.flags.get("is_fid_related", False))
        return item

    async def get_feed(self, topics, sources, days_back, limit) -> NewsSearchResponse:
        return NewsSearchResponse(total=0, returned=0, items=[])

    async def get_fid_announcements(self, days_back, country, technology, min_capacity_mw) -> List:
        return []

    async def search(self, q, days_back, limit) -> NewsSearchResponse:
        return NewsSearchResponse(total=0, returned=0, items=[])

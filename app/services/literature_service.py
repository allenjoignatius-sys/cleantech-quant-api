"""
LiteratureService — academic-literature search + performance extraction.

The fragile regex parsing has been replaced by the resilient NER/LLM pipeline in
:mod:`app.nlp`. ``extract_performance`` parses an abstract into structured,
source-traceable metrics; the network scraping of PubMed/arXiv/DOE remains a
pluggable seam (returns empty until feed credentials are configured).
"""
import logging
from typing import List, Optional

from app.schemas.literature import (
    LiteratureSearchResponse, LiteratureResult, PerformanceData,
)
from app.nlp.factory import build_extractor
from app.nlp.extraction import SourceRef

logger = logging.getLogger(__name__)


class LiteratureService:
    def __init__(self) -> None:
        self._extractor = build_extractor()

    def extract_performance(
        self,
        text: str,
        url: Optional[str] = None,
        doi: Optional[str] = None,
        title: Optional[str] = None,
    ) -> PerformanceData:
        """Parse an abstract into structured performance metrics (resilient pipeline)."""
        source = SourceRef(url=url, doi=doi, title=title, source_type="academic_paper")
        result = self._extractor.extract_catalyst_metrics(text or "", source)
        fm = result.as_field_map()

        def val(field: str) -> Optional[float]:
            return fm[field].value if field in fm else None

        return PerformanceData(
            overpotential_mv=val("overpotential_mv"),
            current_density_ma_cm2=val("current_density_ma_cm2"),
            faradaic_efficiency_pct=val("faradaic_efficiency_pct"),
            tof_s=val("tof_s"),
            durability_h=val("durability_h"),
        )

    def enrich_result(self, result: LiteratureResult) -> LiteratureResult:
        """Attach extracted performance data to a literature result, if it has an abstract."""
        if result.abstract:
            result.performance_data = self.extract_performance(
                result.abstract, url=result.url, doi=result.doi, title=result.title
            )
        return result

    async def search(self, query, sources, max_results, year_from, year_to,
                     extract_performance) -> LiteratureSearchResponse:
        # Network scraping seam — returns empty until feeds are wired; any results
        # produced here would be passed through `enrich_result` for extraction.
        return LiteratureSearchResponse(
            query=query, total=0, sources_searched=sources, results=[]
        )

    async def get_cached_benchmarks(self, catalyst_type, reaction, limit) -> List:
        return []

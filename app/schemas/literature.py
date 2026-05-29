"""
Schemas for Literature Scraping feature.
"""
from pydantic import BaseModel, Field
from typing import Optional, List


class PerformanceData(BaseModel):
    """Extracted performance metrics from an academic paper abstract."""
    overpotential_mv: Optional[float] = Field(None, description="Overpotential in millivolts (η, mV)")
    current_density_ma_cm2: Optional[float] = Field(None, description="Current density in mA/cm²")
    faradaic_efficiency_pct: Optional[float] = Field(None, description="Faradaic efficiency in %")
    tof_s: Optional[float] = Field(None, description="Turnover frequency in s⁻¹")
    durability_h: Optional[float] = Field(None, description="Stability / durability in hours")


class LiteratureResult(BaseModel):
    """A single literature result with optional extracted performance data."""
    source: str = Field(..., description="Source: pubmed | arxiv | doe")
    id: str = Field(..., description="Source-specific ID (PMID, arXiv ID, OSTI ID)")
    title: str
    abstract: Optional[str] = None
    authors: List[str] = Field(default_factory=list)
    journal: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    url: str
    performance_data: Optional[PerformanceData] = None


class LiteratureSearchRequest(BaseModel):
    query: str
    sources: List[str] = ["pubmed", "arxiv", "doe"]
    max_results: int = 20
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    extract_performance: bool = True


class LiteratureSearchResponse(BaseModel):
    query: str
    total: int
    sources_searched: List[str]
    results: List[LiteratureResult]
    errors: Optional[List[str]] = None

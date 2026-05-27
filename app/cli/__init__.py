"""Scraper sub-package — engine + runner."""
from app.scrapers.engine import (
    CrossrefScraper,
    PatentScraper,
    IRENAScraper,
    ProjectIntelligenceScraper,
    MasterScraper,
    ScraperConfig,
)

__all__ = [
    "CrossrefScraper",
    "PatentScraper",
    "IRENAScraper",
    "ProjectIntelligenceScraper",
    "MasterScraper",
    "ScraperConfig",
]

"""
Cleantech Quant Research — Scraper Engine
Collects catalyst benchmarks, project data, and cost figures from:
  - ScienceDirect / Crossref (academic papers)
  - Google Patents / EPO / JPO
  - IRENA, IEA, DOE public databases
  - Company investor relations pages
  - Regulatory databases (FERC, EU ENTSO-G)
"""

import asyncio
import aiohttp
import re
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Any
from bs4 import BeautifulSoup
import pandas as pd

logger = logging.getLogger(__name__)


class ScraperConfig:
    USER_AGENT = "CleanTechQuantResearch/1.0 (research@cleantechquant.io)"
    REQUEST_DELAY = 2.0       # seconds between requests (polite scraping)
    MAX_RETRIES = 3
    TIMEOUT = 30
    HEADERS = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/html",
        "Accept-Language": "en-US,en;q=0.9",
    }


class CrossrefScraper:
    """
    Scrapes academic literature for NH3 cracking efficiency data.
    Uses the Crossref REST API (free, no auth required).
    DOI metadata + abstract text extraction.
    """

    BASE_URL = "https://api.crossref.org/works"

    SEARCH_QUERIES = [
        "ammonia cracking efficiency ruthenium catalyst",
        "ammonia decomposition nickel catalyst benchmark",
        "hydrogen carrier ammonia cracking energy penalty",
        "NH3 cracking conversion rate temperature pressure",
        "ammonia to hydrogen conversion cost techno-economic",
        "ammonia cracker pilot demonstration scale",
    ]

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def search(self, query: str, rows: int = 20, offset: int = 0) -> dict:
        params = {
            "query": query,
            "rows": rows,
            "offset": offset,
            "filter": "from-pub-date:2018",
            "select": "DOI,title,abstract,author,published,publisher,container-title,URL",
            "mailto": "research@cleantechquant.io",
        }
        try:
            async with self.session.get(
                self.BASE_URL, params=params, timeout=ScraperConfig.TIMEOUT
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.error(f"Crossref search error: {e}")
        return {}

    async def extract_benchmark_data(self, item: dict) -> Optional[dict]:
        """
        Parse an abstract for catalyst performance numbers.
        Returns structured benchmark dict or None if no data found.
        """
        abstract = item.get("abstract", "")
        title = " ".join(item.get("title", []))

        # Check relevance
        keywords = ["ammonia cracking", "NH3 decomposition", "catalyst", "conversion"]
        if not any(kw.lower() in (abstract + title).lower() for kw in keywords):
            return None

        # Extract numbers with regex
        conversion = self._extract_number(abstract, r"(\d+\.?\d*)\s*%.*?conversion")
        temperature = self._extract_number(abstract, r"(\d+)\s*°?C")
        pressure = self._extract_number(abstract, r"(\d+\.?\d*)\s*bar")
        energy_penalty = self._extract_number(abstract, r"(\d+\.?\d*)\s*%.*?energy")

        catalyst_type = self._identify_catalyst(abstract + title)
        if not catalyst_type:
            return None

        pub_date = item.get("published", {}).get("date-parts", [[None]])[0]
        year = pub_date[0] if pub_date else None

        return {
            "source_type": "academic_paper",
            "source_doi": item.get("DOI"),
            "source_url": item.get("URL"),
            "institution": item.get("publisher"),
            "year": year,
            "catalyst_type": catalyst_type,
            "nh3_conversion_pct": conversion,
            "temperature_celsius": temperature,
            "pressure_bar": pressure,
            "energy_penalty_pct": energy_penalty,
            "raw_data": {
                "title": title,
                "abstract": abstract[:500],
                "journal": item.get("container-title", [None])[0],
            },
        }

    def _extract_number(self, text: str, pattern: str) -> Optional[float]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return None

    def _identify_catalyst(self, text: str) -> Optional[str]:
        text_lower = text.lower()
        if "ruthenium" in text_lower or " ru " in text_lower:
            if "nickel" in text_lower or " ni " in text_lower:
                return "ni_ru_bimetallic"
            return "ruthenium"
        if "nickel" in text_lower or " ni " in text_lower:
            return "nickel"
        if "iron" in text_lower or " fe " in text_lower:
            return "iron"
        if "cobalt" in text_lower or " co " in text_lower:
            return "cobalt"
        return None

    async def scrape_all(self) -> list[dict]:
        results = []
        for query in self.SEARCH_QUERIES:
            await asyncio.sleep(ScraperConfig.REQUEST_DELAY)
            data = await self.search(query)
            items = data.get("message", {}).get("items", [])
            for item in items:
                benchmark = await self.extract_benchmark_data(item)
                if benchmark:
                    results.append(benchmark)
                    logger.info(f"Extracted benchmark: {benchmark.get('catalyst_type')} @ {benchmark.get('temperature_celsius')}°C")
        logger.info(f"Crossref scraper: {len(results)} benchmarks extracted")
        return results


class PatentScraper:
    """
    Scrapes patent databases for NH3 cracking technology filings.
    Sources: Google Patents (via SerpAPI or direct), EPO OPS API, JPO J-PlatPat.
    """

    EPO_OPS_URL = "https://ops.epo.org/3.2/rest-services"
    IPC_CODES = ["C01B3/04", "C01C1/02", "B01J23/89"]  # relevant IPC codes

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def search_epo(self, ipc_code: str, date_range: str = "2018-2025") -> list[dict]:
        """
        Query EPO Open Patent Services API (free, registration required).
        Returns list of patent metadata dicts.
        """
        query = f"ipc={ipc_code} AND pd within \"{date_range}\""
        try:
            async with self.session.get(
                f"{self.EPO_OPS_URL}/published-data/search/biblio",
                params={"q": query, "Range": "1-100"},
                headers={"Accept": "application/json", **ScraperConfig.HEADERS},
                timeout=ScraperConfig.TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.error(f"EPO search error for {ipc_code}: {e}")
        return []

    async def scrape_all(self) -> list[dict]:
        patents = []
        for ipc in self.IPC_CODES:
            await asyncio.sleep(ScraperConfig.REQUEST_DELAY)
            results = await self.search_epo(ipc)
            patents.extend(results)
        logger.info(f"Patent scraper: {len(patents)} patents found")
        return patents


class IRENAScraper:
    """
    Scrapes IRENA (International Renewable Energy Agency) public data on H2 costs.
    IRENA releases annual hydrogen cost reports with cost curves by geography.
    """

    IRENA_BASE = "https://www.irena.org/Publications"

    KNOWN_REPORT_URLS = [
        "https://www.irena.org/Publications/2023/Dec/Green-hydrogen-cost-reduction",
        "https://www.irena.org/Publications/2022/Jan/Global-Hydrogen-Trade-to-Meet-the-1-5C-Climate-Goal",
    ]

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def fetch_cost_data(self) -> list[dict]:
        """
        Parse IRENA hydrogen cost CSV/Excel datasets where publicly available.
        Returns list of cost datapoint dicts.
        """
        cost_datapoints = []
        for url in self.KNOWN_REPORT_URLS:
            try:
                await asyncio.sleep(ScraperConfig.REQUEST_DELAY)
                async with self.session.get(url, headers=ScraperConfig.HEADERS) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        soup = BeautifulSoup(html, "html.parser")
                        # Look for downloadable data files
                        data_links = soup.find_all("a", href=re.compile(r"\.(xlsx|csv|xls)$"))
                        for link in data_links:
                            href = link.get("href")
                            logger.info(f"Found data file: {href}")
                            # Queue for download and parsing
                            cost_datapoints.extend(
                                await self._parse_irena_file(href)
                            )
            except Exception as e:
                logger.error(f"IRENA scrape error {url}: {e}")
        return cost_datapoints

    async def _parse_irena_file(self, file_url: str) -> list[dict]:
        """Download and parse IRENA Excel/CSV data file."""
        try:
            async with self.session.get(file_url) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    if file_url.endswith(".csv"):
                        df = pd.read_csv(pd.io.common.BytesIO(content))
                    else:
                        df = pd.read_excel(pd.io.common.BytesIO(content))
                    return self._parse_dataframe(df, source_url=file_url)
        except Exception as e:
            logger.error(f"File parse error {file_url}: {e}")
        return []

    def _parse_dataframe(self, df: pd.DataFrame, source_url: str) -> list[dict]:
        """Convert a raw IRENA DataFrame to cost datapoints."""
        results = []
        # Column name normalization (IRENA changes column names between versions)
        col_map = {}
        for col in df.columns:
            col_lower = col.lower()
            if "country" in col_lower or "region" in col_lower:
                col_map["geography"] = col
            if "cost" in col_lower and "usd" in col_lower:
                col_map["total_delivered_h2_cost"] = col
            if "year" in col_lower:
                col_map["year"] = col

        for _, row in df.iterrows():
            try:
                dp = {
                    "source_type": "regulatory_filing",
                    "source_url": source_url,
                    "geography": str(row.get(col_map.get("geography", ""), "Unknown")),
                    "year": int(row.get(col_map.get("year", ""), 2024)),
                    "total_delivered_h2_cost": float(row.get(col_map.get("total_delivered_h2_cost", ""), 0)),
                }
                if dp["total_delivered_h2_cost"] > 0:
                    results.append(dp)
            except (ValueError, TypeError):
                continue
        return results


class ProjectIntelligenceScraper:
    """
    Monitors company press releases, investor pages, and news for project FIDs.
    Target companies: JERA, Air Liquide, Uniper, Topsoe, KBR, Amogy, ThyssenKrupp Uhde.
    """

    COMPANY_FEEDS = {
        "JERA": "https://www.jera.co.jp/en/news/rss",
        "Air Liquide": "https://www.airliquide.com/rss/news.xml",
        "Topsoe": "https://www.topsoe.com/rss",
        "Amogy": "https://amogy.co/feed/",
    }

    PROJECT_KEYWORDS = [
        "ammonia cracking", "cracker", "FID", "final investment decision",
        "hydrogen import", "green ammonia", "commissioning", "operational"
    ]

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def monitor_feeds(self) -> list[dict]:
        projects = []
        for company, rss_url in self.COMPANY_FEEDS.items():
            try:
                await asyncio.sleep(ScraperConfig.REQUEST_DELAY)
                async with self.session.get(rss_url, headers=ScraperConfig.HEADERS) as resp:
                    if resp.status == 200:
                        xml = await resp.text()
                        soup = BeautifulSoup(xml, "xml")
                        for item in soup.find_all("item")[:20]:
                            title = item.find("title").text if item.find("title") else ""
                            desc = item.find("description").text if item.find("description") else ""
                            content = (title + desc).lower()
                            if any(kw.lower() in content for kw in self.PROJECT_KEYWORDS):
                                projects.append({
                                    "developer": company,
                                    "name": title,
                                    "announcement_url": item.find("link").text if item.find("link") else None,
                                    "status": self._classify_status(content),
                                    "source_type": "company_disclosure",
                                })
            except Exception as e:
                logger.error(f"RSS feed error {company}: {e}")
        return projects

    def _classify_status(self, text: str) -> str:
        if "operational" in text or "commissioned" in text:
            return "operational"
        if "fid" in text or "final investment" in text:
            return "fid"
        if "construction" in text or "breaking ground" in text:
            return "construction"
        return "announced"


class MasterScraper:
    """
    Orchestrates all scrapers. Runs on a schedule (every 6 hours).
    Deduplicates, validates, and upserts data into the database.
    """

    def __init__(self, db_session):
        self.db = db_session

    async def run_full_scrape(self):
        logger.info("Starting master scrape cycle...")
        start = datetime.utcnow()

        async with aiohttp.ClientSession(headers=ScraperConfig.HEADERS) as session:
            # Run all scrapers concurrently where safe
            tasks = [
                CrossrefScraper(session).scrape_all(),
                PatentScraper(session).scrape_all(),
                IRENAScraper(session).fetch_cost_data(),
                ProjectIntelligenceScraper(session).monitor_feeds(),
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        benchmarks, patents, cost_data, projects = results

        # Persist to DB (simplified — real impl uses upsert logic)
        stats = {
            "benchmarks_found": len(benchmarks) if isinstance(benchmarks, list) else 0,
            "patents_found": len(patents) if isinstance(patents, list) else 0,
            "cost_datapoints_found": len(cost_data) if isinstance(cost_data, list) else 0,
            "projects_found": len(projects) if isinstance(projects, list) else 0,
            "duration_seconds": (datetime.utcnow() - start).total_seconds(),
            "completed_at": datetime.utcnow().isoformat(),
        }
        logger.info(f"Scrape cycle complete: {stats}")
        return stats

    async def schedule_recurring(self, interval_hours: int = 6):
        """Run forever, scraping every N hours."""
        while True:
            try:
                await self.run_full_scrape()
            except Exception as e:
                logger.error(f"Scrape cycle failed: {e}")
            await asyncio.sleep(interval_hours * 3600)

from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional, List
from app.services.literature_service import LiteratureService
from app.schemas.literature import LiteratureSearchRequest, LiteratureResult, LiteratureSearchResponse
# FIXED
from app.auth import get_current_user, require_plan
from app.models import User

router = APIRouter(prefix="/v1/literature", tags=["Literature Scraping"])

@router.get("/search", response_model=LiteratureSearchResponse)
async def search_literature(
    query: str = Query(...), sources: List[str] = Query(default=["pubmed", "arxiv", "doe"]),
    max_results: int = Query(default=20), year_from: Optional[int] = Query(default=None), year_to: Optional[int] = Query(default=None),
    extract_performance: bool = Query(default=True), current_user: User = Depends(get_current_user)
):
    service = LiteratureService()
    return await service.search(query=query, sources=sources, max_results=max_results, year_from=year_from, year_to=year_to, extract_performance=extract_performance)

@router.get("/benchmarks/latest", response_model=List[LiteratureResult])
async def get_latest_benchmarks(catalyst_type: Optional[str] = Query(default=None), reaction: Optional[str] = Query(default=None), limit: int = Query(default=10), current_user: User = Depends(get_current_user)):
    service = LiteratureService()
    return await service.get_cached_benchmarks(catalyst_type=catalyst_type, reaction=reaction, limit=limit)

@router.post("/refresh")
async def trigger_refresh(sources: List[str] = Query(default=["pubmed", "arxiv", "doe"]), current_user: User = Depends(require_plan(["analyst", "enterprise"]))):
    # FIXED: app.workers.tasks -> app.tasks
    from app.tasks import scrape_literature_task
    task = scrape_literature_task.delay(sources=sources)
    return {"task_id": task.id, "status": "queued", "message": f"Scraping {sources}"}

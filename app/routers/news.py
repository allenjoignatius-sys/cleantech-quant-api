from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional, List
from app.services.news_service import NewsService
from app.schemas.news import NewsItem, NewsSearchResponse, FIDAlert
# FIXED
from app.auth import get_current_user, require_plan
from app.models import User

router = APIRouter(prefix="/v1/news", tags=["News & FID Announcements"])

@router.get("/feed", response_model=NewsSearchResponse)
async def get_news_feed(topics: List[str] = Query(default=["hydrogen", "electrolyzer", "FID"]), sources: List[str] = Query(default=["rss", "gdelt"]), days_back: int = Query(default=7), limit: int = Query(default=20), current_user: User = Depends(get_current_user)):
    service = NewsService()
    return await service.get_feed(topics=topics, sources=sources, days_back=days_back, limit=limit)

@router.get("/fid-announcements", response_model=List[FIDAlert])
async def get_fid_announcements(days_back: int = Query(default=30), country: Optional[str] = Query(default=None), technology: Optional[str] = Query(default=None), min_capacity_mw: Optional[float] = Query(default=None), current_user: User = Depends(get_current_user)):
    service = NewsService()
    return await service.get_fid_announcements(days_back=days_back, country=country, technology=technology, min_capacity_mw=min_capacity_mw)

@router.post("/refresh")
async def trigger_news_refresh(current_user: User = Depends(require_plan(["analyst", "enterprise"]))):
    # FIXED: app.workers.tasks -> app.tasks
    from app.tasks import scrape_news_task
    task = scrape_news_task.delay()
    return {"task_id": task.id, "status": "queued"}

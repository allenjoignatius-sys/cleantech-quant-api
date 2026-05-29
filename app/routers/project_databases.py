from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional, List
from app.services.project_db_service import ProjectDatabaseService
from app.schemas.project_db import ProjectDBResult, ProjectDBSearchResponse, ProjectStats
# FIXED
from app.auth import get_current_user
from app.models import User

router = APIRouter(prefix="/v1/project-databases", tags=["Project Databases"])

@router.get("/search", response_model=ProjectDBSearchResponse)
async def search_projects(query: Optional[str] = Query(default=None), sources: List[str] = Query(default=["iea", "h2iq", "irena"]), page: int = Query(default=1), page_size: int = Query(default=20), current_user: User = Depends(get_current_user)):
    service = ProjectDatabaseService()
    return await service.search(query=query, country=None, technology=None, status=None, capacity_mw_min=None, capacity_mw_max=None, sources=sources, page=page, page_size=page_size)

@router.get("/{project_id}", response_model=ProjectDBResult)
async def get_project_detail(project_id: str, current_user: User = Depends(get_current_user)):
    service = ProjectDatabaseService()
    result = await service.get_by_id(project_id)
    if not result: raise HTTPException(status_code=404, detail="Not found")
    return result

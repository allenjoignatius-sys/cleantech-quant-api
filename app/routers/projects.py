"""
/v1/projects — Project Intelligence Endpoints
Tracks every announced, FEED, construction, and operational
ammonia cracking project globally. GeoJSON map + FID timeline.
"""

from fastapi import APIRouter, Depends, Query, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, or_
from typing import Optional, List
from pydantic import BaseModel, Field, HttpUrl
from datetime import datetime, date
import json

from app.database import get_db
from app.models import Project, CatalystType, User
from app.auth import get_current_user, require_plan

router = APIRouter()


# ─── Schemas ──────────────────────────────────────────────────────────────────

class ProjectOut(BaseModel):
    id: str
    name: str
    developer: Optional[str]
    location_country: Optional[str]
    location_city: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    cracker_capacity_tpd_h2: Optional[float]
    technology_vendor: Optional[str]
    catalyst_type: Optional[str]
    feedstock_source: Optional[str]
    status: Optional[str]
    announced_date: Optional[datetime]
    fid_date: Optional[datetime]
    target_operational_date: Optional[datetime]
    total_capex_usd_millions: Optional[float]
    financing_structure: Optional[str]
    offtaker: Optional[str]
    announcement_url: Optional[str]
    tags: Optional[list]
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class ProjectCreate(BaseModel):
    name: str = Field(..., max_length=255)
    developer: Optional[str] = None
    location_country: Optional[str] = None
    location_city: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    cracker_capacity_tpd_h2: Optional[float] = Field(None, ge=0)
    technology_vendor: Optional[str] = None
    catalyst_type: Optional[CatalystType] = None
    feedstock_source: Optional[str] = None
    status: Optional[str] = Field(
        None, description="announced | fid | construction | operational | cancelled"
    )
    target_operational_date: Optional[datetime] = None
    total_capex_usd_millions: Optional[float] = Field(None, ge=0)
    financing_structure: Optional[str] = None
    offtaker: Optional[str] = None
    announcement_url: Optional[str] = None
    tags: Optional[List[str]] = []


class ProjectUpdate(BaseModel):
    status: Optional[str] = None
    fid_date: Optional[datetime] = None
    construction_start: Optional[datetime] = None
    target_operational_date: Optional[datetime] = None
    total_capex_usd_millions: Optional[float] = None
    technology_vendor: Optional[str] = None
    notes: Optional[str] = None


class ProjectStats(BaseModel):
    total: int
    by_status: dict
    by_country: dict
    total_capacity_tpd_h2: float
    total_capex_usd_billions: float
    avg_capacity_tpd_h2: float


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get(
    "/",
    response_model=dict,
    summary="List all tracked cracking projects",
    description="""
    Global database of announced, FEED, construction, and operational
    ammonia cracking projects. Filterable by status, country, technology vendor,
    capacity range, and date range.

    **Data coverage**: JERA Blue Point, Air Liquide Antwerp, Uniper Germany,
    Amogy maritime projects, and 40+ additional tracked projects.
    """,
)
async def list_projects(
    # Filters
    status: Optional[List[str]] = Query(None, description="Filter by status"),
    country: Optional[List[str]] = Query(None),
    technology_vendor: Optional[str] = Query(None),
    catalyst_type: Optional[CatalystType] = Query(None),
    min_capacity_tpd: Optional[float] = Query(None, ge=0),
    max_capacity_tpd: Optional[float] = Query(None),
    fid_year: Optional[int] = Query(None, description="Filter to projects with FID in this year"),
    operational_by: Optional[int] = Query(None, description="Filter to projects operational by this year"),
    has_capex_data: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, description="Text search on name, developer, offtaker"),
    # Sorting & pagination
    sort_by: str = Query("announced_date", description="Sort field"),
    sort_dir: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(Project)
    conditions = []

    if status:
        conditions.append(Project.status.in_(status))
    if country:
        conditions.append(Project.location_country.in_(country))
    if technology_vendor:
        conditions.append(Project.technology_vendor.ilike(f"%{technology_vendor}%"))
    if catalyst_type:
        conditions.append(Project.catalyst_type == catalyst_type)
    if min_capacity_tpd is not None:
        conditions.append(Project.cracker_capacity_tpd_h2 >= min_capacity_tpd)
    if max_capacity_tpd is not None:
        conditions.append(Project.cracker_capacity_tpd_h2 <= max_capacity_tpd)
    if fid_year:
        conditions.append(
            and_(
                Project.fid_date >= datetime(fid_year, 1, 1),
                Project.fid_date < datetime(fid_year + 1, 1, 1),
            )
        )
    if operational_by:
        conditions.append(
            Project.target_operational_date <= datetime(operational_by, 12, 31)
        )
    if has_capex_data is True:
        conditions.append(Project.total_capex_usd_millions.isnot(None))
    if search:
        conditions.append(
            or_(
                Project.name.ilike(f"%{search}%"),
                Project.developer.ilike(f"%{search}%"),
                Project.offtaker.ilike(f"%{search}%"),
            )
        )

    if conditions:
        query = query.where(and_(*conditions))

    sort_col = getattr(Project, sort_by, Project.announced_date)
    query = query.order_by(sort_col.desc() if sort_dir == "desc" else sort_col.asc())

    total = (await db.execute(
        select(func.count()).select_from(query.subquery())
    )).scalar()

    query = query.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(query)).scalars().all()

    return {
        "data": [ProjectOut.from_orm(r) for r in rows],
        "pagination": {"page": page, "page_size": page_size, "total": total, "pages": -(-total // page_size)},
        "meta": {"filters_applied": len(conditions)},
    }


@router.get(
    "/stats",
    response_model=ProjectStats,
    summary="Aggregate project statistics",
)
async def project_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    total = (await db.execute(select(func.count(Project.id)))).scalar()

    # By status
    status_res = await db.execute(
        select(Project.status, func.count().label("n"))
        .group_by(Project.status)
    )
    by_status = {r.status or "unknown": r.n for r in status_res}

    # By country
    country_res = await db.execute(
        select(Project.location_country, func.count().label("n"))
        .group_by(Project.location_country)
        .order_by(func.count().desc())
        .limit(15)
    )
    by_country = {r.location_country or "unknown": r.n for r in country_res}

    # Aggregates
    agg = (await db.execute(
        select(
            func.sum(Project.cracker_capacity_tpd_h2).label("total_cap"),
            func.sum(Project.total_capex_usd_millions).label("total_capex"),
            func.avg(Project.cracker_capacity_tpd_h2).label("avg_cap"),
        )
    )).first()

    return ProjectStats(
        total=total,
        by_status=by_status,
        by_country=by_country,
        total_capacity_tpd_h2=round(float(agg.total_cap or 0), 1),
        total_capex_usd_billions=round(float(agg.total_capex or 0) / 1000, 2),
        avg_capacity_tpd_h2=round(float(agg.avg_cap or 0), 1),
    )


@router.get(
    "/map",
    summary="GeoJSON FeatureCollection for map rendering",
    description="Returns all projects as a GeoJSON FeatureCollection for use in mapping libraries (Mapbox, Leaflet, Deck.gl).",
)
async def project_map(
    status: Optional[List[str]] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(Project).where(
        and_(Project.latitude.isnot(None), Project.longitude.isnot(None))
    )
    if status:
        query = query.where(Project.status.in_(status))

    rows = (await db.execute(query)).scalars().all()

    # Status → hex colour map for frontend rendering
    colour_map = {
        "operational": "#1D9E75",
        "construction": "#185FA5",
        "fid": "#854F0B",
        "announced": "#533AB7",
        "cancelled": "#A32D2D",
    }

    features = []
    for r in rows:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r.longitude, r.latitude]},
            "properties": {
                "id": r.id,
                "name": r.name,
                "developer": r.developer,
                "status": r.status,
                "colour": colour_map.get(r.status or "", "#888"),
                "capacity_tpd_h2": r.cracker_capacity_tpd_h2,
                "capex_usd_m": r.total_capex_usd_millions,
                "technology_vendor": r.technology_vendor,
                "target_operational": r.target_operational_date.isoformat() if r.target_operational_date else None,
            },
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {"count": len(features), "generated_at": datetime.utcnow().isoformat()},
    }


@router.get(
    "/recent-fids",
    summary="Projects that reached FID in the last 12 months",
    description="Key signal for analysts — FID announcements trigger cracker cost modelling demand.",
)
async def recent_fids(
    months: int = Query(12, ge=1, le=36),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cutoff = datetime.utcnow().replace(
        month=max(1, datetime.utcnow().month - months % 12)
    )
    rows = (await db.execute(
        select(Project)
        .where(and_(
            Project.fid_date >= cutoff,
            Project.status.in_(["fid", "construction", "operational"]),
        ))
        .order_by(Project.fid_date.desc())
        .limit(20)
    )).scalars().all()

    return {
        "data": [ProjectOut.from_orm(r) for r in rows],
        "count": len(rows),
        "period_months": months,
    }


@router.get(
    "/timeline",
    summary="Project commissioning timeline — when capacity comes online",
    description="Aggregated view of cracking capacity by year. Critical for supply/demand modelling.",
)
async def capacity_timeline(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (await db.execute(
        select(
            func.extract("year", Project.target_operational_date).label("year"),
            func.count().label("n_projects"),
            func.sum(Project.cracker_capacity_tpd_h2).label("total_capacity_tpd"),
            func.sum(Project.total_capex_usd_millions).label("total_capex_usd_m"),
        )
        .where(Project.target_operational_date.isnot(None))
        .group_by(func.extract("year", Project.target_operational_date))
        .order_by(func.extract("year", Project.target_operational_date))
    )).all()

    return {
        "unit": "tpd H2",
        "timeline": [
            {
                "year": int(r.year),
                "n_projects": r.n_projects,
                "new_capacity_tpd_h2": round(float(r.total_capacity_tpd or 0), 1),
                "capex_usd_m": round(float(r.total_capex_usd_m or 0), 0),
            }
            for r in rows
        ],
    }


@router.get(
    "/{project_id}",
    response_model=ProjectOut,
    summary="Get full project detail",
)
async def get_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = (await db.execute(
        select(Project).where(Project.id == project_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    return row


@router.post(
    "/",
    response_model=ProjectOut,
    status_code=201,
    summary="Submit a new project (analyst+ plan)",
)
async def create_project(
    payload: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_plan(["analyst", "enterprise"])),
):
    project = Project(**payload.dict())
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


@router.patch(
    "/{project_id}",
    response_model=ProjectOut,
    summary="Update project status or details",
)
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_plan(["analyst", "enterprise"])),
):
    row = (await db.execute(
        select(Project).where(Project.id == project_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    for field, value in payload.dict(exclude_none=True).items():
        setattr(row, field, value)
    await db.commit()
    await db.refresh(row)
    return row


@router.get(
    "/export/csv",
    summary="Export project database as CSV (analyst+ plan)",
)
async def export_projects_csv(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_plan(["analyst", "enterprise"])),
):
    import pandas as pd
    from io import StringIO
    from fastapi.responses import StreamingResponse

    rows = (await db.execute(select(Project).limit(5000))).scalars().all()
    df = pd.DataFrame([
        {
            "id": r.id, "name": r.name, "developer": r.developer,
            "country": r.location_country, "status": r.status,
            "capacity_tpd_h2": r.cracker_capacity_tpd_h2,
            "technology_vendor": r.technology_vendor,
            "capex_usd_m": r.total_capex_usd_millions,
            "fid_date": r.fid_date, "target_operational": r.target_operational_date,
            "offtaker": r.offtaker,
        }
        for r in rows
    ])
    output = StringIO()
    df.to_csv(output, index=False)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cracking_projects.csv"},
    )

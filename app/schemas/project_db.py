from pydantic import BaseModel
from typing import Optional, List, Any, Dict


class ProjectDBResult(BaseModel):
    id: str
    source: str
    name: str
    country: Optional[str] = None
    technology: Optional[str] = None
    status: Optional[str] = None
    capacity_mw: Optional[float] = None
    start_year: Optional[Any] = None
    end_year: Optional[Any] = None
    description: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    url: Optional[str] = None
    developer: Optional[str] = None


class ProjectDBSearchResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: List[ProjectDBResult]


class ProjectStatusCount(BaseModel):
    status: str
    count: int


class TechCapacity(BaseModel):
    technology: str
    capacity_mw: float


class ProjectStats(BaseModel):
    total_projects: int
    total_capacity_gw: float
    by_status: List[ProjectStatusCount]
    by_technology: List[TechCapacity]
    top_countries: List[Dict[str, Any]]


class ProjectTechnology(str):
    PEM = "PEM"
    ALK = "ALK"
    SOEC = "SOEC"
    SMR_CCS = "SMR-CCS"
    ATR_CCS = "ATR-CCS"


class ProjectStatus(str):
    ANNOUNCED = "announced"
    FEASIBILITY = "feasibility"
    FID = "fid"
    CONSTRUCTION = "construction"
    OPERATIONAL = "operational"
    CANCELLED = "cancelled"

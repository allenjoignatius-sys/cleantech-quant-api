from pydantic import BaseModel
from typing import Optional, List


class NewsItem(BaseModel):
    source: str
    title: Optional[str] = None
    url: str
    summary: Optional[str] = None
    published_at: Optional[str] = None
    is_fid_related: bool = False


class NewsSearchResponse(BaseModel):
    total: int
    returned: int
    items: List[NewsItem]


class FIDAlert(BaseModel):
    title: Optional[str]
    url: str
    source: str
    published_at: Optional[str]
    summary: Optional[str]
    detected_capacity_mw: Optional[float]
    country_mentioned: Optional[str]
    is_confirmed_fid: bool

from pydantic import BaseModel
from typing import List, Optional


class GeographyInfo(BaseModel):
    code: str
    name: str
    source: str
    currency: str
    unit: str


class EnergyGeographyList(BaseModel):
    geographies: List[GeographyInfo]


class EnergyPriceResponse(BaseModel):
    geography: str
    price_eur_mwh: float
    currency: str
    source: str
    timestamp: str


class PriceDatapoint(BaseModel):
    timestamp: str
    price: Optional[float]


class EnergyPriceHistoryResponse(BaseModel):
    geography: str
    currency: str
    resolution: str
    datapoints: List[PriceDatapoint]


class EnergyPriceStats(BaseModel):
    geography: str
    mean_price: float
    min_price: float
    max_price: float
    std_dev: float
    latest_spot: float
    currency: str
    window_days: int

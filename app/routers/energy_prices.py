from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional, List
from datetime import date, timedelta
from app.services.energy_price_service import EnergyPriceService
from app.schemas.energy import EnergyPriceResponse, EnergyPriceHistoryResponse, EnergyGeographyList, EnergyPriceStats
# FIXED: app.dependencies -> app.auth | app.models.user -> app.models
from app.auth import get_current_user
from app.models import User

router = APIRouter(prefix="/v1/energy-prices", tags=["Energy Price Feeds"])

@router.get("/geographies", response_model=EnergyGeographyList)
async def list_geographies(current_user: User = Depends(get_current_user)):
    return EnergyPriceService.get_supported_geographies()

@router.get("/spot", response_model=EnergyPriceResponse)
async def get_spot_price(geography: str = Query(...), current_user: User = Depends(get_current_user)):
    service = EnergyPriceService()
    return await service.get_spot_price(geography)

@router.get("/history", response_model=EnergyPriceHistoryResponse)
async def get_price_history(
    geography: str = Query(...), start_date: date = Query(default_factory=lambda: date.today() - timedelta(days=30)),
    end_date: date = Query(default_factory=date.today), resolution: str = Query(default="hour"),
    current_user: User = Depends(get_current_user)
):
    service = EnergyPriceService()
    return await service.get_price_history(geography, start_date, end_date, resolution)

@router.get("/compare", response_model=List[EnergyPriceStats])
async def compare_geographies(geographies: List[str] = Query(...), window_days: int = Query(default=30), current_user: User = Depends(get_current_user)):
    service = EnergyPriceService()
    return await service.compare_geographies(geographies, window_days)

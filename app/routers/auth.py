"""
app/routers/auth.py — Authentication Endpoints
Handles user registration, login, and API key management.
"""
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import User, APIKey, SubscriptionPlan
from app.config import settings

# Import dependencies and helpers from our newly split auth module
from app.auth import (
    get_current_user,
    hash_password,
    verify_password,
    create_access_token,
    generate_api_key
)

router = APIRouter()

# ─── Schemas ──────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    company: Optional[str] = None
    job_title: Optional[str] = None

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict

class APIKeyCreate(BaseModel):
    name: str = Field(default="Default Key", max_length=100)
    expires_in_days: Optional[int] = Field(None, ge=1, le=365)

class APIKeyOut(BaseModel):
    id: str
    name: str
    key_prefix: str  
    is_active: bool
    created_at: datetime
    expires_at: Optional[datetime]
    last_used: Optional[datetime]

# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(
        select(User).where(User.email == payload.email)
    )).scalar_one_or_none()
    
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        company=payload.company,
        job_title=payload.job_title,
        plan=SubscriptionPlan.free,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user={"id": str(user.id), "email": user.email, "plan": user.plan.value},
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(
        select(User).where(User.email == payload.email)
    )).scalar_one_or_none()

    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated")

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user={
            "id": str(user.id), "email": user.email,
            "plan": user.plan.value, "company": user.company,
        },
    )


@router.get("/me")
async def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "company": current_user.company,
        "job_title": current_user.job_title,
        "plan": current_user.plan.value,
        "is_active": current_user.is_active,
        "requests_today": current_user.requests_today,
        "requests_this_month": current_user.requests_this_month,
        "created_at": current_user.created_at,
    }


@router.get("/keys", response_model=List[APIKeyOut])
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    keys = (await db.execute(
        select(APIKey).where(APIKey.user_id == current_user.id, APIKey.is_active == True)
    )).scalars().all()
    
    return [APIKeyOut(
        id=str(k.id), name=k.name, key_prefix=k.key_prefix,
        is_active=k.is_active, created_at=k.created_at,
        expires_at=k.expires_at, last_used=k.last_used,
    ) for k in keys]


@router.post("/keys", status_code=201)
async def create_api_key(
    payload: APIKeyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Check key limit by plan
    key_limits = {"free": 1, "analyst": 5, "enterprise": 50}
    existing_count = len((await db.execute(
        select(APIKey).where(APIKey.user_id == current_user.id, APIKey.is_active == True)
    )).scalars().all())

    limit = key_limits.get(current_user.plan.value, 1)
    if existing_count >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Key limit reached ({limit} for {current_user.plan.value} plan). "
                   "Delete an existing key or upgrade your plan."
        )

    raw_key, key_hash, key_prefix = generate_api_key()
    expires_at = None
    if payload.expires_in_days:
        expires_at = datetime.utcnow() + timedelta(days=payload.expires_in_days)

    key = APIKey(
        user_id=current_user.id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        name=payload.name,
        expires_at=expires_at,
    )
    db.add(key)
    await db.commit()

    return {
        "id": str(key.id),
        "name": key.name,
        "key": raw_key,   # Only shown ONCE on creation
        "key_prefix": key_prefix,
        "expires_at": expires_at,
        "warning": "Save this key now — it will not be shown again.",
    }


@router.delete("/keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    key = (await db.execute(
        select(APIKey).where(APIKey.id == key_id, APIKey.user_id == current_user.id)
    )).scalar_one_or_none()
    
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")
        
    key.is_active = False
    await db.commit()
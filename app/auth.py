"""
app/auth.py — Core Authentication Dependencies & Helpers
Contains token generation, password hashing, and FastAPI dependencies
for resolving users via JWT or API Key and enforcing plan limits.
"""
import secrets
import hashlib
import jwt
import bcrypt
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import User, APIKey
from app.config import settings

bearer = HTTPBearer(auto_error=False)


# ─── Auth helpers ─────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_access_token(user_id: str) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

def generate_api_key() -> tuple[str, str, str]:
    """Returns (raw_key, key_hash, key_prefix)"""
    raw = settings.API_KEY_PREFIX + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:12]
    return raw, key_hash, prefix


# ─── Dependencies ─────────────────────────────────────────────────────────────

async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    # Try API key first (X-API-Key header)
    api_key_header = request.headers.get("X-API-Key")
    if api_key_header:
        key_hash = hashlib.sha256(api_key_header.encode()).hexdigest()
        result = await db.execute(
            select(APIKey).where(APIKey.key_hash == key_hash, APIKey.is_active == True)
        )
        api_key = result.scalar_one_or_none()
        if not api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")
        if api_key.expires_at and api_key.expires_at < datetime.utcnow():
            raise HTTPException(status_code=401, detail="API key expired")
        
        # Update last_used (fire and forget)
        api_key.last_used = datetime.utcnow()
        await db.commit()
        
        user = (await db.execute(
            select(User).where(User.id == api_key.user_id)
        )).scalar_one_or_none()
        
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="User account inactive")
        return user

    # Try JWT Bearer
    if credentials:
        try:
            payload = jwt.decode(
                credentials.credentials,
                settings.JWT_SECRET,
                algorithms=[settings.JWT_ALGORITHM]
            )
            user_id = payload.get("sub")
            user = (await db.execute(
                select(User).where(User.id == user_id)
            )).scalar_one_or_none()
            if user and user.is_active:
                return user
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token")

    raise HTTPException(
        status_code=401,
        detail="Authentication required. Provide X-API-Key header or Bearer token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_plan(allowed_plans: List[str]):
    """Dependency factory: require user to be on one of the listed plans."""
    async def check_plan(current_user: User = Depends(get_current_user)) -> User:
        if current_user.plan.value not in allowed_plans:
            raise HTTPException(
                status_code=403,
                detail=f"This endpoint requires one of: {', '.join(allowed_plans)} plan. "
                       f"Current plan: {current_user.plan.value}. Upgrade at cleantechquant.io/upgrade"
            )
        return current_user
    return check_plan
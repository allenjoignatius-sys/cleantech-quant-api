"""
Async SQLAlchemy database engine, session factory, and Base declarative model.
All routers import `get_db` from here as a FastAPI dependency.
"""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

# Async engine — pool_size / max_overflow tuned for t3.medium (2 vCPU)
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,          # auto-reconnect on stale connections
    pool_recycle=3600,           # recycle connections every hour
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,      # keep objects usable after commit
    autoflush=False,
)


class Base(DeclarativeBase):
    """Shared declarative base — all ORM models inherit from this."""
    pass


async def get_db() -> AsyncSession:
    """
    FastAPI dependency that yields an async DB session per request.
    Automatically commits on success, rolls back on exception.

    Usage:
        @router.get("/")
        async def endpoint(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ─── Redis clients ──────────────────────────────────────────────────────────
# Construction is lazy and does not connect until first use, so importing these
# never fails even when Redis is down (callers degrade gracefully).
_async_redis = None


def get_redis_client():
    """Return a shared async Redis client (``redis.asyncio.Redis``) or None."""
    global _async_redis
    if _async_redis is None:
        try:
            from redis import asyncio as aioredis
            _async_redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        except Exception:  # pragma: no cover - redis lib/url issues
            return None
    return _async_redis


def get_redis_client_sync():
    """Return a synchronous Redis client (for Celery tasks) or None."""
    try:
        import redis
        return redis.from_url(settings.REDIS_URL, decode_responses=True)
    except Exception:  # pragma: no cover
        return None

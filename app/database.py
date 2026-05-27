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

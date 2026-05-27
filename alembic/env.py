"""
Alembic migration environment — async-compatible.
Reads DATABASE_URL from environment / .env file so it works
in both local dev and CI/CD pipelines without hardcoded credentials.
"""

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ── Load app models so Alembic can detect changes ────────────────
# Must import Base and all models before autogenerate will work.
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Base  # noqa: F401 — all model classes loaded via Base
from app.config import settings

# ── Alembic config object ─────────────────────────────────────────
config = context.config

# Override sqlalchemy.url with value from settings / env
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# ── Set up Python logging from alembic.ini ────────────────────────
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Target metadata for autogenerate ─────────────────────────────
target_metadata = Base.metadata


# ── Offline mode (generates SQL without a live DB) ────────────────

def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode. No DB connection required.
    Useful for generating SQL scripts for review.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online mode (connects to DB and applies migrations) ───────────

def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


# ── Entry point ───────────────────────────────────────────────────

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

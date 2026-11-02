from collections.abc import AsyncIterator

from sqlalchemy import text
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base

engine = create_async_engine(settings.resolved_database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


async def check_database() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def initialize_database() -> None:
    async with engine.connect() as connection:
        tables = await connection.run_sync(lambda sync_connection: set(inspect(sync_connection).get_table_names()))
    expected_tables = set(Base.metadata.tables)
    has_version = "alembic_version" in tables
    existing_application_tables = tables & expected_tables
    if not has_version and existing_application_tables:
        initial_tables = {
            "projects", "project_files", "analysis_runs", "findings", "tool_results",
            "workflow_events", "repairs", "validation_results", "reports",
        }
        if not initial_tables.issubset(tables):
            missing = ", ".join(sorted(initial_tables - tables))
            raise RuntimeError(f"Database has a partial unversioned schema; missing tables: {missing}")
        alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        await asyncio.to_thread(command.stamp, alembic_config, "0001_initial")

    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    await asyncio.to_thread(command.upgrade, alembic_config, "head")


async def close_database() -> None:
    await engine.dispose()
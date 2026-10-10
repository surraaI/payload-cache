"""Engine and session wiring."""

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from payload_cache import models  # noqa: F401  (registers the tables on SQLModel.metadata)


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url)


async def create_tables(engine: AsyncEngine) -> None:
    # Shortcut: with two tables and no schema history yet, create_all stands in
    # for a migration tool such as Alembic.
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: async sessions cannot lazily reload attributes,
    # so objects must stay readable after a commit.
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

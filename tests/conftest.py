from collections.abc import AsyncIterator

import pytest

from fakes import CountingTransformer
from payload_cache.db import create_engine, create_session_factory, create_tables


@pytest.fixture
def database_url(tmp_path) -> str:
    # A file rather than ":memory:" so that every pooled connection, and a
    # restarted app, sees the same database.
    return f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture
def transformer() -> CountingTransformer:
    return CountingTransformer()


@pytest.fixture
async def session(database_url) -> AsyncIterator:
    engine = create_engine(database_url)
    await create_tables(engine)
    async with create_session_factory(engine)() as session:
        yield session
    await engine.dispose()

"""Application factory.

Run with: uvicorn payload_cache.main:create_app --factory
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from payload_cache.api import router
from payload_cache.config import Settings
from payload_cache.db import create_engine, create_session_factory, create_tables
from payload_cache.transformer import CoalescingTransformer, Transformer, make_uppercase_transformer


def create_app(settings: Settings | None = None, transformer: Transformer | None = None) -> FastAPI:
    """Build the app.

    A factory rather than a module-level app, so tests can pass their own
    database and a transformer whose calls they can count.
    """
    settings = settings or Settings()
    transformer = transformer or make_uppercase_transformer(settings.transformer_delay_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        await create_tables(engine)
        app.state.session_factory = create_session_factory(engine)
        # One instance for the whole app: coalescing only works if every
        # request shares the same record of in-flight calls.
        app.state.transformer = CoalescingTransformer(transformer)
        yield
        await engine.dispose()

    app = FastAPI(title="Payload cache", lifespan=lifespan)
    app.include_router(router)
    return app

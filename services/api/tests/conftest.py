"""Pytest configuration and fixtures for API integration tests."""

import os
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Configure database URL before importing app modules
TEST_DB_URL = os.getenv(
    "AGENTPROBE_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres@127.0.0.1:54333/agentprobe",
)
os.environ["AGENTPROBE_DATABASE_URL"] = TEST_DB_URL
os.environ["SYNC_DATABASE_URL"] = TEST_DB_URL.replace("postgresql+asyncpg://", "postgresql://")

from app.config import settings  # noqa: E402

settings.DATABASE_URL = TEST_DB_URL

from app.db import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(
        TEST_DB_URL,
        echo=False,
        pool_pre_ping=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    async_session = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_session() as session:
        yield session
        # Truncate tables between tests for clean test state
        await session.execute(text("TRUNCATE TABLE spans, runs, projects CASCADE;"))
        await session.commit()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models import Base
from app.database import get_db

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


async def get_test_app():
    """Create a minimal FastAPI app for testing routes without full lifespan."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from app.api.routes import router, set_bot_state
    from datetime import datetime

    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    test_session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with test_session_factory() as session:
            yield session

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.dependency_overrides[get_db] = override_get_db

    set_bot_state({
        "running": True,
        "llm_provider": "mock",
        "llm_model": "test",
        "last_decision_at": None,
        "start_time": datetime.utcnow(),
        "portfolio": None,
    })

    return app, engine


async def test_status_endpoint():
    app, engine = await get_test_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/status")
    assert response.status_code == 200
    data = response.json()
    assert data["bot_running"] is True
    assert data["llm_provider"] == "mock"
    await engine.dispose()


async def test_portfolio_endpoint_no_portfolio():
    app, engine = await get_test_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/portfolio")
    assert response.status_code == 200
    data = response.json()
    assert "total_value" in data
    await engine.dispose()


async def test_trades_endpoint_empty():
    app, engine = await get_test_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/trades")
    assert response.status_code == 200
    assert response.json() == []
    await engine.dispose()


async def test_portfolio_snapshots_empty():
    app, engine = await get_test_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/portfolio/snapshots")
    assert response.status_code == 200
    assert response.json() == []
    await engine.dispose()


async def test_stats_endpoint():
    app, engine = await get_test_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_trades"] == 0
    assert data["win_rate"] == 0.0
    await engine.dispose()


async def test_news_signals_empty():
    app, engine = await get_test_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/signals/news")
    assert response.status_code == 200
    assert response.json() == []
    await engine.dispose()


async def test_skipped_signals_empty():
    app, engine = await get_test_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/signals/skipped")
    assert response.status_code == 200
    assert response.json() == []
    await engine.dispose()

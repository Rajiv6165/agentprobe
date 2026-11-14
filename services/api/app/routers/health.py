"""Health and readiness probe endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db

router = APIRouter(tags=["Health"])


@router.get("/health", summary="Liveness probe")
async def health_check() -> dict[str, str]:
    """Liveness probe verifying the service process is up."""
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe")
async def readiness_check(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """Readiness probe verifying connectivity to PostgreSQL."""
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "DATABASE_UNAVAILABLE", "message": str(exc)},
        ) from exc


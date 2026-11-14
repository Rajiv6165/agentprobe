"""Trace ingestion endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.schemas import IngestRequest, IngestResponse
from app.services.auth import verify_api_key
from app.services.ingest_service import process_ingest_batch

router = APIRouter(prefix="/v1/ingest", tags=["Ingest"])


@router.post(
    "",
    response_model=IngestResponse,
    status_code=status.HTTP_200_OK,
    summary="Ingest batch of runs and spans",
)
async def ingest_traces(
    payload: IngestRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str | None = Depends(verify_api_key),
) -> IngestResponse:
    """Accept and persist a batch of agent runs and spans idempotently."""
    return await process_ingest_batch(db, payload)

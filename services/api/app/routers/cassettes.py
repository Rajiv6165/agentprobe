"""Cassette endpoints for managing deterministic recordings."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.schemas import (
    CassetteCreateRequest,
    CassetteListResponse,
    CassetteResponse,
)
from app.services.auth import verify_api_key
from app.services.cassette_service import (
    create_cassette_from_run,
    delete_cassette,
    get_cassette_by_id,
    list_cassettes,
)

router = APIRouter(tags=["Cassettes"], dependencies=[Depends(verify_api_key)])


@router.post(
    "/v1/runs/{run_id}/cassette",
    response_model=CassetteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create or extract a cassette from an existing run",
)
async def record_cassette_from_run(
    run_id: uuid.UUID,
    payload: CassetteCreateRequest | None = None,
    force: bool = Query(default=False, description="Overwrite existing cassette if already linked"),
    db: AsyncSession = Depends(get_db),
) -> CassetteResponse:
    """Extract interactive spans from run, validate size, and persist as deterministic cassette."""
    name = payload.name if payload else None
    cassette = await create_cassette_from_run(db=db, run_id=run_id, name=name, force=force)
    return CassetteResponse(
        id=cassette.id,
        project_id=cassette.project_id,
        source_run_id=cassette.source_run_id,
        name=cassette.name,
        schema_version=cassette.schema_version,
        fingerprint=cassette.fingerprint,
        content=cassette.content,
        size_bytes=cassette.size_bytes,
        created_at=cassette.created_at,
    )


@router.get(
    "/v1/cassettes",
    response_model=CassetteListResponse,
    summary="List cassette summaries with cursor pagination",
)
async def get_cassettes(
    project: str | None = Query(default=None, description="Filter by project name or ID"),
    agent_name: str | None = Query(default=None, description="Filter by agent name"),
    limit: int = Query(default=50, ge=1, le=100, description="Items per page"),
    cursor: str | None = Query(default=None, description="Opaque cursor for pagination"),
    db: AsyncSession = Depends(get_db),
) -> CassetteListResponse:
    """Retrieve catalog of recorded cassettes filtered by project or agent name."""
    return await list_cassettes(
        db=db,
        project=project,
        agent_name=agent_name,
        limit=limit,
        cursor=cursor,
    )


@router.get(
    "/v1/cassettes/{cassette_id}",
    response_model=CassetteResponse,
    summary="Get full cassette details and interaction steps",
)
async def get_cassette(
    cassette_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> CassetteResponse:
    """Retrieve complete cassette including interactions and execution fingerprint."""
    cassette = await get_cassette_by_id(db, cassette_id)
    if cassette is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CASSETTE_NOT_FOUND", "message": f"Cassette {cassette_id} not found"},
        )
    return CassetteResponse(
        id=cassette.id,
        project_id=cassette.project_id,
        source_run_id=cassette.source_run_id,
        name=cassette.name,
        schema_version=cassette.schema_version,
        fingerprint=cassette.fingerprint,
        content=cassette.content,
        size_bytes=cassette.size_bytes,
        created_at=cassette.created_at,
    )


@router.get(
    "/v1/cassettes/{cassette_id}/download",
    summary="Download raw .cassette.json file",
)
async def download_cassette_file(
    cassette_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Download raw deterministic cassette JSON with Content-Disposition header."""
    cassette = await get_cassette_by_id(db, cassette_id)
    if cassette is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CASSETTE_NOT_FOUND", "message": f"Cassette {cassette_id} not found"},
        )
    content_str = json.dumps(cassette.content, indent=2)
    filename = f"{cassette.name}.cassette.json"
    return Response(
        content=content_str,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete(
    "/v1/cassettes/{cassette_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a cassette",
)
async def remove_cassette(
    cassette_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a cassette record and nullify cassette references on associated runs."""
    deleted = await delete_cassette(db, cassette_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CASSETTE_NOT_FOUND", "message": f"Cassette {cassette_id} not found"},
        )

"""Run and span query endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.schemas import RunListResponse, RunResponse, SpanResponse, SpanTreeNode
from app.services.auth import verify_api_key
from app.services.run_service import (
    get_run_by_id,
    get_run_spans,
    get_run_tree,
    list_runs,
)

router = APIRouter(prefix="/v1/runs", tags=["Runs"], dependencies=[Depends(verify_api_key)])


@router.get("", response_model=RunListResponse, summary="Query runs with cursor pagination")
async def get_runs(
    project: str | None = Query(default=None, description="Filter by project name or ID"),
    status_filter: str | None = Query(
        default=None, alias="status", description="Filter by status (running/success/error)"
    ),
    agent_name: str | None = Query(default=None, description="Filter by agent name"),
    replay_of: uuid.UUID | None = Query(
        default=None, description="Filter runs that are replays of this run ID"
    ),
    started_after: datetime | None = Query(
        default=None, description="Filter runs started after ISO datetime"
    ),
    started_before: datetime | None = Query(
        default=None, description="Filter runs started before ISO datetime"
    ),
    sort_by: str = Query(
        default="created_at",
        description="Field to sort by (created_at, started_at, cost, latency_ms)",
    ),
    sort_order: str = Query(default="desc", description="Sort order: asc or desc"),
    limit: int = Query(default=50, ge=1, le=100, description="Items per page"),
    cursor: str | None = Query(default=None, description="Opaque cursor for pagination"),
    db: AsyncSession = Depends(get_db),
) -> RunListResponse:
    """Retrieve cursor-paginated agent runs filtered by project, status, agent, and date."""
    return await list_runs(
        db=db,
        project=project,
        status=status_filter,
        agent_name=agent_name,
        replay_of=replay_of,
        started_after=started_after,
        started_before=started_before,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        cursor=cursor,
    )


@router.get("/{run_id}", response_model=RunResponse, summary="Get run summary and aggregates")
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> RunResponse:
    """Retrieve details, usage totals, and status for a specific execution run."""
    run = await get_run_by_id(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RUN_NOT_FOUND", "message": f"Run {run_id} not found"},
        )
    return run


@router.get("/{run_id}/spans", response_model=list[SpanResponse], summary="Get flat list of spans")
async def get_spans(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[SpanResponse]:
    """Retrieve chronological flat list of all spans associated with a run."""
    # Ensure run exists
    run = await get_run_by_id(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RUN_NOT_FOUND", "message": f"Run {run_id} not found"},
        )
    return await get_run_spans(db, run_id)


@router.get(
    "/{run_id}/tree",
    response_model=list[SpanTreeNode],
    summary="Get spans assembled as tree",
)
async def get_tree(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[SpanTreeNode]:
    """Retrieve execution hierarchy assembled as a nested span tree starting from roots."""
    run = await get_run_by_id(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RUN_NOT_FOUND", "message": f"Run {run_id} not found"},
        )
    return await get_run_tree(db, run_id)

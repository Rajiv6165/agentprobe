"""Query services for runs and span hierarchy assembly."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Project, Run, Span
from app.schemas import (
    RunListResponse,
    RunResponse,
    RunTotalsSchema,
    SpanResponse,
    SpanTreeNode,
)


def encode_cursor(created_at: datetime, run_id: uuid.UUID) -> str:
    """Encode created_at timestamp and UUID into an opaque pagination cursor."""
    payload = {"t": created_at.isoformat(), "id": str(run_id)}
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID] | None:
    """Decode an opaque pagination cursor into created_at timestamp and UUID."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        dt = datetime.fromisoformat(payload["t"])
        uid = uuid.UUID(payload["id"])
        return dt, uid
    except Exception:
        return None


def run_to_schema(run: Run) -> RunResponse:
    """Convert an ORM Run model to its API response schema."""
    project_name = run.project.name if run.project else "unknown"
    duration = None
    if run.ended_at and run.started_at:
        duration = max(0.0, (run.ended_at - run.started_at).total_seconds() * 1000.0)

    totals = RunTotalsSchema(
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        total_tokens=run.total_tokens,
        cost=run.cost,
        latency_ms=run.latency_ms,
        step_count=run.step_count,
    )

    return RunResponse(
        id=run.id,
        project_id=run.project_id,
        project_name=project_name,
        agent_name=run.agent_name,
        agent_version=run.agent_version,
        status=run.status,
        tags=list(run.tags or []),
        started_at=run.started_at,
        ended_at=run.ended_at,
        duration_ms=duration,
        totals=totals,
        metadata=dict(run.metadata_json or {}),
        replay_of_run_id=run.replay_of_run_id,
        cassette_id=run.cassette_id,
        replay_policy=run.replay_policy,
        created_at=run.created_at,
    )


async def list_runs(
    db: AsyncSession,
    project: str | None = None,
    status: str | None = None,
    agent_name: str | None = None,
    replay_of: uuid.UUID | None = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    limit: int = 50,
    cursor: str | None = None,
) -> RunListResponse:
    """Filter and retrieve runs with cursor-based pagination."""
    query = (
        select(Run).join(Project, Run.project_id == Project.id).options(selectinload(Run.project))
    )

    filters = []
    if project:
        # Check if project parameter is a UUID or name
        try:
            proj_uuid = uuid.UUID(project)
            filters.append(or_(Project.id == proj_uuid, Project.name == project))
        except ValueError:
            filters.append(Project.name == project)

    if status:
        filters.append(Run.status == status.lower())
    if agent_name:
        filters.append(Run.agent_name == agent_name)
    if replay_of:
        filters.append(Run.replay_of_run_id == replay_of)
    if started_after:
        filters.append(Run.started_at >= started_after)
    if started_before:
        filters.append(Run.started_at <= started_before)

    # Cursor filtering
    if cursor:
        decoded = decode_cursor(cursor)
        if decoded:
            cursor_dt, cursor_id = decoded
            if sort_order.lower() == "asc":
                filters.append(
                    or_(
                        Run.created_at > cursor_dt,
                        and_(Run.created_at == cursor_dt, Run.id > cursor_id),
                    )
                )
            else:
                filters.append(
                    or_(
                        Run.created_at < cursor_dt,
                        and_(Run.created_at == cursor_dt, Run.id < cursor_id),
                    )
                )

    if filters:
        query = query.where(and_(*filters))

    # Sorting
    sort_col = getattr(Run, sort_by, Run.created_at)
    if sort_order.lower() == "asc":
        query = query.order_by(sort_col.asc(), Run.id.asc())
    else:
        query = query.order_by(sort_col.desc(), Run.id.desc())

    # Limit + 1 to detect if next page exists
    query = query.limit(min(limit, 100) + 1)
    result = await db.execute(query)
    runs = list(result.scalars().all())

    has_more = len(runs) > limit
    if has_more:
        runs = runs[:limit]

    run_schemas = [run_to_schema(r) for r in runs]
    next_cursor = None
    if has_more and runs:
        last_run = runs[-1]
        next_cursor = encode_cursor(last_run.created_at, last_run.id)

    return RunListResponse(
        runs=run_schemas,
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def get_run_by_id(db: AsyncSession, run_id: uuid.UUID) -> RunResponse | None:
    """Retrieve an individual run summary and aggregates."""
    stmt = select(Run).where(Run.id == run_id).options(selectinload(Run.project))
    res = await db.execute(stmt)
    run = res.scalar_one_or_none()
    return run_to_schema(run) if run else None


async def get_run_spans(db: AsyncSession, run_id: uuid.UUID) -> list[SpanResponse]:
    """Retrieve flat list of spans belonging to a run, ordered chronologically."""
    stmt = (
        select(Span)
        .where(Span.run_id == run_id)
        .order_by(Span.started_at.asc(), Span.created_at.asc())
    )
    res = await db.execute(stmt)
    spans = res.scalars().all()

    return [
        SpanResponse(
            id=s.id,
            run_id=s.run_id,
            parent_id=s.parent_id,
            type=s.type,
            name=s.name,
            input=s.input,
            output=s.output,
            error=s.error,
            started_at=s.started_at,
            ended_at=s.ended_at,
            duration_ms=s.duration_ms,
            metadata=dict(s.metadata_json or {}),
            created_at=s.created_at,
        )
        for s in spans
    ]


async def get_run_tree(db: AsyncSession, run_id: uuid.UUID) -> list[SpanTreeNode]:
    """Assemble all spans of a run into a nested hierarchical tree structure."""
    spans = await get_run_spans(db, run_id)
    if not spans:
        return []

    node_map: dict[uuid.UUID, SpanTreeNode] = {}
    for s in spans:
        node_map[s.id] = SpanTreeNode(
            id=s.id,
            run_id=s.run_id,
            parent_id=s.parent_id,
            type=s.type,
            name=s.name,
            input=s.input,
            output=s.output,
            error=s.error,
            started_at=s.started_at,
            ended_at=s.ended_at,
            duration_ms=s.duration_ms,
            metadata=s.metadata,
            children=[],
        )

    roots: list[SpanTreeNode] = []
    for s in spans:
        node = node_map[s.id]
        if s.parent_id and s.parent_id in node_map:
            node_map[s.parent_id].children.append(node)
        else:
            roots.append(node)

    return roots

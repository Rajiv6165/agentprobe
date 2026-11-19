"""Cassette management and extraction service."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import Cassette, Project, Run, Span
from app.schemas import CassetteListResponse, CassetteSummaryResponse

logger = logging.getLogger("agentprobe.cassette")

DEFAULT_IGNORE_KEYS: set[str] = {
    "request_id",
    "timestamp",
    "started_at",
    "ended_at",
    "duration_ms",
    "message_id",
    "call_id",
    "created_at",
    "trace_id",
    "span_id",
}

UUID_REGEX: re.Pattern[str] = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
ISO_TIMESTAMP_REGEX: re.Pattern[str] = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
HEX_ADDR_REGEX: re.Pattern[str] = re.compile(r"\b0x[0-9a-fA-F]{6,16}\b")
OBJECT_ADDR_REGEX: re.Pattern[str] = re.compile(r" at 0x[0-9a-fA-F]+\b")


def _normalize_value(value: Any, seen: set[int] | None = None) -> Any:
    """Scrub volatile keys and mask regex patterns for deterministic fingerprinting."""
    if seen is None:
        seen = set()

    if value is None or isinstance(value, (bool, int)):
        return value

    if isinstance(value, float):
        return round(value, 8)

    if isinstance(value, str):
        res = unicodedata.normalize("NFC", value)
        res = OBJECT_ADDR_REGEX.sub("", res)
        res = UUID_REGEX.sub("<UUID>", res)
        res = ISO_TIMESTAMP_REGEX.sub("<TIMESTAMP>", res)
        res = HEX_ADDR_REGEX.sub("<ADDR>", res)
        return res

    val_id = id(value)
    if val_id in seen:
        return "<CircularReference>"
    seen.add(val_id)

    if isinstance(value, dict):
        return {
            str(k): _normalize_value(v, seen)
            for k, v in value.items()
            if str(k) not in DEFAULT_IGNORE_KEYS
        }

    if isinstance(value, (list, tuple, set)):
        return [_normalize_value(item, seen) for item in value]

    return str(value)


def _canonical_json(data: Any) -> str:
    """Format python data into strict deterministic canonical JSON."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def compute_fingerprint(data: Any) -> str:
    """Compute normalized SHA-256 fingerprint for a payload."""
    normalized = _normalize_value(data)
    canonical = _canonical_json(normalized)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _has_redacted_marker(val: Any) -> bool:
    """Check if value contains the SDK [REDACTED] indicator."""
    if val is None:
        return False
    if isinstance(val, str):
        return "[REDACTED]" in val
    if isinstance(val, dict):
        return any(_has_redacted_marker(k) or _has_redacted_marker(v) for k, v in val.items())
    if isinstance(val, (list, tuple, set)):
        return any(_has_redacted_marker(x) for x in val)
    return False


def encode_cassette_cursor(created_at: datetime, cassette_id: uuid.UUID) -> str:
    """Encode created_at and UUID into an opaque pagination cursor."""
    payload = {"t": created_at.isoformat(), "id": str(cassette_id)}
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cassette_cursor(cursor: str) -> tuple[datetime, uuid.UUID] | None:
    """Decode an opaque pagination cursor."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        dt = datetime.fromisoformat(payload["t"])
        uid = uuid.UUID(payload["id"])
        return dt, uid
    except Exception:
        return None


async def create_cassette_from_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    name: str | None = None,
    force: bool = False,
) -> Cassette:
    """Extract spans from a recorded run and build a portable deterministic Cassette."""
    # 1. Fetch run
    run_stmt = select(Run).where(Run.id == run_id)
    run_res = await db.execute(run_stmt)
    run = run_res.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RUN_NOT_FOUND", "message": f"Run {run_id} not found"},
        )

    # 2. Check if run already has a cassette linked and force is False
    if not force and run.cassette_id:
        cas_stmt = select(Cassette).where(Cassette.id == run.cassette_id)
        cas_res = await db.execute(cas_stmt)
        existing_cas = cas_res.scalar_one_or_none()
        if existing_cas:
            return existing_cas

    # 3. Fetch all spans for this run ordered chronologically
    spans_stmt = select(Span).where(Span.run_id == run_id).order_by(Span.started_at.asc())
    spans_res = await db.execute(spans_stmt)
    spans = list(spans_res.scalars().all())

    # 4. Filter interaction spans (llm, tool, capture)
    interactive_spans: list[Span] = []
    for s in spans:
        is_capture = s.metadata_json.get("kind") == "capture" or s.name == "capture"
        if is_capture or s.type in ("llm", "tool"):
            interactive_spans.append(s)

    # Reject runs that have no interactions with 400
    if not interactive_spans:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "NO_INTERACTIONS",
                "message": f"Run {run_id} has no interactive spans (llm, tool, capture) to record",
            },
        )

    # Sort spans chronologically
    interactive_spans.sort(key=lambda x: x.started_at)

    # Identify root agent span (if any)
    root_agent_spans = [
        s
        for s in spans
        if s.type == "agent" and s.metadata_json.get("kind") != "capture" and s.parent_id is None
    ]
    if not root_agent_spans:
        root_agent_spans = [
            s
            for s in spans
            if s.metadata_json.get("kind") != "capture"
            and (s.name == run.agent_name or s.type == "agent")
        ]
    root_agent_span = root_agent_spans[0] if root_agent_spans else None

    agent_input: dict[str, Any] = {}
    agent_output: Any = None
    if root_agent_span:
        if isinstance(root_agent_span.input, dict) and "args" in root_agent_span.input:
            agent_input = root_agent_span.input
        elif root_agent_span.input is not None:
            agent_input = {"args": [root_agent_span.input], "kwargs": {}}
        agent_output = root_agent_span.output

    # Map span id to interaction index
    span_id_to_index: dict[uuid.UUID, int] = {s.id: idx for idx, s in enumerate(interactive_spans)}

    interactions: list[dict[str, Any]] = []
    for idx, s in enumerate(interactive_spans):
        if s.metadata_json.get("kind") == "capture" or s.name == "capture":
            kind = "capture"
        elif s.type == "llm":
            kind = "llm"
        else:
            kind = "tool"

        parent_idx = span_id_to_index.get(s.parent_id) if s.parent_id else None

        is_redacted = (
            _has_redacted_marker(s.input)
            or _has_redacted_marker(s.output)
            or _has_redacted_marker(s.metadata_json)
            or bool(s.metadata_json.get("redacted"))
        )

        fp = compute_fingerprint(s.input)

        interactions.append(
            {
                "index": idx,
                "kind": kind,
                "name": s.name,
                "request": s.input,
                "response": s.output,
                "error": s.error,
                "request_fingerprint": fp,
                "recorded_duration_ms": s.duration_ms or 0.0,
                "parent_index": parent_idx,
                "redacted": is_redacted,
                "metadata": dict(s.metadata_json or {}),
            }
        )

    cassette_name = name or f"{run.agent_name}_cassette"
    cassette_id = uuid.uuid4()
    now_iso = datetime.now(timezone.utc).isoformat()

    cassette_content: dict[str, Any] = {
        "schema_version": "1.0",
        "id": str(cassette_id),
        "name": cassette_name,
        "created_at": now_iso,
        "source_run_id": str(run.id),
        "agent_name": run.agent_name,
        "agent_version": run.agent_version,
        "agent_input": agent_input,
        "agent_output": agent_output,
        "metadata": dict(run.metadata_json or {}),
        "interactions": interactions,
    }

    # Canonicalize and size check
    canonical_bytes = _canonical_json(cassette_content).encode("utf-8")
    size_bytes = len(canonical_bytes)

    if size_bytes > settings.MAX_CASSETTE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": "CASSETTE_TOO_LARGE",
                "message": (
                    f"Cassette size ({size_bytes} bytes) exceeds maximum "
                    f"allowed limit of {settings.MAX_CASSETTE_SIZE_BYTES} bytes"
                ),
            },
        )

    fingerprint = hashlib.sha256(canonical_bytes).hexdigest()

    # If force=True and run already had a cassette, delete the old one or overwrite
    if force and run.cassette_id:
        old_cas = await db.get(Cassette, run.cassette_id)
        if old_cas:
            await db.delete(old_cas)
            await db.flush()

    cassette = Cassette(
        id=cassette_id,
        project_id=run.project_id,
        source_run_id=run.id,
        name=cassette_name,
        schema_version="1.0",
        fingerprint=fingerprint,
        content=cassette_content,
        size_bytes=size_bytes,
    )
    db.add(cassette)
    await db.flush()
    run.cassette_id = cassette.id
    await db.commit()
    await db.refresh(cassette)
    return cassette


async def get_cassette_by_id(db: AsyncSession, cassette_id: uuid.UUID) -> Cassette | None:
    """Retrieve cassette model by its primary key."""
    stmt = select(Cassette).where(Cassette.id == cassette_id)
    res = await db.execute(stmt)
    return res.scalar_one_or_none()


async def list_cassettes(
    db: AsyncSession,
    project: str | None = None,
    agent_name: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> CassetteListResponse:
    """List cassettes with cursor pagination and optional project/agent filtering."""
    query = (
        select(Cassette)
        .join(Project, Cassette.project_id == Project.id)
        .options(selectinload(Cassette.project))
    )

    filters = []
    if project:
        try:
            p_uuid = uuid.UUID(project)
            filters.append(or_(Project.id == p_uuid, Project.name == project))
        except ValueError:
            filters.append(Project.name == project)

    if agent_name:
        # Join source_run or check JSONB content
        filters.append(Cassette.content["agent_name"].astext == agent_name)

    if cursor:
        decoded = decode_cassette_cursor(cursor)
        if decoded:
            cursor_dt, cursor_id = decoded
            filters.append(
                or_(
                    Cassette.created_at < cursor_dt,
                    and_(Cassette.created_at == cursor_dt, Cassette.id < cursor_id),
                )
            )

    if filters:
        query = query.where(and_(*filters))

    query = query.order_by(Cassette.created_at.desc(), Cassette.id.desc())
    query = query.limit(min(limit, 100) + 1)

    result = await db.execute(query)
    cassettes = list(result.scalars().all())

    has_more = len(cassettes) > limit
    if has_more:
        cassettes = cassettes[:limit]

    summaries = [
        CassetteSummaryResponse(
            id=c.id,
            project_id=c.project_id,
            source_run_id=c.source_run_id,
            name=c.name,
            schema_version=c.schema_version,
            fingerprint=c.fingerprint,
            size_bytes=c.size_bytes,
            created_at=c.created_at,
        )
        for c in cassettes
    ]

    next_cursor = None
    if has_more and cassettes:
        last = cassettes[-1]
        next_cursor = encode_cassette_cursor(last.created_at, last.id)

    return CassetteListResponse(
        cassettes=summaries,
        total=len(summaries),
        next_cursor=next_cursor,
    )


async def delete_cassette(db: AsyncSession, cassette_id: uuid.UUID) -> bool:
    """Delete a cassette and decouple any runs pointing to it."""
    stmt = select(Cassette).where(Cassette.id == cassette_id)
    res = await db.execute(stmt)
    cassette = res.scalar_one_or_none()
    if cassette is None:
        return False

    # Disassociate runs referencing this cassette
    runs_stmt = select(Run).where(Run.cassette_id == cassette_id)
    runs_res = await db.execute(runs_stmt)
    runs = list(runs_res.scalars().all())
    for r in runs:
        r.cassette_id = None

    await db.delete(cassette)
    await db.commit()
    return True

"""Trace ingestion service handling idempotency, project creation, and run aggregates."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, Run, Span
from app.schemas import IngestRequest, IngestResponse, RunIngestItem, SpanIngestItem
from app.services.project_service import get_or_create_project

logger = logging.getLogger("agentprobe.ingest")


async def process_ingest_batch(db: AsyncSession, batch: IngestRequest) -> IngestResponse:
    """Ingest a batch of runs and spans idempotently, creating projects and aggregates."""
    project_cache: dict[str, Project] = {}
    touched_run_ids: set[uuid.UUID] = set()

    # Deduplicate runs within the same batch payload
    deduped_runs: dict[uuid.UUID, RunIngestItem] = {}
    for r_item in batch.runs:
        if r_item.id in deduped_runs:
            cur = deduped_runs[r_item.id]
            if cur.status in ("success", "error"):
                if r_item.status == "error":
                    cur.status = "error"
            else:
                cur.status = r_item.status
            if r_item.agent_name:
                cur.agent_name = r_item.agent_name
            if r_item.agent_version:
                cur.agent_version = r_item.agent_version
            if r_item.tags:
                for t in r_item.tags:
                    if t not in cur.tags:
                        cur.tags.append(t)
            if r_item.ended_at is not None:
                if cur.ended_at is None or r_item.ended_at > cur.ended_at:
                    cur.ended_at = r_item.ended_at
            if r_item.totals:
                cur.totals.prompt_tokens = max(
                    cur.totals.prompt_tokens, r_item.totals.prompt_tokens
                )
                cur.totals.completion_tokens = max(
                    cur.totals.completion_tokens, r_item.totals.completion_tokens
                )
                cur.totals.total_tokens = max(cur.totals.total_tokens, r_item.totals.total_tokens)
                cur.totals.cost = max(cur.totals.cost, r_item.totals.cost)
                cur.totals.step_count = max(cur.totals.step_count, r_item.totals.step_count)
                cur.totals.latency_ms = max(cur.totals.latency_ms, r_item.totals.latency_ms)
            if r_item.metadata:
                cur.metadata.update(r_item.metadata)
            if r_item.replay_of_run_id is not None:
                cur.replay_of_run_id = r_item.replay_of_run_id
            if r_item.cassette_id is not None:
                cur.cassette_id = r_item.cassette_id
            if r_item.replay_policy is not None:
                cur.replay_policy = r_item.replay_policy
        else:
            deduped_runs[r_item.id] = r_item.model_copy(deep=True)

    # Deduplicate spans within the same batch payload
    deduped_spans: dict[uuid.UUID, SpanIngestItem] = {}
    for s_item in batch.spans:
        if s_item.id in deduped_spans:
            cur_s = deduped_spans[s_item.id]
            if s_item.name:
                cur_s.name = s_item.name
            if s_item.type:
                cur_s.type = s_item.type
            if s_item.parent_id is not None:
                cur_s.parent_id = s_item.parent_id
            if s_item.input is not None:
                cur_s.input = s_item.input
            if s_item.output is not None:
                cur_s.output = s_item.output
            if s_item.error is not None:
                cur_s.error = s_item.error
            if s_item.ended_at is not None:
                if cur_s.ended_at is None or s_item.ended_at > cur_s.ended_at:
                    cur_s.ended_at = s_item.ended_at
            if s_item.duration_ms is not None:
                cur_s.duration_ms = max(cur_s.duration_ms or 0.0, s_item.duration_ms)
            if s_item.metadata:
                cur_s.metadata.update(s_item.metadata)
        else:
            deduped_spans[s_item.id] = s_item.model_copy(deep=True)

    # 1. Process deduplicated runs
    for run_in in deduped_runs.values():
        project_name = run_in.project or "default"
        if project_name not in project_cache:
            project_cache[project_name] = await get_or_create_project(db, project_name)
        project = project_cache[project_name]

        stmt = select(Run).where(Run.id == run_in.id)
        res = await db.execute(stmt)
        existing_run = res.scalar_one_or_none()

        if existing_run:
            if existing_run.status in ("success", "error"):
                if run_in.status == "error":
                    existing_run.status = "error"
            else:
                existing_run.status = run_in.status

            if run_in.agent_name:
                existing_run.agent_name = run_in.agent_name
            if run_in.agent_version:
                existing_run.agent_version = run_in.agent_version
            if run_in.tags:
                merged_tags = list(existing_run.tags or [])
                for t in run_in.tags:
                    if t not in merged_tags:
                        merged_tags.append(t)
                existing_run.tags = merged_tags

            if run_in.ended_at is not None:
                if existing_run.ended_at is None or run_in.ended_at > existing_run.ended_at:
                    existing_run.ended_at = run_in.ended_at

            if run_in.metadata:
                merged_meta = dict(existing_run.metadata_json or {})
                merged_meta.update(run_in.metadata)
                existing_run.metadata_json = merged_meta

            if run_in.replay_of_run_id is not None:
                existing_run.replay_of_run_id = run_in.replay_of_run_id
            if run_in.cassette_id is not None:
                existing_run.cassette_id = run_in.cassette_id
            if run_in.replay_policy is not None:
                existing_run.replay_policy = run_in.replay_policy

            if run_in.totals:
                existing_run.prompt_tokens = max(
                    existing_run.prompt_tokens, run_in.totals.prompt_tokens
                )
                existing_run.completion_tokens = max(
                    existing_run.completion_tokens, run_in.totals.completion_tokens
                )
                existing_run.total_tokens = max(
                    existing_run.total_tokens, run_in.totals.total_tokens
                )
                existing_run.cost = max(existing_run.cost, run_in.totals.cost)
                existing_run.step_count = max(existing_run.step_count, run_in.totals.step_count)
                existing_run.latency_ms = max(existing_run.latency_ms, run_in.totals.latency_ms)
        else:
            new_run = Run(
                id=run_in.id,
                project_id=project.id,
                agent_name=run_in.agent_name,
                agent_version=run_in.agent_version,
                status=run_in.status,
                tags=run_in.tags,
                started_at=run_in.started_at,
                ended_at=run_in.ended_at,
                prompt_tokens=run_in.totals.prompt_tokens if run_in.totals else 0,
                completion_tokens=run_in.totals.completion_tokens if run_in.totals else 0,
                total_tokens=run_in.totals.total_tokens if run_in.totals else 0,
                cost=run_in.totals.cost if run_in.totals else 0.0,
                latency_ms=run_in.totals.latency_ms if run_in.totals else 0.0,
                step_count=run_in.totals.step_count if run_in.totals else 0,
                metadata_json=dict(run_in.metadata),
                replay_of_run_id=run_in.replay_of_run_id,
                cassette_id=run_in.cassette_id,
                replay_policy=run_in.replay_policy,
            )
            db.add(new_run)

        touched_run_ids.add(run_in.id)

    await db.flush()

    # 2. Process deduplicated spans
    for span_in in deduped_spans.values():
        run_stmt = select(Run).where(Run.id == span_in.run_id)
        run_res = await db.execute(run_stmt)
        run_obj = run_res.scalar_one_or_none()

        if run_obj is None:
            default_proj = await get_or_create_project(db, "default")
            run_obj = Run(
                id=span_in.run_id,
                project_id=default_proj.id,
                agent_name=span_in.name,
                status="running",
                started_at=span_in.started_at,
            )
            db.add(run_obj)
            await db.flush()

        span_stmt = select(Span).where(Span.id == span_in.id)
        span_res = await db.execute(span_stmt)
        existing_span = span_res.scalar_one_or_none()

        if existing_span:
            if span_in.name:
                existing_span.name = span_in.name
            if span_in.type:
                existing_span.type = span_in.type
            if span_in.parent_id is not None:
                existing_span.parent_id = span_in.parent_id
            if span_in.input is not None:
                existing_span.input = span_in.input
            if span_in.output is not None:
                existing_span.output = span_in.output
            if span_in.error is not None:
                existing_span.error = span_in.error
            if span_in.ended_at is not None:
                if existing_span.ended_at is None or span_in.ended_at > existing_span.ended_at:
                    existing_span.ended_at = span_in.ended_at
            if span_in.duration_ms is not None:
                existing_span.duration_ms = max(
                    existing_span.duration_ms or 0.0, span_in.duration_ms
                )
            if span_in.metadata:
                merged_span_meta = dict(existing_span.metadata_json or {})
                merged_span_meta.update(span_in.metadata)
                existing_span.metadata_json = merged_span_meta
        else:
            new_span = Span(
                id=span_in.id,
                run_id=span_in.run_id,
                parent_id=span_in.parent_id,
                type=span_in.type,
                name=span_in.name,
                input=span_in.input,
                output=span_in.output,
                error=span_in.error,
                started_at=span_in.started_at,
                ended_at=span_in.ended_at,
                duration_ms=span_in.duration_ms,
                metadata_json=dict(span_in.metadata),
            )
            db.add(new_span)

        touched_run_ids.add(span_in.run_id)

    await db.flush()

    # 3. Synchronize aggregates for all affected runs
    for run_id in touched_run_ids:
        await update_run_aggregates(db, run_id)

    await db.commit()

    return IngestResponse(
        accepted_runs=len(batch.runs),
        accepted_spans=len(batch.spans),
        rejected=0,
    )


async def update_run_aggregates(db: AsyncSession, run_id: uuid.UUID) -> None:
    """Compute and update run metrics (tokens, cost, latency, step count, status) from spans."""
    run_stmt = select(Run).where(Run.id == run_id)
    run_res = await db.execute(run_stmt)
    run_obj = run_res.scalar_one_or_none()
    if run_obj is None:
        return

    spans_stmt = select(Span).where(Span.run_id == run_id)
    spans_res = await db.execute(spans_stmt)
    spans = list(spans_res.scalars().all())

    step_count = len(spans)
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    total_cost = 0.0
    total_latency_ms = 0.0
    has_error = False

    for s in spans:
        if s.duration_ms:
            total_latency_ms += s.duration_ms
        if s.error is not None:
            has_error = True

        usage = s.metadata_json.get("usage") if isinstance(s.metadata_json, dict) else None
        if isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_tokens", 0) or 0
            completion_tokens = usage.get("completion_tokens", 0) or 0
            tokens = usage.get("total_tokens", prompt_tokens + completion_tokens) or 0
            cost = usage.get("cost", 0.0) or 0.0

            total_prompt_tokens += int(prompt_tokens)
            total_completion_tokens += int(completion_tokens)
            total_tokens += int(tokens)
            total_cost += float(cost)

    run_obj.step_count = max(run_obj.step_count, step_count)
    run_obj.prompt_tokens = max(run_obj.prompt_tokens, total_prompt_tokens)
    run_obj.completion_tokens = max(run_obj.completion_tokens, total_completion_tokens)
    run_obj.total_tokens = max(run_obj.total_tokens, total_tokens)
    run_obj.cost = max(run_obj.cost, total_cost)
    run_obj.latency_ms = max(run_obj.latency_ms, total_latency_ms)

    if has_error:
        run_obj.status = "error"
    elif run_obj.status == "running":
        if run_obj.ended_at and (not spans or all(s.ended_at is not None for s in spans)):
            run_obj.status = "success"
        elif any(s.ended_at for s in spans) and run_obj.ended_at:
            run_obj.status = "success"
    elif run_obj.status == "success" and has_error:
        run_obj.status = "error"

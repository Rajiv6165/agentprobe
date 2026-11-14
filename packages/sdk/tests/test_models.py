"""Tests for AgentProbe SDK data models."""

import uuid

from agentprobe.models import (
    ErrorDetails,
    IngestBatch,
    Run,
    RunStatus,
    Span,
    SpanType,
)


def test_span_defaults() -> None:
    span = Span(run_id="run-1", type=SpanType.AGENT, name="agent_root")
    assert uuid.UUID(span.id)
    assert span.run_id == "run-1"
    assert span.parent_id is None
    assert span.type == SpanType.AGENT
    assert span.name == "agent_root"
    assert span.started_at.tzinfo is not None
    assert span.ended_at is None
    assert span.duration_ms is None
    assert span.metadata == {}
    assert span.error is None


def test_span_with_error_and_metadata() -> None:
    err = ErrorDetails(type="ValueError", message="Invalid argument", traceback="traceback details")
    span = Span(
        run_id="run-1",
        parent_id="parent-1",
        type=SpanType.TOOL,
        name="calculator",
        metadata={"tool_args": {"x": 1}},
        error=err,
    )
    assert span.parent_id == "parent-1"
    assert span.type == SpanType.TOOL
    assert span.error is not None
    assert span.error.type == "ValueError"
    assert span.metadata["tool_args"] == {"x": 1}


def test_run_defaults() -> None:
    run = Run(agent_name="support_agent")
    assert uuid.UUID(run.id)
    assert run.project == "default"
    assert run.agent_name == "support_agent"
    assert run.agent_version is None
    assert run.status == RunStatus.RUNNING
    assert run.tags == []
    assert run.totals.step_count == 0
    assert run.totals.cost == 0.0
    assert run.totals.total_tokens == 0


def test_ingest_batch() -> None:
    run = Run(agent_name="agent_1")
    span = Span(run_id=run.id, type=SpanType.AGENT, name="root")
    batch = IngestBatch(runs=[run], spans=[span])
    assert len(batch.runs) == 1
    assert len(batch.spans) == 1
    dump = batch.model_dump(mode="json")
    assert dump["runs"][0]["agent_name"] == "agent_1"
    assert dump["spans"][0]["name"] == "root"

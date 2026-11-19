"""Cassette builder utilities constructing cassettes from spans, JSONL, and live runs."""

from __future__ import annotations

import inspect
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from agentprobe.cassette.fingerprint import FingerprintNormalizer, compute_fingerprint
from agentprobe.cassette.models import Cassette, Interaction
from agentprobe.config import AgentProbeConfig
from agentprobe.models import Run, Span, SpanType
from agentprobe.tracer import Tracer
from agentprobe.transport import BaseExporter, sanitize_and_truncate


class _InMemoryExporter(BaseExporter):
    """Temporary in-memory trace accumulator for offline recording."""

    def __init__(self) -> None:
        self.runs: list[Run] = []
        self.spans: list[Span] = []

    def enqueue_run(self, run: Run) -> None:
        self.runs.append(run.model_copy(deep=True))

    def enqueue_span(self, span: Span) -> None:
        self.spans.append(span.model_copy(deep=True))

    def flush(self, timeout: float = 5.0) -> None:
        pass

    def close(self) -> None:
        pass


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


def from_spans(
    spans: list[Span],
    run: Run | None = None,
    name: str | None = None,
    normalizer: FingerprintNormalizer | None = None,
) -> Cassette:
    """Construct a Cassette from a collection of recorded Spans."""
    norm = normalizer or FingerprintNormalizer.default()

    # Identify root agent span (if any) to extract top-level input/output
    root_agent_spans = [
        s
        for s in spans
        if s.type in (SpanType.AGENT, "agent")
        and s.metadata.get("kind") != "capture"
        and s.parent_id is None
    ]
    if not root_agent_spans:
        root_agent_spans = [
            s
            for s in spans
            if s.metadata.get("kind") != "capture"
            and (
                (run is not None and s.name == run.agent_name)
                or s.type in (SpanType.AGENT, "agent")
            )
        ]
    root_agent_span = root_agent_spans[0] if root_agent_spans else None

    agent_input: dict[str, Any] = {}
    agent_output: Any = None
    agent_name: str = "unknown_agent"
    agent_version: str | None = None

    sanitize_cfg = AgentProbeConfig(exporter="none")

    if root_agent_span:
        agent_name = root_agent_span.name
        if isinstance(root_agent_span.input, dict) and "args" in root_agent_span.input:
            agent_input = sanitize_and_truncate(root_agent_span.input, sanitize_cfg)
        elif root_agent_span.input is not None:
            agent_input = sanitize_and_truncate(
                {"args": [root_agent_span.input], "kwargs": {}}, sanitize_cfg
            )
        agent_output = sanitize_and_truncate(root_agent_span.output, sanitize_cfg)

    if run:
        agent_name = run.agent_name or agent_name
        agent_version = run.agent_version or agent_version

    # Filter interaction spans: llm, tool, capture
    interactive_spans: list[Span] = []
    for s in spans:
        is_capture = s.metadata.get("kind") == "capture" or s.name == "capture"
        if is_capture or s.type in (SpanType.LLM, SpanType.TOOL, "llm", "tool"):
            interactive_spans.append(s)

    # Sort spans chronologically by started_at
    interactive_spans.sort(key=lambda x: x.started_at)

    # Map span id to interaction index
    span_id_to_index: dict[str, int] = {s.id: idx for idx, s in enumerate(interactive_spans)}

    interactions: list[Interaction] = []
    for idx, s in enumerate(interactive_spans):
        kind: Literal["llm", "tool", "capture"]
        if s.metadata.get("kind") == "capture" or s.name == "capture":
            kind = "capture"
        elif s.type in (SpanType.LLM, "llm"):
            kind = "llm"
        else:
            kind = "tool"

        parent_idx = span_id_to_index.get(s.parent_id) if s.parent_id else None

        clean_req = sanitize_and_truncate(s.input, sanitize_cfg)
        clean_resp = sanitize_and_truncate(s.output, sanitize_cfg)
        clean_meta = sanitize_and_truncate(s.metadata, sanitize_cfg)

        # Check for redactions
        is_redacted = (
            _has_redacted_marker(clean_req)
            or _has_redacted_marker(clean_resp)
            or _has_redacted_marker(clean_meta)
            or bool(s.metadata.get("redacted"))
        )

        fingerprint = compute_fingerprint(clean_req, normalizer=norm)

        interactions.append(
            Interaction(
                index=idx,
                kind=kind,
                name=s.name,
                request=clean_req,
                response=clean_resp,
                error=s.error,
                request_fingerprint=fingerprint,
                recorded_duration_ms=s.duration_ms or 0.0,
                parent_index=parent_idx,
                redacted=is_redacted,
                metadata=dict(clean_meta if isinstance(clean_meta, dict) else {}),
            )
        )

    cassette_name = name or (f"{agent_name}_cassette" if agent_name else "agent_cassette")
    source_run_id = run.id if run else (spans[0].run_id if spans else None)

    return Cassette(
        name=cassette_name,
        source_run_id=source_run_id,
        agent_name=agent_name,
        agent_version=agent_version,
        agent_input=agent_input,
        agent_output=agent_output,
        interactions=interactions,
    )


def from_jsonl(
    path: str | Path,
    run_id: str | None = None,
    normalizer: FingerprintNormalizer | None = None,
) -> Cassette:
    """Parse local exporter JSONL file and build a Cassette."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"JSONL trace file not found: {path}")

    runs: dict[str, Run] = {}
    spans_by_run: dict[str, list[Span]] = {}

    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            entry_type = entry.get("type")
            data = entry.get("data", {})
            if entry_type == "run":
                r = Run.model_validate(data)
                runs[r.id] = r
            elif entry_type == "span":
                s = Span.model_validate(data)
                spans_by_run.setdefault(s.run_id, []).append(s)

    target_run_id = run_id
    if target_run_id is None:
        if runs:
            # Most recently ended or started run
            target_run_id = max(
                runs.keys(), key=lambda rid: runs[rid].ended_at or runs[rid].started_at
            )
        elif spans_by_run:
            target_run_id = list(spans_by_run.keys())[-1]
        else:
            raise ValueError(f"No runs or spans found in {path}")

    target_spans = spans_by_run.get(target_run_id, [])
    target_run = runs.get(target_run_id)

    return from_spans(spans=target_spans, run=target_run, normalizer=normalizer)


def record_sync(
    agent_fn: Callable[..., Any],
    *args: Any,
    name: str | None = None,
    seed: int | None = None,
    normalizer: FingerprintNormalizer | None = None,
    **kwargs: Any,
) -> tuple[Any, Cassette]:
    """Execute synchronous agent function live, capture spans in memory, and produce Cassette."""
    if seed is not None:
        random.seed(seed)

    exporter = _InMemoryExporter()
    config = AgentProbeConfig(exporter="none")
    tracer = Tracer(config=config)
    tracer.exporter = exporter

    # Temporarily install in-memory tracer
    import agentprobe as ap

    old_tracer = ap._global_tracer
    ap._global_tracer = tracer

    try:
        result = agent_fn(*args, **kwargs)
        tracer.flush()
    finally:
        ap._global_tracer = old_tracer

    cassette = from_spans(
        spans=exporter.spans,
        run=exporter.runs[0] if exporter.runs else None,
        name=name,
        normalizer=normalizer,
    )
    cassette.seed = seed
    if not cassette.agent_output and result is not None:
        cassette.agent_output = result
    if not cassette.agent_input and (args or kwargs):
        cassette.agent_input = {"args": list(args), "kwargs": kwargs}

    return result, cassette


async def record_async(
    agent_fn: Callable[..., Any],
    *args: Any,
    name: str | None = None,
    seed: int | None = None,
    normalizer: FingerprintNormalizer | None = None,
    **kwargs: Any,
) -> tuple[Any, Cassette]:
    """Execute asynchronous agent function live, capture spans in memory, and produce Cassette."""
    if seed is not None:
        random.seed(seed)

    exporter = _InMemoryExporter()
    config = AgentProbeConfig(exporter="none")
    tracer = Tracer(config=config)
    tracer.exporter = exporter

    import agentprobe as ap

    old_tracer = ap._global_tracer
    ap._global_tracer = tracer

    try:
        if inspect.iscoroutinefunction(agent_fn):
            result = await agent_fn(*args, **kwargs)
        else:
            result = agent_fn(*args, **kwargs)
        tracer.flush()
    finally:
        ap._global_tracer = old_tracer

    cassette = from_spans(
        spans=exporter.spans,
        run=exporter.runs[0] if exporter.runs else None,
        name=name,
        normalizer=normalizer,
    )
    cassette.seed = seed
    if not cassette.agent_output and result is not None:
        cassette.agent_output = result
    if not cassette.agent_input and (args or kwargs):
        cassette.agent_input = {"args": list(args), "kwargs": kwargs}

    return result, cassette

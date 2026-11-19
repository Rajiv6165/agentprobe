"""Main execution engine for deterministic cassette replay."""

from __future__ import annotations

import inspect
import random
from collections.abc import Callable
from typing import Any, Literal

import agentprobe as ap
from agentprobe.cassette.fingerprint import FingerprintNormalizer
from agentprobe.cassette.models import Cassette
from agentprobe.context import reset_current_run, set_current_run
from agentprobe.models import Run, RunStatus, Span
from agentprobe.replay.matcher import BaseMatcher
from agentprobe.replay.policy import ReplayPolicy
from agentprobe.replay.result import ReplayResult
from agentprobe.replay.session import (
    ReplaySession,
    reset_current_replay_session,
    set_current_replay_session,
)
from agentprobe.replay.trace import Trace
from agentprobe.transport import BaseExporter


class _ReplayCollectorExporter(BaseExporter):
    """Accumulates spans and runs generated during a replay session."""

    def __init__(self, upstream_exporter: BaseExporter | None = None) -> None:
        self.upstream = upstream_exporter
        self.spans: list[Span] = []
        self.runs: list[Run] = []

    def enqueue_run(self, run: Run) -> None:
        self.runs.append(run.model_copy(deep=True))
        if self.upstream:
            self.upstream.enqueue_run(run)

    def enqueue_span(self, span: Span) -> None:
        self.spans.append(span.model_copy(deep=True))
        if self.upstream:
            self.upstream.enqueue_span(span)

    def flush(self, timeout: float = 5.0) -> None:
        if self.upstream:
            self.upstream.flush(timeout=timeout)

    def close(self) -> None:
        if self.upstream:
            self.upstream.close()


def replay(
    agent_fn: Callable[..., Any],
    cassette: Cassette,
    policy: ReplayPolicy | None = None,
    overrides: dict[str, Any] | None = None,
    on_miss: Literal["error", "live", "skip"] = "error",
    matcher: BaseMatcher | str | None = "fingerprint",
    normalizer: FingerprintNormalizer | None = None,
    seed: int | None = None,
    input_args: list[Any] | None = None,
    input_kwargs: dict[str, Any] | None = None,
) -> ReplayResult:
    """Replay an agent function against a recorded cassette with deterministic interception."""
    # 1. Determine input arguments
    if input_args is None and input_kwargs is None:
        args = cassette.agent_input.get("args", [])
        kwargs = cassette.agent_input.get("kwargs", {})
    else:
        args = input_args or []
        kwargs = input_kwargs or {}

    # 2. Seed handling
    effective_seed = seed if seed is not None else cassette.seed
    if effective_seed is not None:
        random.seed(effective_seed)

    # 3. Setup replay session
    session = ReplaySession(
        cassette=cassette,
        policy=policy,
        overrides=overrides,
        on_miss=on_miss,
        matcher=matcher,
        normalizer=normalizer,
    )
    token = set_current_replay_session(session)

    # 4. Configure tracing linkage
    tracer = ap.get_tracer()
    collector = _ReplayCollectorExporter(upstream_exporter=tracer.exporter)
    old_exporter = tracer.exporter
    tracer.exporter = collector

    # Top-level replay run metadata
    replay_run = Run(
        agent_name=cassette.agent_name,
        agent_version=cassette.agent_version,
        project=tracer.config.project,
        tags=["replay"],
        status=RunStatus.RUNNING,
        metadata={
            "source_run_id": cassette.source_run_id,
            "cassette_id": cassette.id,
            "replay_policy": session.policy.model_dump(),
        },
    )
    run_token = set_current_run(replay_run)
    tracer.exporter.enqueue_run(replay_run)

    output: Any = None
    try:
        output = agent_fn(*args, **kwargs)
        replay_run.status = RunStatus.SUCCESS
    except BaseException:
        replay_run.status = RunStatus.ERROR
        raise
    finally:
        tracer.exporter.enqueue_run(replay_run)
        tracer.flush()
        tracer.exporter = old_exporter
        reset_current_run(run_token)
        reset_current_replay_session(token)

    unconsumed = session.matcher.get_unconsumed()
    trace_obj = Trace(collector.spans)

    return ReplayResult(
        output=output,
        trace=trace_obj,
        divergences=list(session.divergences),
        unconsumed=unconsumed,
        matched_count=session.matched_count,
        live_count=session.live_count,
        mocked_count=session.mocked_count,
        redaction_warnings=session.redaction_warnings,
        new_run_id=replay_run.id,
    )


async def areplay(
    agent_fn: Callable[..., Any],
    cassette: Cassette,
    policy: ReplayPolicy | None = None,
    overrides: dict[str, Any] | None = None,
    on_miss: Literal["error", "live", "skip"] = "error",
    matcher: BaseMatcher | str | None = "fingerprint",
    normalizer: FingerprintNormalizer | None = None,
    seed: int | None = None,
    input_args: list[Any] | None = None,
    input_kwargs: dict[str, Any] | None = None,
) -> ReplayResult:
    """Asynchronously replay an agent function against a recorded cassette."""
    if input_args is None and input_kwargs is None:
        args = cassette.agent_input.get("args", [])
        kwargs = cassette.agent_input.get("kwargs", {})
    else:
        args = input_args or []
        kwargs = input_kwargs or {}

    effective_seed = seed if seed is not None else cassette.seed
    if effective_seed is not None:
        random.seed(effective_seed)

    session = ReplaySession(
        cassette=cassette,
        policy=policy,
        overrides=overrides,
        on_miss=on_miss,
        matcher=matcher,
        normalizer=normalizer,
    )
    token = set_current_replay_session(session)

    tracer = ap.get_tracer()
    collector = _ReplayCollectorExporter(upstream_exporter=tracer.exporter)
    old_exporter = tracer.exporter
    tracer.exporter = collector

    replay_run = Run(
        agent_name=cassette.agent_name,
        agent_version=cassette.agent_version,
        project=tracer.config.project,
        tags=["replay"],
        status=RunStatus.RUNNING,
        metadata={
            "source_run_id": cassette.source_run_id,
            "cassette_id": cassette.id,
            "replay_policy": session.policy.model_dump(),
        },
    )
    run_token = set_current_run(replay_run)
    tracer.exporter.enqueue_run(replay_run)

    output: Any = None
    try:
        if inspect.iscoroutinefunction(agent_fn):
            output = await agent_fn(*args, **kwargs)
        else:
            output = agent_fn(*args, **kwargs)
        replay_run.status = RunStatus.SUCCESS
    except BaseException:
        replay_run.status = RunStatus.ERROR
        raise
    finally:
        tracer.exporter.enqueue_run(replay_run)
        tracer.flush()
        tracer.exporter = old_exporter
        reset_current_run(run_token)
        reset_current_replay_session(token)

    unconsumed = session.matcher.get_unconsumed()
    trace_obj = Trace(collector.spans)

    return ReplayResult(
        output=output,
        trace=trace_obj,
        divergences=list(session.divergences),
        unconsumed=unconsumed,
        matched_count=session.matched_count,
        live_count=session.live_count,
        mocked_count=session.mocked_count,
        redaction_warnings=session.redaction_warnings,
        new_run_id=replay_run.id,
    )

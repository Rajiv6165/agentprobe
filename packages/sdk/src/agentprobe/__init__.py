"""AgentProbe: Open-source testing and tracing framework for AI agents."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any

from agentprobe.cassette import (
    Cassette,
    FingerprintNormalizer,
    Interaction,
    UnsupportedSchemaVersionError,
)
from agentprobe.config import AgentProbeConfig
from agentprobe.context import (
    copy_context_to_thread,
    get_current_run,
    get_current_span,
)
from agentprobe.models import (
    ErrorDetails,
    IngestBatch,
    Run,
    RunStatus,
    RunTotals,
    Span,
    SpanType,
)
from agentprobe.replay import (
    Divergence,
    Live,
    Mock,
    ReplayMismatchError,
    ReplayPolicy,
    ReplayResult,
    ReplaySession,
    Trace,
    areplay,
    replay,
)
from agentprobe.tracer import SpanContextManager, Tracer

__version__ = "0.1.0"

_global_tracer: Tracer | None = None


def init(
    project: str | None = None,
    endpoint: str | None = None,
    api_key: str | None = None,
    exporter: str | None = None,
    jsonl_path: str | None = None,
    max_queue_size: int | None = None,
    batch_size: int | None = None,
    flush_interval_seconds: float | None = None,
    max_payload_size_bytes: int | None = None,
    redact_secrets: bool | None = None,
    custom_redactors: list[Callable[[Any], Any]] | None = None,
) -> Tracer:
    """Initialize the global AgentProbe tracer instance with configuration options."""
    global _global_tracer
    config = AgentProbeConfig.from_env(
        project=project,
        endpoint=endpoint,
        api_key=api_key,
        exporter=exporter,
        jsonl_path=jsonl_path,
        max_queue_size=max_queue_size,
        batch_size=batch_size,
        flush_interval_seconds=flush_interval_seconds,
        max_payload_size_bytes=max_payload_size_bytes,
        redact_secrets=redact_secrets,
        custom_redactors=custom_redactors or [],
    )
    if _global_tracer is not None:
        _global_tracer.shutdown()
    _global_tracer = Tracer(config=config)
    return _global_tracer


def get_tracer() -> Tracer:
    """Retrieve the global Tracer instance, creating a default one if uninitialized."""
    global _global_tracer
    if _global_tracer is None:
        _global_tracer = Tracer()
    return _global_tracer


def span(
    name: str,
    type: SpanType | str = SpanType.AGENT,
    parent_id: str | None = None,
    run_id: str | None = None,
    **metadata: Any,
) -> SpanContextManager:
    """Create a new span within the current trace context."""
    return get_tracer().span(
        name=name,
        type=type,
        parent_id=parent_id,
        run_id=run_id,
        **metadata,
    )


def trace_agent(
    name: str | None = None,
    version: str | None = None,
    project: str | None = None,
    tags: list[str] | None = None,
) -> Callable[..., Any]:
    """Decorator to instrument an agent execution run."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await get_tracer().trace_agent(
                    name=name, version=version, project=project, tags=tags
                )(func)(*args, **kwargs)

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return get_tracer().trace_agent(name=name, version=version, project=project, tags=tags)(
                func
            )(*args, **kwargs)

        return sync_wrapper

    return decorator


def trace_tool(name: str | None = None) -> Callable[..., Any]:
    """Decorator to instrument tool execution within a run."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await get_tracer().trace_tool(name=name)(func)(*args, **kwargs)

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return get_tracer().trace_tool(name=name)(func)(*args, **kwargs)

        return sync_wrapper

    return decorator


def trace_llm(name: str | None = None, model: str | None = None) -> Callable[..., Any]:
    """Decorator to instrument and intercept LLM invocations."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await get_tracer().trace_llm(name=name, model=model)(func)(*args, **kwargs)

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return get_tracer().trace_llm(name=name, model=model)(func)(*args, **kwargs)

        return sync_wrapper

    return decorator


def capture(name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Capture a non-deterministic value, replaying it deterministically during replay."""
    return get_tracer().capture(name, fn, *args, **kwargs)


def flush(timeout: float = 5.0) -> None:
    """Flush pending traces to the configured exporter."""
    get_tracer().flush(timeout=timeout)


def shutdown() -> None:
    """Shut down the active tracer and close its background exporters."""
    global _global_tracer
    if _global_tracer is not None:
        _global_tracer.shutdown()
        _global_tracer = None


__all__ = [
    "Cassette",
    "Divergence",
    "ErrorDetails",
    "FingerprintNormalizer",
    "IngestBatch",
    "Interaction",
    "Live",
    "Mock",
    "ReplayMismatchError",
    "ReplayPolicy",
    "ReplayResult",
    "ReplaySession",
    "Run",
    "RunStatus",
    "RunTotals",
    "Span",
    "SpanContextManager",
    "SpanType",
    "Trace",
    "Tracer",
    "UnsupportedSchemaVersionError",
    "areplay",
    "capture",
    "copy_context_to_thread",
    "flush",
    "get_current_run",
    "get_current_span",
    "get_tracer",
    "init",
    "replay",
    "shutdown",
    "span",
    "trace_agent",
    "trace_llm",
    "trace_tool",
]

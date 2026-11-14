"""Tracer, execution span context managers, and instrumentation decorators."""

from __future__ import annotations

import functools
import inspect
import logging
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from types import TracebackType
from typing import Any, Literal, ParamSpec, TypeVar

from agentprobe.config import AgentProbeConfig
from agentprobe.context import (
    get_current_run,
    get_current_span,
    reset_current_run,
    reset_current_span,
    set_current_run,
    set_current_span,
)
from agentprobe.models import ErrorDetails, Run, RunStatus, Span, SpanType
from agentprobe.transport import BaseExporter, create_exporter

logger = logging.getLogger("agentprobe")

P = ParamSpec("P")
R = TypeVar("R")


class SpanContextManager:
    """Synchronous and asynchronous context manager for span lifecycle instrumentation."""

    def __init__(
        self,
        tracer: Tracer,
        name: str,
        span_type: SpanType | str = SpanType.AGENT,
        parent_id: str | None = None,
        run_id: str | None = None,
        **metadata: Any,
    ) -> None:
        self.tracer = tracer
        self.name = name
        self.span_type = SpanType(span_type)
        self.explicit_parent_id = parent_id
        self.explicit_run_id = run_id
        self.initial_metadata = metadata

        self.span: Span | None = None
        self._span_token: Any = None
        self._auto_run: Run | None = None
        self._run_token: Any = None

    def set_input(self, input_data: Any) -> None:
        """Record input payload on the active span."""
        if self.span:
            self.span.input = input_data

    def set_output(self, output_data: Any) -> None:
        """Record output payload on the active span."""
        if self.span:
            self.span.output = output_data

    def set_metadata(self, key: str, value: Any) -> None:
        """Set a single metadata key-value pair."""
        if self.span:
            self.span.metadata[key] = value

    def update_metadata(self, values: dict[str, Any]) -> None:
        """Merge a dictionary into span metadata."""
        if self.span:
            self.span.metadata.update(values)

    def set_usage(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int | None = None,
        cost: float = 0.0,
    ) -> None:
        """Record token consumption and estimated cost on the span and current run."""
        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens

        usage_data = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost": cost,
        }
        self.set_metadata("usage", usage_data)

        # Propagate usage to active run totals if present
        current_run = get_current_run()
        if current_run:
            current_run.totals.prompt_tokens += prompt_tokens
            current_run.totals.completion_tokens += completion_tokens
            current_run.totals.total_tokens += total_tokens
            current_run.totals.cost += cost

    def set_error(self, exc: BaseException) -> None:
        """Capture exception details onto the span."""
        if self.span:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            self.span.error = ErrorDetails(
                type=type(exc).__name__,
                message=str(exc),
                traceback=tb,
            )

    def __enter__(self) -> SpanContextManager:
        active_run = get_current_run()
        if active_run is None:
            # Create an automatic fallback run for orphaned spans
            if self.explicit_run_id:
                run_id = self.explicit_run_id
            else:
                self._auto_run = Run(
                    agent_name=self.name,
                    project=self.tracer.config.project,
                    status=RunStatus.RUNNING,
                )
                self._run_token = set_current_run(self._auto_run)
                run_id = self._auto_run.id
                self.tracer.exporter.enqueue_run(self._auto_run)
        else:
            run_id = active_run.id

        parent_span = get_current_span()
        parent_id = self.explicit_parent_id or (parent_span.id if parent_span else None)

        self.span = Span(
            run_id=run_id,
            parent_id=parent_id,
            type=self.span_type,
            name=self.name,
            started_at=datetime.now(timezone.utc),
            metadata=dict(self.initial_metadata),
        )

        self._span_token = set_current_span(self.span)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> Literal[False]:
        if self.span is None:
            return False

        now = datetime.now(timezone.utc)
        self.span.ended_at = now
        duration = (now - self.span.started_at).total_seconds() * 1000.0
        self.span.duration_ms = max(0.0, duration)

        active_run = get_current_run()
        if active_run:
            active_run.totals.step_count += 1
            active_run.totals.latency_ms += self.span.duration_ms

        if exc_val is not None:
            self.set_error(exc_val)
            if active_run:
                active_run.status = RunStatus.ERROR

        # Enqueue completed span for export
        self.tracer.exporter.enqueue_span(self.span)

        # Restore span context
        if self._span_token is not None:
            reset_current_span(self._span_token)

        # If we created an auto-run, close and enqueue it
        if self._auto_run is not None:
            self._auto_run.ended_at = now
            if self._auto_run.status == RunStatus.RUNNING:
                self._auto_run.status = RunStatus.SUCCESS if exc_val is None else RunStatus.ERROR
            self.tracer.exporter.enqueue_run(self._auto_run)
            if self._run_token is not None:
                reset_current_run(self._run_token)

        # Never suppress exceptions: always bubble up unchanged
        return False

    async def __aenter__(self) -> SpanContextManager:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> Literal[False]:
        return self.__exit__(exc_type, exc_val, exc_tb)


class Tracer:
    """Core tracer instance responsible for managing runs, spans, and exporters."""

    def __init__(self, config: AgentProbeConfig | None = None) -> None:
        self.config = config or AgentProbeConfig.from_env()
        self.exporter: BaseExporter = create_exporter(self.config)

    def span(
        self,
        name: str,
        type: SpanType | str = SpanType.AGENT,
        parent_id: str | None = None,
        run_id: str | None = None,
        **metadata: Any,
    ) -> SpanContextManager:
        """Create a new span context manager."""
        return SpanContextManager(
            tracer=self,
            name=name,
            span_type=type,
            parent_id=parent_id,
            run_id=run_id,
            **metadata,
        )

    def trace_agent(
        self,
        name: str | None = None,
        version: str | None = None,
        project: str | None = None,
        tags: list[str] | None = None,
    ) -> Callable[[Callable[P, R]], Callable[P, R]]:
        """Decorator for tracing top-level agent execution runs."""

        def decorator(func: Callable[P, R]) -> Callable[P, R]:
            agent_name = name or func.__name__
            agent_version = version
            run_project = project or self.config.project
            run_tags = tags or []

            if inspect.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                    run = Run(
                        agent_name=agent_name,
                        agent_version=agent_version,
                        project=run_project,
                        tags=list(run_tags),
                        started_at=datetime.now(timezone.utc),
                        status=RunStatus.RUNNING,
                    )
                    run_token = set_current_run(run)
                    self.exporter.enqueue_run(run)

                    try:
                        async with self.span(
                            name=agent_name,
                            type=SpanType.AGENT,
                            run_id=run.id,
                        ) as s:
                            s.set_input({"args": args, "kwargs": kwargs})
                            res = await func(*args, **kwargs)  # type: ignore[misc]
                            s.set_output(res)
                            run.status = RunStatus.SUCCESS
                            return res  # type: ignore[no-any-return]
                    except BaseException:
                        run.status = RunStatus.ERROR
                        raise
                    finally:
                        run.ended_at = datetime.now(timezone.utc)
                        self.exporter.enqueue_run(run)
                        reset_current_run(run_token)

                return async_wrapper  # type: ignore[return-value]

            @functools.wraps(func)
            def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                run = Run(
                    agent_name=agent_name,
                    agent_version=agent_version,
                    project=run_project,
                    tags=list(run_tags),
                    started_at=datetime.now(timezone.utc),
                    status=RunStatus.RUNNING,
                )
                run_token = set_current_run(run)
                self.exporter.enqueue_run(run)

                try:
                    with self.span(
                        name=agent_name,
                        type=SpanType.AGENT,
                        run_id=run.id,
                    ) as s:
                        s.set_input({"args": args, "kwargs": kwargs})
                        res = func(*args, **kwargs)
                        s.set_output(res)
                        run.status = RunStatus.SUCCESS
                        return res
                except BaseException as exc:
                    run.status = RunStatus.ERROR
                    raise exc
                finally:
                    run.ended_at = datetime.now(timezone.utc)
                    self.exporter.enqueue_run(run)
                    reset_current_run(run_token)

            return sync_wrapper

        return decorator

    def trace_tool(
        self,
        name: str | None = None,
    ) -> Callable[[Callable[P, R]], Callable[P, R]]:
        """Decorator for tracing tool invocations within an agent run."""

        def decorator(func: Callable[P, R]) -> Callable[P, R]:
            tool_name = name or func.__name__

            if inspect.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                    tool_args = {"args": args, "kwargs": kwargs}
                    async with self.span(
                        name=tool_name,
                        type=SpanType.TOOL,
                        tool_args=tool_args,
                    ) as s:
                        s.set_input(tool_args)
                        res = await func(*args, **kwargs)  # type: ignore[misc]
                        s.set_output(res)
                        return res  # type: ignore[no-any-return]

                return async_wrapper  # type: ignore[return-value]

            @functools.wraps(func)
            def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                tool_args = {"args": args, "kwargs": kwargs}
                with self.span(
                    name=tool_name,
                    type=SpanType.TOOL,
                    tool_args=tool_args,
                ) as s:
                    s.set_input(tool_args)
                    res = func(*args, **kwargs)
                    s.set_output(res)
                    return res

            return sync_wrapper

        return decorator

    def flush(self, timeout: float = 5.0) -> None:
        """Flush pending runs and spans to destination exporter."""
        self.exporter.flush(timeout=timeout)

    def shutdown(self) -> None:
        """Close exporter and flush remaining items."""
        self.exporter.close()

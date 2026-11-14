"""Contextvars-based execution context propagation for runs and spans.

Threading and Async Context Propagation:
----------------------------------------
In Python, contextvars propagate automatically to child asyncio tasks
created via asyncio.create_task() or asyncio.gather().

For OS threads (threading.Thread), contextvars are NOT inherited automatically
by the runtime. To propagate the active AgentProbe run and span context to a
new thread, wrap the target callable using `copy_context_to_thread` or invoke
`contextvars.copy_context().run(func, *args, **kwargs)`.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable
from typing import Any, TypeVar

from agentprobe.models import Run, Span

T = TypeVar("T")

_current_run: contextvars.ContextVar[Run | None] = contextvars.ContextVar(
    "agentprobe_current_run", default=None
)
_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "agentprobe_current_span", default=None
)


def get_current_run() -> Run | None:
    """Return the active Run in the current context, if any."""
    return _current_run.get()


def set_current_run(run: Run | None) -> contextvars.Token[Run | None]:
    """Set the active Run in the current context and return a restore token."""
    return _current_run.set(run)


def reset_current_run(token: contextvars.Token[Run | None]) -> None:
    """Reset the active Run using a restore token."""
    _current_run.reset(token)


def get_current_span() -> Span | None:
    """Return the active Span in the current context, if any."""
    return _current_span.get()


def set_current_span(span: Span | None) -> contextvars.Token[Span | None]:
    """Set the active Span in the current context and return a restore token."""
    return _current_span.set(span)


def reset_current_span(token: contextvars.Token[Span | None]) -> None:
    """Reset the active Span using a restore token."""
    _current_span.reset(token)


def copy_context_to_thread(target: Callable[..., T]) -> Callable[..., T]:
    """Wrap a target function to run within a snapshot of the current contextvars.

    Use this when spawning threads via threading.Thread(target=...) to ensure
    active run and span IDs correctly propagate to child threads:

        thread = threading.Thread(target=copy_context_to_thread(worker_fn))
        thread.start()
    """
    ctx = contextvars.copy_context()

    def wrapper(*args: Any, **kwargs: Any) -> T:
        return ctx.run(target, *args, **kwargs)

    return wrapper

"""Lightweight Trace abstraction over execution spans for inspection and assertions."""

from __future__ import annotations

from typing import Any

from agentprobe.models import Span, SpanType


class Trace:
    """Read-only container and inspection helper over a collection of execution Spans."""

    def __init__(self, spans: list[Span]) -> None:
        # Keep spans sorted by start time
        self._spans = sorted(spans, key=lambda s: s.started_at)

    @property
    def spans(self) -> list[Span]:
        """All spans recorded during execution."""
        return list(self._spans)

    @property
    def steps(self) -> list[Span]:
        """Ordered sequence of execution step spans (LLM, tool, capture)."""
        return [
            s
            for s in self._spans
            if s.type in (SpanType.LLM, SpanType.TOOL, "llm", "tool")
            or s.metadata.get("kind") == "capture"
        ]

    def llm_calls(self) -> list[Span]:
        """Return all spans representing LLM model inferences."""
        return [s for s in self._spans if s.type in (SpanType.LLM, "llm")]

    def tool_calls(self) -> list[Span]:
        """Return all spans representing tool invocations."""
        return [s for s in self._spans if s.type in (SpanType.TOOL, "tool")]

    def tool_names(self) -> list[str]:
        """Return the sequence of tool names executed during the run."""
        return [s.name for s in self.tool_calls()]

    def find_spans(
        self,
        name: str | None = None,
        span_type: SpanType | str | None = None,
    ) -> list[Span]:
        """Filter spans by name or type."""
        res = self._spans
        if name is not None:
            res = [s for s in res if s.name == name]
        if span_type is not None:
            expected = span_type.value if isinstance(span_type, SpanType) else span_type
            res = [
                s
                for s in res
                if (s.type.value if isinstance(s.type, SpanType) else s.type) == expected
            ]
        return res

    @property
    def total_tokens(self) -> int:
        """Sum of all tokens reported across spans."""
        total = 0
        for s in self._spans:
            usage = s.metadata.get("usage")
            if isinstance(usage, dict):
                total += int(usage.get("total_tokens", 0) or 0)
        return total

    @property
    def total_cost(self) -> float:
        """Total estimated cost accumulated across spans."""
        cost = 0.0
        for s in self._spans:
            usage = s.metadata.get("usage")
            if isinstance(usage, dict):
                cost += float(usage.get("cost", 0.0) or 0.0)
        return cost

    def __len__(self) -> int:
        return len(self._spans)

    def __iter__(self) -> Any:
        return iter(self._spans)

    def __repr__(self) -> str:
        return f"Trace(spans={len(self._spans)}, tools={self.tool_names()})"

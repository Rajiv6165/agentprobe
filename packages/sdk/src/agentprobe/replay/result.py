"""Results, divergences, and mismatch exceptions produced by replay executions."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from typing import Any

from agentprobe.cassette.models import Interaction
from agentprobe.replay.trace import Trace


@dataclass
class Divergence:
    """Record of an execution divergence during replay where actual behavior did not match the cassette."""

    index: int
    kind: str
    name: str
    expected_fingerprint: str | None
    actual_fingerprint: str
    resolution: str  # "error", "live", "mock", "skip"
    expected_request: Any = None
    actual_request: Any = None
    diff: str | None = None


class ReplayMismatchError(RuntimeError):
    """Raised when an agent invocation fails to match any recorded cassette interaction under strict replay."""

    def __init__(
        self,
        divergence: Divergence,
        nearest_interactions: list[Interaction] | None = None,
    ) -> None:
        self.divergence = divergence
        self.nearest_interactions = nearest_interactions or []

        # Build clean, human-readable diff message
        lines: list[str] = [
            f"AgentProbe Replay Divergence at step #{divergence.index} ({divergence.kind}: '{divergence.name}'):",
            f"  Expected fingerprint: {divergence.expected_fingerprint or '<None>'}",
            f"  Actual fingerprint:   {divergence.actual_fingerprint}",
        ]

        if divergence.diff:
            lines.append("  Structured Request Diff:")
            for diff_line in divergence.diff.splitlines():
                lines.append(f"    {diff_line}")

        if self.nearest_interactions:
            lines.append("  Closest recorded interactions in cassette:")
            for ni in self.nearest_interactions[:3]:
                lines.append(
                    f"    - Step #{ni.index} ({ni.kind}: '{ni.name}'): fingerprint={ni.request_fingerprint[:12]}..."
                )

        super().__init__("\n".join(lines))


def compute_readable_diff(expected: Any, actual: Any) -> str:
    """Generate a unified text diff between expected and actual request structures."""
    try:
        exp_str = json.dumps(expected, indent=2, sort_keys=True, default=str)
    except Exception:
        exp_str = repr(expected)

    try:
        act_str = json.dumps(actual, indent=2, sort_keys=True, default=str)
    except Exception:
        act_str = repr(actual)

    diff = difflib.unified_diff(
        exp_str.splitlines(keepends=True),
        act_str.splitlines(keepends=True),
        fromfile="recorded_request",
        tofile="actual_request",
        n=2,
    )
    return "".join(diff).strip()


@dataclass
class ReplayResult:
    """Outcome of replaying an agent execution against a recorded cassette."""

    output: Any
    trace: Trace
    divergences: list[Divergence] = field(default_factory=list)
    unconsumed: list[Interaction] = field(default_factory=list)
    matched_count: int = 0
    live_count: int = 0
    mocked_count: int = 0
    redaction_warnings: int = 0
    new_run_id: str = ""

    @property
    def has_divergence(self) -> bool:
        """True if any runtime divergences or unconsumed interactions occurred."""
        return len(self.divergences) > 0 or len(self.unconsumed) > 0

    def summary(self) -> str:
        """Format a human-readable summary table of the replay outcome."""
        lines: list[str] = [
            "=" * 70,
            " AgentProbe Replay Summary",
            "=" * 70,
            f"  Run ID:              {self.new_run_id}",
            f"  Matched Steps:       {self.matched_count}",
            f"  Live Executions:     {self.live_count}",
            f"  Mocked Overrides:    {self.mocked_count}",
            f"  Redaction Warnings:  {self.redaction_warnings}",
            f"  Divergences:         {len(self.divergences)}",
            f"  Unconsumed Steps:    {len(self.unconsumed)}",
            f"  Total Trace Steps:   {len(self.trace.steps)}",
            "-" * 70,
        ]

        if self.divergences:
            lines.append("  Detected Divergences:")
            for d in self.divergences:
                lines.append(
                    f"    * Step #{d.index} [{d.kind}] '{d.name}' -> resolution: {d.resolution}"
                )

        if self.unconsumed:
            lines.append("  Unconsumed Recorded Interactions (skipped by agent):")
            for u in self.unconsumed:
                lines.append(f"    * Step #{u.index} [{u.kind}] '{u.name}'")

        lines.append("=" * 70)
        return "\n".join(lines)

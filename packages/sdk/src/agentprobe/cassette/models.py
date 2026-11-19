"""Pydantic v2 models and storage methods for AgentProbe cassettes."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentprobe.models import ErrorDetails, Run, Span

if TYPE_CHECKING:
    from agentprobe.cassette.fingerprint import FingerprintNormalizer

CURRENT_SCHEMA_VERSION = "1.0"


class UnsupportedSchemaVersionError(ValueError):
    """Raised when loading a cassette with an unsupported or unrecognized schema version."""


class Interaction(BaseModel):
    """An individual recorded interaction within a cassette (LLM call, tool invocation, or capture)."""

    model_config = ConfigDict(extra="ignore")

    index: int = Field(ge=0, description="Sequential position of the interaction")
    kind: Literal["llm", "tool", "capture"] = Field(
        description="Category of interaction: llm, tool, or captured non-deterministic value"
    )
    name: str = Field(description="Name of the model, tool function, or captured operation")
    request: Any = Field(default=None, description="Input arguments or prompt structure")
    response: Any = Field(default=None, description="Returned result or model output")
    error: ErrorDetails | None = Field(
        default=None, description="Captured exception details if failed"
    )
    request_fingerprint: str = Field(
        description="Deterministic SHA-256 digest of normalized request"
    )
    recorded_duration_ms: float = Field(
        default=0.0, ge=0.0, description="Original execution duration"
    )
    parent_index: int | None = Field(
        default=None, description="Index of enclosing parent interaction"
    )
    redacted: bool = Field(
        default=False, description="Flag indicating sensitive values were redacted"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Metadata including model and usage"
    )


class Cassette(BaseModel):
    """Portable deterministic recording of an agent execution run."""

    model_config = ConfigDict(extra="ignore")

    schema_version: str = Field(
        default=CURRENT_SCHEMA_VERSION, description="Cassette format specification version"
    )
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), description="Unique cassette identifier"
    )
    name: str = Field(description="Human-readable cassette title or scenario label")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="UTC timestamp of creation"
    )
    source_run_id: str | None = Field(
        default=None, description="Original AgentProbe run ID if recorded"
    )
    agent_name: str = Field(description="Name of the instrumented agent")
    agent_version: str | None = Field(default=None, description="Version of the agent")
    agent_input: dict[str, Any] = Field(
        default_factory=dict,
        description="Top-level invocation arguments: {'args': [...], 'kwargs': {...}}",
    )
    agent_output: Any = Field(default=None, description="Final agent return value")
    seed: int | None = Field(
        default=None, description="Random seed used for deterministic execution"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary run or environment tags"
    )
    interactions: list[Interaction] = Field(
        default_factory=list, description="Ordered sequence of intercepted interactions"
    )

    def save(self, path: str | Path) -> None:
        """Serialize cassette to a formatted JSON file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        dump = self.model_dump(mode="json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(dump, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: str | Path) -> Cassette:
        """Load and validate a cassette from a JSON file, checking schema version."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Cassette file not found: {path}")

        with open(p, encoding="utf-8") as f:
            raw_data = json.load(f)

        version = raw_data.get("schema_version")
        if version != CURRENT_SCHEMA_VERSION:
            raise UnsupportedSchemaVersionError(
                f"Unsupported cassette schema version '{version}'. Supported version is '{CURRENT_SCHEMA_VERSION}'."
            )

        return cls.model_validate(raw_data)

    @classmethod
    def from_spans(
        cls,
        spans: list[Span],
        run: Run | None = None,
        name: str | None = None,
        normalizer: FingerprintNormalizer | None = None,
    ) -> Cassette:
        """Construct a Cassette from a list of Spans and optional Run."""
        from agentprobe.cassette.builder import from_spans

        return from_spans(spans=spans, run=run, name=name, normalizer=normalizer)

    @classmethod
    def from_jsonl(
        cls,
        path: str | Path,
        run_id: str | None = None,
        normalizer: FingerprintNormalizer | None = None,
    ) -> Cassette:
        """Construct a Cassette from local JSONL exporter output."""
        from agentprobe.cassette.builder import from_jsonl

        return from_jsonl(path=path, run_id=run_id, normalizer=normalizer)

    @classmethod
    def record(
        cls,
        agent_fn: Callable[..., Any],
        *args: Any,
        name: str | None = None,
        seed: int | None = None,
        normalizer: FingerprintNormalizer | None = None,
        **kwargs: Any,
    ) -> tuple[Any, Cassette]:
        """Execute the agent live, capture all spans in memory, and produce a Cassette."""
        from agentprobe.cassette.builder import record_sync

        return record_sync(agent_fn, *args, name=name, seed=seed, normalizer=normalizer, **kwargs)

    @classmethod
    async def arecord(
        cls,
        agent_fn: Callable[..., Any],
        *args: Any,
        name: str | None = None,
        seed: int | None = None,
        normalizer: FingerprintNormalizer | None = None,
        **kwargs: Any,
    ) -> tuple[Any, Cassette]:
        """Asynchronously execute agent live, capture spans in memory, and produce a Cassette."""
        from agentprobe.cassette.builder import record_async

        return await record_async(
            agent_fn, *args, name=name, seed=seed, normalizer=normalizer, **kwargs
        )

"""Data models for AgentProbe SDK."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SpanType(str, Enum):
    """Supported span types."""

    LLM = "llm"
    TOOL = "tool"
    AGENT = "agent"


class RunStatus(str, Enum):
    """Run lifecycle statuses."""

    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


class ErrorDetails(BaseModel):
    """Captured exception details."""

    model_config = ConfigDict(extra="ignore")

    type: str = Field(description="Exception class name")
    message: str = Field(description="String representation of exception")
    traceback: str | None = Field(default=None, description="Formatted traceback")


class RunTotals(BaseModel):
    """Aggregated run performance and usage metrics."""

    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost: float = Field(default=0.0, ge=0.0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    step_count: int = Field(default=0, ge=0)


class Span(BaseModel):
    """An individual execution span (agent, tool, or LLM call)."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    parent_id: str | None = None
    type: SpanType
    name: str
    input: Any = None
    output: Any = None
    error: ErrorDetails | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None
    duration_ms: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Run(BaseModel):
    """An execution run spanning one or more steps."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project: str = "default"
    agent_name: str
    agent_version: str | None = None
    status: RunStatus = RunStatus.RUNNING
    tags: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None
    totals: RunTotals = Field(default_factory=RunTotals)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestBatch(BaseModel):
    """Batch payload sent to the ingestion endpoint."""

    model_config = ConfigDict(extra="ignore")

    runs: list[Run] = Field(default_factory=list)
    spans: list[Span] = Field(default_factory=list)

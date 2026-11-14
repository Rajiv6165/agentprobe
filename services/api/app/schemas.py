"""Pydantic request and response schemas for AgentProbe REST API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    """Structured error information."""

    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    """Standardized error envelope returned across all API endpoints."""

    error: ErrorDetail


class ProjectResponse(BaseModel):
    """Project entity summary."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime
    run_count: int = 0


class ProjectListResponse(BaseModel):
    """Collection of projects."""

    projects: list[ProjectResponse]


class RunTotalsSchema(BaseModel):
    """Aggregated usage and latency totals."""

    model_config = ConfigDict(from_attributes=True)

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    latency_ms: float = 0.0
    step_count: int = 0


class RunIngestItem(BaseModel):
    """Run update or registration item submitted during trace ingestion."""

    id: uuid.UUID
    project: str = "default"
    agent_name: str
    agent_version: str | None = None
    status: str = "running"
    tags: list[str] = Field(default_factory=list)
    started_at: datetime
    ended_at: datetime | None = None
    totals: RunTotalsSchema = Field(default_factory=RunTotalsSchema)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SpanIngestItem(BaseModel):
    """Span registration item submitted during trace ingestion."""

    id: uuid.UUID
    run_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    type: str  # llm | tool | agent
    name: str
    input: Any = None
    output: Any = None
    error: dict[str, Any] | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestRequest(BaseModel):
    """Batch payload submitted to /v1/ingest."""

    runs: list[RunIngestItem] = Field(default_factory=list)
    spans: list[SpanIngestItem] = Field(default_factory=list)


class IngestResponse(BaseModel):
    """Response confirming processed counts from ingestion."""

    accepted_runs: int
    accepted_spans: int
    rejected: int = 0


class RunResponse(BaseModel):
    """Run details including computed aggregates."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    agent_name: str
    agent_version: str | None = None
    status: str
    tags: list[str]
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: float | None = None
    totals: RunTotalsSchema
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RunListResponse(BaseModel):
    """Cursor-paginated runs query response."""

    runs: list[RunResponse]
    next_cursor: str | None = None
    has_more: bool = False


class SpanResponse(BaseModel):
    """Flat span representation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    type: str
    name: str
    input: Any = None
    output: Any = None
    error: dict[str, Any] | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class SpanTreeNode(BaseModel):
    """Nested hierarchy node representing a span and its nested children."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    type: str
    name: str
    input: Any = None
    output: Any = None
    error: dict[str, Any] | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    children: list[SpanTreeNode] = Field(default_factory=list)

"""Tests for AgentProbe Tracer, context managers, and decorators."""

import asyncio

import pytest

import agentprobe as ap
from agentprobe.models import Run, RunStatus, Span, SpanType
from agentprobe.transport import BaseExporter


class RecordingExporter(BaseExporter):
    """Test exporter that stores runs and spans in memory for assertions."""

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


@pytest.fixture(autouse=True)
def setup_recording_tracer() -> RecordingExporter:
    exporter = RecordingExporter()
    tracer = ap.init(project="test-project", exporter="none")
    tracer.exporter = exporter
    return exporter


def test_span_context_manager_basic(setup_recording_tracer: RecordingExporter) -> None:
    exporter = setup_recording_tracer

    with ap.span("custom_operation", type="tool", model="gpt-4o") as s:
        s.set_input({"query": "hello"})
        s.set_output({"status": "ok"})
        s.set_usage(prompt_tokens=10, completion_tokens=20, cost=0.001)

    assert len(exporter.spans) == 1
    span = exporter.spans[0]
    assert span.name == "custom_operation"
    assert span.type == SpanType.TOOL
    assert span.input == {"query": "hello"}
    assert span.output == {"status": "ok"}
    assert span.duration_ms is not None and span.duration_ms >= 0
    assert span.metadata["model"] == "gpt-4o"
    assert span.metadata["usage"]["total_tokens"] == 30

    # An auto-run was created because no outer run was open
    assert len(exporter.runs) >= 1
    final_run = exporter.runs[-1]
    assert final_run.status == RunStatus.SUCCESS
    assert final_run.totals.step_count == 1
    assert final_run.totals.prompt_tokens == 10
    assert final_run.totals.completion_tokens == 20
    assert final_run.totals.total_tokens == 30
    assert final_run.totals.cost == 0.001


def test_agent_and_tool_nesting(setup_recording_tracer: RecordingExporter) -> None:
    exporter = setup_recording_tracer

    @ap.trace_tool(name="search_database")
    def search_db(q: str) -> list[str]:
        with ap.span("embedding_model", type="llm", model="text-embedding-3-small") as s:
            s.set_usage(prompt_tokens=5, completion_tokens=0, cost=0.0001)
        return [f"result for {q}"]

    @ap.trace_agent(name="customer_agent", version="2.1", tags=["prod", "v2"])
    def run_agent(question: str) -> str:
        results = search_db(question)
        with ap.span("chat_completion", type="llm", model="gpt-4o") as s:
            s.set_output("Here is your answer")
            s.set_usage(prompt_tokens=50, completion_tokens=25, cost=0.002)
        return f"Answer based on {results[0]}"

    out = run_agent("test query")
    assert "Answer based on result for test query" in out

    # We expect 4 spans:
    # 1. embedding_model (child of search_database)
    # 2. search_database (child of customer_agent)
    # 3. chat_completion (child of customer_agent)
    # 4. customer_agent (root, no parent)
    assert len(exporter.spans) == 4

    agent_span = next(s for s in exporter.spans if s.name == "customer_agent")
    tool_span = next(s for s in exporter.spans if s.name == "search_database")
    embed_span = next(s for s in exporter.spans if s.name == "embedding_model")
    llm_span = next(s for s in exporter.spans if s.name == "chat_completion")

    assert agent_span.parent_id is None
    assert tool_span.parent_id == agent_span.id
    assert embed_span.parent_id == tool_span.id
    assert llm_span.parent_id == agent_span.id

    assert all(s.run_id == agent_span.run_id for s in exporter.spans)

    final_run = exporter.runs[-1]
    assert final_run.status == RunStatus.SUCCESS
    assert final_run.agent_name == "customer_agent"
    assert final_run.agent_version == "2.1"
    assert final_run.tags == ["prod", "v2"]
    assert final_run.totals.step_count == 4
    assert final_run.totals.prompt_tokens == 55
    assert final_run.totals.completion_tokens == 25
    assert final_run.totals.total_tokens == 80


@pytest.mark.asyncio
async def test_async_agent_and_tool_nesting(setup_recording_tracer: RecordingExporter) -> None:
    exporter = setup_recording_tracer

    @ap.trace_tool(name="async_calculator")
    async def async_calc(x: int, y: int) -> int:
        await asyncio.sleep(0.01)
        return x + y

    @ap.trace_agent(name="async_support_agent")
    async def async_agent() -> int:
        async with ap.span("async_prep", type="llm"):
            await asyncio.sleep(0.01)
        res = await async_calc(10, 20)
        return res

    result = await async_agent()
    assert result == 30

    assert len(exporter.spans) == 3
    root_span = next(s for s in exporter.spans if s.name == "async_support_agent")
    prep_span = next(s for s in exporter.spans if s.name == "async_prep")
    calc_span = next(s for s in exporter.spans if s.name == "async_calculator")

    assert prep_span.parent_id == root_span.id
    assert calc_span.parent_id == root_span.id
    assert root_span.parent_id is None


def test_exception_capture_and_reraise(setup_recording_tracer: RecordingExporter) -> None:
    exporter = setup_recording_tracer

    @ap.trace_tool(name="failing_tool")
    def failing_tool() -> None:
        raise ValueError("Simulated tool crash")

    @ap.trace_agent(name="resilient_agent")
    def run_agent() -> None:
        failing_tool()

    with pytest.raises(ValueError, match="Simulated tool crash"):
        run_agent()

    # The exception must be captured on the failing span AND parent agent span
    assert len(exporter.spans) == 2
    tool_span = next(s for s in exporter.spans if s.name == "failing_tool")
    agent_span = next(s for s in exporter.spans if s.name == "resilient_agent")

    assert tool_span.error is not None
    assert tool_span.error.type == "ValueError"
    assert "Simulated tool crash" in tool_span.error.message
    assert tool_span.error.traceback is not None

    assert agent_span.error is not None
    assert agent_span.error.type == "ValueError"

    # Run status must be set to ERROR
    final_run = exporter.runs[-1]
    assert final_run.status == RunStatus.ERROR

"""Tests for context propagation across async tasks and OS threads."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import agentprobe as ap
from agentprobe.context import copy_context_to_thread, get_current_run, get_current_span
from agentprobe.models import Run, Span
from agentprobe.transport import BaseExporter


class RecordingExporter(BaseExporter):
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
def setup_tracer() -> RecordingExporter:
    exporter = RecordingExporter()
    tracer = ap.init(project="test-context", exporter="none")
    tracer.exporter = exporter
    return exporter


@pytest.mark.asyncio
async def test_asyncio_concurrent_subtasks(setup_tracer: RecordingExporter) -> None:
    exporter = setup_tracer

    @ap.trace_tool(name="fetch_item")
    async def fetch_item(item_id: int) -> int:
        async with ap.span(f"substep_{item_id}", type="tool"):
            await asyncio.sleep(0.01)
        return item_id * 2

    @ap.trace_agent(name="parallel_agent")
    async def parallel_agent() -> list[int]:
        # Run 3 concurrent tasks
        results = await asyncio.gather(
            fetch_item(1),
            fetch_item(2),
            fetch_item(3),
        )
        return list(results)

    results = await parallel_agent()
    assert results == [2, 4, 6]

    agent_span = next(s for s in exporter.spans if s.name == "parallel_agent")

    for i in (1, 2, 3):
        expected_input = {"args": (i,), "kwargs": {}}
        tool_span = next(
            s for s in exporter.spans if s.name == "fetch_item" and s.input == expected_input
        )
        substep_span = next(s for s in exporter.spans if s.name == f"substep_{i}")

        assert tool_span.parent_id == agent_span.id
        assert substep_span.parent_id == tool_span.id


def test_thread_propagation_with_helper(setup_tracer: RecordingExporter) -> None:
    exporter = setup_tracer

    thread_run_id = None
    thread_parent_id = None

    def worker_target() -> None:
        nonlocal thread_run_id, thread_parent_id
        current_span = get_current_span()
        current_run = get_current_run()
        if current_span:
            thread_parent_id = current_span.id
        if current_run:
            thread_run_id = current_run.id

        with ap.span("thread_worker_span", type="tool"):
            pass

    @ap.trace_agent(name="threaded_agent")
    def run_agent() -> None:
        # Wrap target with copy_context_to_thread
        t = threading.Thread(target=copy_context_to_thread(worker_target))
        t.start()
        t.join()

    run_agent()

    agent_span = next(s for s in exporter.spans if s.name == "threaded_agent")
    worker_span = next(s for s in exporter.spans if s.name == "thread_worker_span")

    assert thread_run_id == agent_span.run_id
    assert thread_parent_id == agent_span.id
    assert worker_span.parent_id == agent_span.id
    assert worker_span.run_id == agent_span.run_id


def test_thread_pool_executor_propagation(setup_tracer: RecordingExporter) -> None:
    exporter = setup_tracer

    def worker_fn(idx: int) -> int:
        with ap.span(f"pool_worker_{idx}", type="tool"):
            return idx * 10

    @ap.trace_agent(name="pool_agent")
    def run_agent() -> list[int]:
        with ThreadPoolExecutor(max_workers=2) as executor:
            # Wrap tasks using copy_context_to_thread
            futures = [executor.submit(copy_context_to_thread(worker_fn), i) for i in range(2)]
            return [f.result() for f in futures]

    res = run_agent()
    assert res == [0, 10]

    agent_span = next(s for s in exporter.spans if s.name == "pool_agent")
    w0 = next(s for s in exporter.spans if s.name == "pool_worker_0")
    w1 = next(s for s in exporter.spans if s.name == "pool_worker_1")

    assert w0.parent_id == agent_span.id
    assert w1.parent_id == agent_span.id

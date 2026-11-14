"""Tests for exporter reliability, queue bounds, and failure isolation."""

import json
from pathlib import Path

import httpx
import pytest

from agentprobe.config import AgentProbeConfig
from agentprobe.models import Run, Span, SpanType
from agentprobe.transport import HttpExporter, JsonlExporter


def test_queue_overflow_drop_oldest() -> None:
    # Configure tiny queue size of 3
    config = AgentProbeConfig(max_queue_size=3, batch_size=10, flush_interval_seconds=100.0)
    exporter = HttpExporter(config)

    try:
        # Enqueue 5 items (exceeding queue limit of 3)
        for i in range(5):
            run = Run(agent_name=f"agent_{i}")
            exporter.enqueue_run(run)

        # The queue should contain at most 3 items (the newest ones: agent_2, agent_3, agent_4)
        assert exporter._queue.qsize() <= 3
        items = []
        while not exporter._queue.empty():
            item_type, item = exporter._queue.get_nowait()
            items.append(item.agent_name)

        assert "agent_0" not in items
        assert "agent_1" not in items
        assert "agent_4" in items
    finally:
        exporter.close()


def test_http_exporter_failure_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    # Verify that HTTP transport crashes or network errors are completely swallowed
    config = AgentProbeConfig(
        endpoint="http://non-existent-probe-host:9999",
        flush_interval_seconds=0.1,
    )
    exporter = HttpExporter(config)

    # Mock client.post to raise an httpx network error
    def mock_post(*args: object, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("Failed to reach server")

    monkeypatch.setattr(exporter._client, "post", mock_post)

    try:
        run = Run(agent_name="isolated_agent")
        span = Span(run_id=run.id, type=SpanType.AGENT, name="root_span")

        # Must not raise
        exporter.enqueue_run(run)
        exporter.enqueue_span(span)
        exporter.flush(timeout=1.0)
    finally:
        exporter.close()


def test_jsonl_exporter_writes_valid_json(tmp_path: Path) -> None:
    file_path = tmp_path / "traces.jsonl"
    config = AgentProbeConfig(exporter="jsonl", jsonl_path=str(file_path))
    exporter = JsonlExporter(config)

    run = Run(agent_name="offline_agent")
    span = Span(run_id=run.id, type=SpanType.TOOL, name="local_tool")

    exporter.enqueue_run(run)
    exporter.enqueue_span(span)
    exporter.flush()
    exporter.close()

    assert file_path.exists()
    lines = file_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    run_entry = json.loads(lines[0])
    span_entry = json.loads(lines[1])

    assert run_entry["type"] == "run"
    assert run_entry["data"]["agent_name"] == "offline_agent"

    assert span_entry["type"] == "span"
    assert span_entry["data"]["name"] == "local_tool"

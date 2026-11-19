"""Tests for idempotent trace ingestion."""

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_idempotent_ingest(client: AsyncClient) -> None:
    run_id = str(uuid.uuid4())
    span_1_id = str(uuid.uuid4())
    span_2_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    batch_payload = {
        "runs": [
            {
                "id": run_id,
                "project": "customer_service",
                "agent_name": "support_bot",
                "agent_version": "1.0",
                "status": "running",
                "tags": ["prod", "tier1"],
                "started_at": now_iso,
                "totals": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                    "cost": 0.001,
                    "latency_ms": 150.0,
                    "step_count": 1,
                },
                "metadata": {"env": "production"},
            }
        ],
        "spans": [
            {
                "id": span_1_id,
                "run_id": run_id,
                "parent_id": None,
                "type": "agent",
                "name": "support_bot",
                "input": {"question": "order status"},
                "output": None,
                "started_at": now_iso,
                "ended_at": None,
                "metadata": {},
            },
            {
                "id": span_2_id,
                "run_id": run_id,
                "parent_id": span_1_id,
                "type": "tool",
                "name": "lookup_order",
                "input": {"order_id": "ORD-123"},
                "output": {"status": "shipped"},
                "started_at": now_iso,
                "ended_at": now_iso,
                "duration_ms": 45.0,
                "metadata": {
                    "usage": {
                        "prompt_tokens": 15,
                        "completion_tokens": 25,
                        "total_tokens": 40,
                        "cost": 0.002,
                    }
                },
            },
        ],
    }

    # 1. Initial ingestion
    resp1 = await client.post("/v1/ingest", json=batch_payload)
    assert resp1.status_code == 200
    res1_data = resp1.json()
    assert res1_data["accepted_runs"] == 1
    assert res1_data["accepted_spans"] == 2
    assert res1_data["rejected"] == 0

    # Verify project auto-creation
    projects_resp = await client.get("/v1/projects")
    assert projects_resp.status_code == 200
    projects = projects_resp.json()["projects"]
    assert any(p["name"] == "customer_service" for p in projects)

    # 2. Resubmitting same batch should be idempotent (no duplicates)
    resp2 = await client.post("/v1/ingest", json=batch_payload)
    assert resp2.status_code == 200

    # Check spans count in database
    spans_resp = await client.get(f"/v1/runs/{run_id}/spans")
    assert spans_resp.status_code == 200
    spans = spans_resp.json()
    assert len(spans) == 2  # Not 4! Idempotency verified.

    # Verify run aggregates
    run_resp = await client.get(f"/v1/runs/{run_id}")
    assert run_resp.status_code == 200
    run_data = run_resp.json()
    assert run_data["agent_name"] == "support_bot"
    assert run_data["project_name"] == "customer_service"
    # Spans computed usage totals merged
    assert run_data["totals"]["step_count"] == 2
    assert run_data["totals"]["prompt_tokens"] >= 15
    assert run_data["totals"]["completion_tokens"] >= 25


@pytest.mark.asyncio
async def test_ingest_validation_failure(client: AsyncClient) -> None:
    bad_payload = {"runs": [{"id": "not-a-uuid", "agent_name": "bot"}]}
    resp = await client.post("/v1/ingest", json=bad_payload)
    assert resp.status_code == 422
    err_body = resp.json()
    assert err_body["error"]["code"] == "VALIDATION_ERROR"
    assert err_body["error"]["details"] is not None


@pytest.mark.asyncio
async def test_cross_batch_idempotency_terminal_states_and_out_of_order(
    client: AsyncClient,
) -> None:
    run_id = str(uuid.uuid4())
    t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc).isoformat()
    t1 = datetime(2026, 1, 1, 10, 0, 5, tzinfo=timezone.utc).isoformat()
    t2 = datetime(2026, 1, 1, 10, 0, 10, tzinfo=timezone.utc).isoformat()

    # Request 1: Run started, status="running"
    req1 = {
        "runs": [
            {
                "id": run_id,
                "project": "idempotency_test",
                "agent_name": "idemp_bot",
                "status": "running",
                "started_at": t0,
                "tags": ["v1"],
                "totals": {"prompt_tokens": 50, "latency_ms": 100.0, "step_count": 0},
                "metadata": {"attempt": 1},
            }
        ],
        "spans": [],
    }
    r1 = await client.post("/v1/ingest", json=req1)
    assert r1.status_code == 200

    # Verify run is running
    run_check1 = await client.get(f"/v1/runs/{run_id}")
    assert run_check1.json()["status"] == "running"
    assert run_check1.json()["tags"] == ["v1"]
    assert run_check1.json()["totals"]["prompt_tokens"] == 50

    # Request 2: Run completes with status="success", ended_at=t2, new tag "v2", higher tokens
    req2 = {
        "runs": [
            {
                "id": run_id,
                "project": "idempotency_test",
                "agent_name": "idemp_bot",
                "status": "success",
                "started_at": t0,
                "ended_at": t2,
                "tags": ["v2"],
                "totals": {
                    "prompt_tokens": 120,
                    "cost": 0.005,
                    "latency_ms": 500.0,
                    "step_count": 1,
                },
                "metadata": {"completed": True},
            }
        ],
        "spans": [],
    }
    r2 = await client.post("/v1/ingest", json=req2)
    assert r2.status_code == 200

    run_check2 = await client.get(f"/v1/runs/{run_id}")
    d2 = run_check2.json()
    assert d2["status"] == "success"
    assert "v1" in d2["tags"] and "v2" in d2["tags"]
    assert d2["totals"]["prompt_tokens"] == 120
    assert d2["totals"]["cost"] == 0.005
    assert d2["ended_at"] is not None

    # Request 3: Delayed/out-of-order partial packet sending status="running", earlier ended_at t1, lower tokens 20
    req3 = {
        "runs": [
            {
                "id": run_id,
                "project": "idempotency_test",
                "agent_name": "idemp_bot",
                "status": "running",
                "started_at": t0,
                "ended_at": t1,
                "tags": ["delayed"],
                "totals": {"prompt_tokens": 20, "cost": 0.001, "latency_ms": 80.0, "step_count": 0},
                "metadata": {"out_of_order": True},
            }
        ],
        "spans": [],
    }
    r3 = await client.post("/v1/ingest", json=req3)
    assert r3.status_code == 200

    # Non-regression check: terminal status "success" wins, later ended_at t2 wins, totals do not decrease!
    run_check3 = await client.get(f"/v1/runs/{run_id}")
    d3 = run_check3.json()
    assert d3["status"] == "success"
    assert d3["totals"]["prompt_tokens"] == 120
    assert d3["totals"]["cost"] == 0.005
    assert "delayed" in d3["tags"]


@pytest.mark.asyncio
async def test_cross_batch_span_idempotency_and_error_propagation(client: AsyncClient) -> None:
    run_id = str(uuid.uuid4())
    span_id = str(uuid.uuid4())
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    t1 = datetime(2026, 1, 1, 12, 0, 2, tzinfo=timezone.utc).isoformat()

    # Step 1: Ingest span with input, but output is None
    req1 = {
        "runs": [{"id": run_id, "agent_name": "err_bot", "started_at": t0, "status": "running"}],
        "spans": [
            {
                "id": span_id,
                "run_id": run_id,
                "type": "tool",
                "name": "critical_tool",
                "input": {"action": "execute"},
                "output": None,
                "started_at": t0,
                "ended_at": None,
            }
        ],
    }
    r1 = await client.post("/v1/ingest", json=req1)
    assert r1.status_code == 200

    # Step 2: Later request updates span with error
    req2 = {
        "runs": [{"id": run_id, "agent_name": "err_bot", "started_at": t0, "status": "running"}],
        "spans": [
            {
                "id": span_id,
                "run_id": run_id,
                "type": "tool",
                "name": "critical_tool",
                "input": {"action": "execute"},
                "error": {"type": "RuntimeError", "message": "Disk full"},
                "started_at": t0,
                "ended_at": t1,
                "duration_ms": 2000.0,
            }
        ],
    }
    r2 = await client.post("/v1/ingest", json=req2)
    assert r2.status_code == 200

    # Run aggregates should automatically propagate error
    run_res = await client.get(f"/v1/runs/{run_id}")
    assert run_res.json()["status"] == "error"

    # Step 3: Resend span with None error; existing error must NOT be wiped out
    req3 = {
        "runs": [{"id": run_id, "agent_name": "err_bot", "started_at": t0, "status": "running"}],
        "spans": [
            {
                "id": span_id,
                "run_id": run_id,
                "type": "tool",
                "name": "critical_tool",
                "error": None,
                "started_at": t0,
            }
        ],
    }
    r3 = await client.post("/v1/ingest", json=req3)
    assert r3.status_code == 200

    spans_res = await client.get(f"/v1/runs/{run_id}/spans")
    spans_data = spans_res.json()
    assert len(spans_data) == 1
    assert spans_data[0]["error"]["message"] == "Disk full"
    assert spans_data[0]["duration_ms"] == 2000.0


@pytest.mark.asyncio
async def test_in_batch_deduplication_and_orphan_spans(client: AsyncClient) -> None:
    run_id = str(uuid.uuid4())
    span_1_id = str(uuid.uuid4())
    span_orphan_id = str(uuid.uuid4())
    t0 = datetime(2026, 1, 1, 14, 0, 0, tzinfo=timezone.utc).isoformat()
    t1 = datetime(2026, 1, 1, 14, 0, 5, tzinfo=timezone.utc).isoformat()

    # Batch with intra-batch duplicate runs and spans + orphan span with uncreated run
    payload = {
        "runs": [
            {
                "id": run_id,
                "project": "batch_dedup_test",
                "agent_name": "agent_initial",
                "agent_version": "1.0",
                "status": "running",
                "started_at": t0,
                "tags": ["tag_a"],
                "totals": {
                    "prompt_tokens": 10,
                    "completion_tokens": 10,
                    "cost": 0.001,
                    "step_count": 0,
                    "latency_ms": 50.0,
                },
                "metadata": {"key1": "val1"},
            },
            {
                "id": run_id,
                "project": "batch_dedup_test",
                "agent_name": "agent_updated",
                "agent_version": "1.1",
                "status": "success",
                "started_at": t0,
                "ended_at": t1,
                "tags": ["tag_b"],
                "totals": {
                    "prompt_tokens": 50,
                    "completion_tokens": 60,
                    "total_tokens": 110,
                    "cost": 0.005,
                    "step_count": 2,
                    "latency_ms": 250.0,
                },
                "metadata": {"key2": "val2"},
            },
        ],
        "spans": [
            {
                "id": span_1_id,
                "run_id": run_id,
                "type": "llm",
                "name": "generate_answer",
                "input": {"prompt": "test"},
                "started_at": t0,
                "metadata": {"model": "mock-v1"},
            },
            {
                "id": span_1_id,
                "run_id": run_id,
                "parent_id": None,
                "type": "llm",
                "name": "generate_answer",
                "output": {"text": "hello"},
                "started_at": t0,
                "ended_at": t1,
                "duration_ms": 120.0,
                "metadata": {
                    "model": "mock-v1",
                    "usage": {"prompt_tokens": 20, "completion_tokens": 30, "cost": 0.002},
                },
            },
            # Orphan span for a run that wasn't included in the runs array
            {
                "id": span_orphan_id,
                "run_id": str(uuid.uuid4()),
                "type": "tool",
                "name": "auto_created_agent_tool",
                "input": {"data": 123},
                "started_at": t0,
                "ended_at": t1,
                "duration_ms": 40.0,
            },
        ],
    }

    resp = await client.post("/v1/ingest", json=payload)
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["accepted_runs"] == 2
    assert res_data["accepted_spans"] == 3

    # Check deduplicated run
    run_resp = await client.get(f"/v1/runs/{run_id}")
    assert run_resp.status_code == 200
    r_data = run_resp.json()
    assert r_data["agent_name"] == "agent_updated"
    assert r_data["agent_version"] == "1.1"
    assert r_data["status"] == "success"
    assert "tag_a" in r_data["tags"] and "tag_b" in r_data["tags"]
    assert r_data["totals"]["prompt_tokens"] >= 50
    assert r_data["totals"]["cost"] >= 0.005

    # Check deduplicated span
    spans_resp = await client.get(f"/v1/runs/{run_id}/spans")
    assert spans_resp.status_code == 200
    s_list = spans_resp.json()
    assert len(s_list) == 1
    assert s_list[0]["input"] == {"prompt": "test"}
    assert s_list[0]["output"] == {"text": "hello"}
    assert s_list[0]["duration_ms"] == 120.0

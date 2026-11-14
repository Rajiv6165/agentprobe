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

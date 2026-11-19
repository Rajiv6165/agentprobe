"""Integration tests for cassette endpoints, storage, size limits, and replay linkage."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from app.config import settings


@pytest.mark.asyncio
async def test_create_cassette_not_found(client: AsyncClient) -> None:
    non_existent = uuid.uuid4()
    resp = await client.post(f"/v1/runs/{non_existent}/cassette")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_create_cassette_no_interactions(client: AsyncClient) -> None:
    run_id = uuid.uuid4()
    now = datetime.now(timezone.utc).isoformat()
    # Ingest a run with only an agent root span (no tool or llm interactive spans)
    ingest_payload = {
        "runs": [
            {
                "id": str(run_id),
                "project": "test_proj",
                "agent_name": "solo_agent",
                "status": "success",
                "started_at": now,
                "ended_at": now,
            }
        ],
        "spans": [
            {
                "id": str(uuid.uuid4()),
                "run_id": str(run_id),
                "type": "agent",
                "name": "solo_agent",
                "started_at": now,
                "ended_at": now,
            }
        ],
    }
    ingest_resp = await client.post("/v1/ingest", json=ingest_payload)
    assert ingest_resp.status_code == 200

    resp = await client.post(f"/v1/runs/{run_id}/cassette")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "NO_INTERACTIONS"


@pytest.mark.asyncio
async def test_cassette_crud_and_download(client: AsyncClient) -> None:
    run_id = uuid.uuid4()
    agent_span_id = uuid.uuid4()
    tool_span_id = uuid.uuid4()
    llm_span_id = uuid.uuid4()
    now = datetime.now(timezone.utc).isoformat()

    # Ingest full trace with agent, tool, and llm spans
    ingest_payload = {
        "runs": [
            {
                "id": str(run_id),
                "project": "demo_project",
                "agent_name": "support_agent",
                "agent_version": "1.0",
                "status": "success",
                "started_at": now,
                "ended_at": now,
                "metadata": {"env": "test"},
            }
        ],
        "spans": [
            {
                "id": str(agent_span_id),
                "run_id": str(run_id),
                "parent_id": None,
                "type": "agent",
                "name": "support_agent",
                "input": {"args": ["How to return item?"], "kwargs": {}},
                "output": "Return policy is 30 days.",
                "started_at": now,
                "ended_at": now,
                "duration_ms": 150.0,
            },
            {
                "id": str(tool_span_id),
                "run_id": str(run_id),
                "parent_id": str(agent_span_id),
                "type": "tool",
                "name": "search_kb",
                "input": {"query": "return policy"},
                "output": ["Policy: 30 days"],
                "started_at": now,
                "ended_at": now,
                "duration_ms": 40.0,
            },
            {
                "id": str(llm_span_id),
                "run_id": str(run_id),
                "parent_id": str(agent_span_id),
                "type": "llm",
                "name": "generate_answer",
                "input": {"prompt": "Summarize policy"},
                "output": "Return policy is 30 days.",
                "started_at": now,
                "ended_at": now,
                "duration_ms": 80.0,
                "metadata": {"model": "gpt-4o"},
            },
        ],
    }
    ingest_resp = await client.post("/v1/ingest", json=ingest_payload)
    assert ingest_resp.status_code == 200

    # 1. POST /v1/runs/{id}/cassette
    cas_create_resp = await client.post(
        f"/v1/runs/{run_id}/cassette",
        json={"name": "custom_support_cassette"},
    )
    assert cas_create_resp.status_code == 201
    cas_data = cas_create_resp.json()
    cas_id = cas_data["id"]

    assert cas_data["name"] == "custom_support_cassette"
    assert cas_data["schema_version"] == "1.0"
    assert cas_data["source_run_id"] == str(run_id)
    assert cas_data["size_bytes"] > 0
    assert len(cas_data["fingerprint"]) == 64
    assert len(cas_data["content"]["interactions"]) == 2
    assert cas_data["content"]["interactions"][0]["name"] in ("search_kb", "generate_answer")

    # Verify run now references the cassette
    run_resp = await client.get(f"/v1/runs/{run_id}")
    assert run_resp.status_code == 200
    assert run_resp.json()["cassette_id"] == cas_id

    # 2. Idempotency without force returns existing cassette
    repeat_resp = await client.post(
        f"/v1/runs/{run_id}/cassette",
        json={"name": "ignored_new_name"},
    )
    assert repeat_resp.status_code == 201
    assert repeat_resp.json()["id"] == cas_id
    assert repeat_resp.json()["name"] == "custom_support_cassette"

    # 3. GET /v1/cassettes (list)
    list_resp = await client.get("/v1/cassettes?project=demo_project")
    assert list_resp.status_code == 200
    cassettes_list = list_resp.json()["cassettes"]
    assert len(cassettes_list) >= 1
    assert any(c["id"] == cas_id for c in cassettes_list)

    # Filter by agent_name
    agent_filter_resp = await client.get("/v1/cassettes?agent_name=support_agent")
    assert agent_filter_resp.status_code == 200
    assert len(agent_filter_resp.json()["cassettes"]) >= 1

    # 4. GET /v1/cassettes/{id}
    get_resp = await client.get(f"/v1/cassettes/{cas_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == cas_id
    assert get_resp.json()["content"]["agent_name"] == "support_agent"

    # 5. GET /v1/cassettes/{id}/download
    dl_resp = await client.get(f"/v1/cassettes/{cas_id}/download")
    assert dl_resp.status_code == 200
    assert dl_resp.headers["content-type"].startswith("application/json")
    assert (
        dl_resp.headers["content-disposition"]
        == 'attachment; filename="custom_support_cassette.cassette.json"'
    )
    dl_json = dl_resp.json()
    assert dl_json["schema_version"] == "1.0"
    assert dl_json["id"] == cas_id

    # 6. DELETE /v1/cassettes/{id}
    del_resp = await client.delete(f"/v1/cassettes/{cas_id}")
    assert del_resp.status_code == 204

    # Verify cassette is deleted
    assert (await client.get(f"/v1/cassettes/{cas_id}")).status_code == 404
    assert (await client.delete(f"/v1/cassettes/{cas_id}")).status_code == 404

    # Verify run.cassette_id was set to None
    run_resp_after = await client.get(f"/v1/runs/{run_id}")
    assert run_resp_after.status_code == 200
    assert run_resp_after.json()["cassette_id"] is None


@pytest.mark.asyncio
async def test_replay_of_run_linkage_and_filter(client: AsyncClient) -> None:
    source_run_id = uuid.uuid4()
    replay_run_id = uuid.uuid4()
    other_run_id = uuid.uuid4()
    now = datetime.now(timezone.utc).isoformat()

    # Ingest original run, replay run, and an unrelated run
    ingest_payload = {
        "runs": [
            {
                "id": str(source_run_id),
                "project": "replay_test",
                "agent_name": "order_agent",
                "status": "success",
                "started_at": now,
                "ended_at": now,
            },
            {
                "id": str(replay_run_id),
                "project": "replay_test",
                "agent_name": "order_agent",
                "status": "success",
                "tags": ["replay"],
                "started_at": now,
                "ended_at": now,
                "replay_of_run_id": str(source_run_id),
                "replay_policy": {"mode": "all_recorded"},
            },
            {
                "id": str(other_run_id),
                "project": "replay_test",
                "agent_name": "order_agent",
                "status": "success",
                "started_at": now,
                "ended_at": now,
            },
        ],
        "spans": [],
    }
    ingest_resp = await client.post("/v1/ingest", json=ingest_payload)
    assert ingest_resp.status_code == 200

    # Verify replay run details
    r_resp = await client.get(f"/v1/runs/{replay_run_id}")
    assert r_resp.status_code == 200
    data = r_resp.json()
    assert data["replay_of_run_id"] == str(source_run_id)
    assert data["replay_policy"] == {"mode": "all_recorded"}

    # Query with ?replay_of=<source_run_id>
    filter_resp = await client.get(f"/v1/runs?replay_of={source_run_id}")
    assert filter_resp.status_code == 200
    runs = filter_resp.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["id"] == str(replay_run_id)
    assert runs[0]["replay_of_run_id"] == str(source_run_id)


@pytest.mark.asyncio
async def test_max_cassette_size_limit(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Set MAX_CASSETTE_SIZE_BYTES to a tiny value to trigger 413
    monkeypatch.setattr(settings, "MAX_CASSETTE_SIZE_BYTES", 50)

    run_id = uuid.uuid4()
    span_id = uuid.uuid4()
    now = datetime.now(timezone.utc).isoformat()

    # Ingest a normal run and span
    ingest_payload = {
        "runs": [
            {
                "id": str(run_id),
                "project": "size_test",
                "agent_name": "test_agent",
                "status": "success",
                "started_at": now,
                "ended_at": now,
            }
        ],
        "spans": [
            {
                "id": str(span_id),
                "run_id": str(run_id),
                "type": "tool",
                "name": "large_tool",
                "input": {"data": "x" * 200},
                "output": "ok",
                "started_at": now,
                "ended_at": now,
            }
        ],
    }
    # Reset limit temporarily for ingest
    monkeypatch.setattr(settings, "MAX_CASSETTE_SIZE_BYTES", 10_000_000)
    await client.post("/v1/ingest", json=ingest_payload)

    # Re-enable tiny limit
    monkeypatch.setattr(settings, "MAX_CASSETTE_SIZE_BYTES", 50)
    resp = await client.post(f"/v1/runs/{run_id}/cassette")
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "CASSETTE_TOO_LARGE"


@pytest.mark.asyncio
async def test_cassette_force_overwrite_and_capture(client: AsyncClient) -> None:
    run_id = uuid.uuid4()
    agent_span_id = uuid.uuid4()
    cap_span_id = uuid.uuid4()
    now = datetime.now(timezone.utc).isoformat()

    # Ingest a run with capture span
    ingest_payload = {
        "runs": [
            {
                "id": str(run_id),
                "project": "overwrite_test",
                "agent_name": "capture_agent",
                "status": "success",
                "started_at": now,
                "ended_at": now,
            }
        ],
        "spans": [
            {
                "id": str(agent_span_id),
                "run_id": str(run_id),
                "type": "agent",
                "name": "capture_agent",
                "input": "test input string",
                "output": "done",
                "started_at": now,
                "ended_at": now,
            },
            {
                "id": str(cap_span_id),
                "run_id": str(run_id),
                "parent_id": str(agent_span_id),
                "type": "tool",
                "name": "uuid_gen",
                "input": None,
                "output": "123e4567-e89b-12d3-a456-426614174000",
                "metadata": {"kind": "capture"},
                "started_at": now,
                "ended_at": now,
            },
        ],
    }
    await client.post("/v1/ingest", json=ingest_payload)

    # 1. Create first version of cassette
    r1 = await client.post(f"/v1/runs/{run_id}/cassette", json={"name": "v1_name"})
    assert r1.status_code == 201
    assert r1.json()["name"] == "v1_name"
    assert r1.json()["content"]["interactions"][0]["kind"] == "capture"
    cas1_id = r1.json()["id"]

    # 2. Force overwrite
    r2 = await client.post(f"/v1/runs/{run_id}/cassette?force=true", json={"name": "v2_name"})
    assert r2.status_code == 201
    assert r2.json()["name"] == "v2_name"
    cas2_id = r2.json()["id"]
    assert cas1_id != cas2_id


@pytest.mark.asyncio
async def test_cassette_cursor_pagination(client: AsyncClient) -> None:
    run_id1 = uuid.uuid4()
    run_id2 = uuid.uuid4()
    now = datetime.now(timezone.utc).isoformat()

    # Ingest 2 runs with tool spans
    for r_id in (run_id1, run_id2):
        await client.post(
            "/v1/ingest",
            json={
                "runs": [
                    {
                        "id": str(r_id),
                        "project": "page_test",
                        "agent_name": "bot",
                        "status": "success",
                        "started_at": now,
                        "ended_at": now,
                    }
                ],
                "spans": [
                    {
                        "id": str(uuid.uuid4()),
                        "run_id": str(r_id),
                        "type": "tool",
                        "name": "t1",
                        "input": {},
                        "output": {},
                        "started_at": now,
                        "ended_at": now,
                    }
                ],
            },
        )
        await client.post(f"/v1/runs/{r_id}/cassette")

    # Query with limit 1 to test pagination cursor
    page1 = await client.get("/v1/cassettes?project=page_test&limit=1")
    assert page1.status_code == 200
    p1_data = page1.json()
    assert len(p1_data["cassettes"]) == 1
    cursor = p1_data["next_cursor"]
    assert cursor is not None

    page2 = await client.get(f"/v1/cassettes?project=page_test&limit=1&cursor={cursor}")
    assert page2.status_code == 200
    p2_data = page2.json()
    assert len(p2_data["cassettes"]) == 1
    assert p1_data["cassettes"][0]["id"] != p2_data["cassettes"][0]["id"]

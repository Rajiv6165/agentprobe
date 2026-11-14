"""Tests for run query, cursor pagination, and tree assembly."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_run_pagination_and_filtering(client: AsyncClient) -> None:
    now = datetime.now(timezone.utc)
    runs_to_create = []

    for i in range(5):
        run_id = str(uuid.uuid4())
        runs_to_create.append(
            {
                "id": run_id,
                "project": "benchmarks" if i < 3 else "production",
                "agent_name": f"agent_{i % 2}",
                "status": "success" if i % 2 == 0 else "error",
                "started_at": (now - timedelta(minutes=10 - i)).isoformat(),
                "totals": {"step_count": i + 1, "total_tokens": 100 * (i + 1)},
            }
        )

    ingest_resp = await client.post("/v1/ingest", json={"runs": runs_to_create, "spans": []})
    assert ingest_resp.status_code == 200

    # 1. Test pagination: Page 1 with limit=2
    p1 = await client.get("/v1/runs?limit=2")
    assert p1.status_code == 200
    p1_data = p1.json()
    assert len(p1_data["runs"]) == 2
    assert p1_data["has_more"] is True
    assert p1_data["next_cursor"] is not None

    # Page 2 using cursor
    cursor = p1_data["next_cursor"]
    p2 = await client.get(f"/v1/runs?limit=2&cursor={cursor}")
    assert p2.status_code == 200
    p2_data = p2.json()
    assert len(p2_data["runs"]) == 2
    # Ensure disjoint runs
    p1_ids = {r["id"] for r in p1_data["runs"]}
    p2_ids = {r["id"] for r in p2_data["runs"]}
    assert p1_ids.isdisjoint(p2_ids)

    # 2. Test filtering by project
    benchmarks_resp = await client.get("/v1/runs?project=benchmarks")
    assert benchmarks_resp.status_code == 200
    b_runs = benchmarks_resp.json()["runs"]
    assert len(b_runs) == 3
    assert all(r["project_name"] == "benchmarks" for r in b_runs)

    # 3. Test filtering by status
    error_resp = await client.get("/v1/runs?status=error")
    assert error_resp.status_code == 200
    e_runs = error_resp.json()["runs"]
    assert len(e_runs) == 2
    assert all(r["status"] == "error" for r in e_runs)


@pytest.mark.asyncio
async def test_run_tree_assembly(client: AsyncClient) -> None:
    run_id = str(uuid.uuid4())
    root_id = str(uuid.uuid4())
    child_tool_id = str(uuid.uuid4())
    grandchild_llm_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    batch = {
        "runs": [
            {
                "id": run_id,
                "project": "tree_test",
                "agent_name": "hierarchical_agent",
                "status": "success",
                "started_at": now_iso,
            }
        ],
        "spans": [
            {
                "id": root_id,
                "run_id": run_id,
                "parent_id": None,
                "type": "agent",
                "name": "root_agent_span",
                "started_at": now_iso,
            },
            {
                "id": child_tool_id,
                "run_id": run_id,
                "parent_id": root_id,
                "type": "tool",
                "name": "child_search_tool",
                "started_at": now_iso,
            },
            {
                "id": grandchild_llm_id,
                "run_id": run_id,
                "parent_id": child_tool_id,
                "type": "llm",
                "name": "grandchild_embedding",
                "started_at": now_iso,
            },
        ],
    }

    ingest_resp = await client.post("/v1/ingest", json=batch)
    assert ingest_resp.status_code == 200

    # Test GET /v1/runs/{id}/tree
    tree_resp = await client.get(f"/v1/runs/{run_id}/tree")
    assert tree_resp.status_code == 200
    roots = tree_resp.json()
    assert len(roots) == 1

    root_node = roots[0]
    assert root_node["id"] == root_id
    assert root_node["name"] == "root_agent_span"
    assert len(root_node["children"]) == 1

    child_node = root_node["children"][0]
    assert child_node["id"] == child_tool_id
    assert child_node["name"] == "child_search_tool"
    assert len(child_node["children"]) == 1

    grandchild_node = child_node["children"][0]
    assert grandchild_node["id"] == grandchild_llm_id
    assert grandchild_node["name"] == "grandchild_embedding"
    assert grandchild_node["children"] == []


@pytest.mark.asyncio
async def test_run_not_found(client: AsyncClient) -> None:
    random_id = str(uuid.uuid4())
    resp = await client.get(f"/v1/runs/{random_id}")
    assert resp.status_code == 404
    err_body = resp.json()
    assert err_body["error"]["code"] == "RUN_NOT_FOUND"

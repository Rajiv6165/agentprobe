"""Tests for Cassette models, fingerprinting, normalizers, schema validation, and builders."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

import agentprobe as ap
from agentprobe.cassette import (
    Cassette,
    FingerprintNormalizer,
    Interaction,
    UnsupportedSchemaVersionError,
    compute_fingerprint,
)
from agentprobe.models import Run, Span, SpanType


def test_canonical_json_and_fingerprint_stability() -> None:
    norm = FingerprintNormalizer.default()

    # Two requests with different key ordering, different timestamps, and different UUIDs
    req_a = {
        "user_id": "usr_12345",
        "session_uuid": "12345678-1234-5678-1234-567812345678",
        "created_at": "2026-11-19T10:00:00.000Z",
        "temperature": 0.7,
        "query": "How do I reset my password?",
    }
    req_b = {
        "query": "How do I reset my password?",
        "temperature": 0.700000001,  # minor float jitter
        "session_uuid": "87654321-4321-8765-4321-876543218765",
        "created_at": "2026-12-01T15:30:22+00:00",
        "user_id": "usr_12345",
    }

    fp_a = compute_fingerprint(req_a, normalizer=norm)
    fp_b = compute_fingerprint(req_b, normalizer=norm)

    # Must produce the identical SHA-256 fingerprint
    assert fp_a == fp_b
    assert len(fp_a) == 64


def test_custom_normalizers() -> None:
    custom_norm = FingerprintNormalizer(
        ignore_keys={"custom_token"},
        custom_normalizers=[lambda s: s.replace("sandbox_", "") if isinstance(s, str) else s],
    )

    req1 = {"custom_token": "abc", "env": "sandbox_prod"}
    req2 = {"custom_token": "xyz", "env": "prod"}

    assert compute_fingerprint(req1, normalizer=custom_norm) == compute_fingerprint(
        req2, normalizer=custom_norm
    )


def test_cassette_save_load_round_trip(tmp_path: Path) -> None:
    cassette_file = tmp_path / "test_run.cassette.json"

    interaction = Interaction(
        index=0,
        kind="tool",
        name="lookup_order",
        request={"order_id": "ORD-123"},
        response={"status": "shipped"},
        request_fingerprint="abc1234567890",
        recorded_duration_ms=45.2,
        parent_index=None,
        metadata={"carrier": "FedEx"},
    )

    cassette = Cassette(
        name="order_lookup_scenario",
        agent_name="support_agent",
        agent_version="1.0",
        agent_input={"args": ["Check ORD-123"], "kwargs": {}},
        agent_output="Your order is shipped.",
        seed=42,
        interactions=[interaction],
    )

    cassette.save(cassette_file)
    assert cassette_file.exists()

    loaded = Cassette.load(cassette_file)
    assert loaded.id == cassette.id
    assert loaded.name == "order_lookup_scenario"
    assert loaded.agent_name == "support_agent"
    assert loaded.seed == 42
    assert len(loaded.interactions) == 1
    assert loaded.interactions[0].name == "lookup_order"
    assert loaded.interactions[0].response == {"status": "shipped"}


def test_unsupported_schema_version_rejection(tmp_path: Path) -> None:
    invalid_file = tmp_path / "invalid.cassette.json"
    data = {
        "schema_version": "99.0",
        "id": str(uuid.uuid4()),
        "name": "invalid",
        "agent_name": "bot",
        "interactions": [],
    }
    with open(invalid_file, "w") as f:
        json.dump(data, f)

    with pytest.raises(
        UnsupportedSchemaVersionError, match="Unsupported cassette schema version '99.0'"
    ):
        Cassette.load(invalid_file)


def test_cassette_from_spans_hierarchy_and_redaction() -> None:
    run_id = str(uuid.uuid4())
    root_id = str(uuid.uuid4())
    tool1_id = str(uuid.uuid4())
    tool2_id = str(uuid.uuid4())
    t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 1, 10, 0, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 1, 10, 0, 2, tzinfo=timezone.utc)

    spans = [
        Span(
            id=root_id,
            run_id=run_id,
            type=SpanType.AGENT,
            name="customer_agent",
            input={"args": ["Hello"], "kwargs": {}},
            output="Done",
            started_at=t0,
        ),
        Span(
            id=tool1_id,
            run_id=run_id,
            parent_id=root_id,
            type=SpanType.TOOL,
            name="auth_check",
            input={"token": "[REDACTED]"},
            output={"authenticated": True},
            started_at=t1,
            duration_ms=10.0,
        ),
        Span(
            id=tool2_id,
            run_id=run_id,
            parent_id=tool1_id,
            type=SpanType.TOOL,
            name="fetch_profile",
            input={"user_id": 42},
            output={"name": "Alice"},
            started_at=t2,
            duration_ms=25.0,
        ),
    ]

    cassette = Cassette.from_spans(spans=spans, run=Run(id=run_id, agent_name="customer_agent"))
    assert cassette.agent_name == "customer_agent"
    assert cassette.agent_input == {"args": ["Hello"], "kwargs": {}}
    assert cassette.agent_output == "Done"
    assert len(cassette.interactions) == 2

    int0 = cassette.interactions[0]
    int1 = cassette.interactions[1]

    assert int0.name == "auth_check"
    assert int0.redacted is True  # Redaction detected and preserved!
    assert int0.parent_index is None

    assert int1.name == "fetch_profile"
    assert int1.parent_index == 0  # Hierarchical parent resolved correctly!
    assert int1.redacted is False


def test_cassette_record_sync_and_async() -> None:
    @ap.trace_tool(name="calc_add")
    def calc_add(a: int, b: int) -> int:
        return a + b

    @ap.trace_agent(name="calc_agent")
    def run_calc(x: int, y: int) -> int:
        return calc_add(x, y)

    res, cassette = Cassette.record(run_calc, 10, 20, seed=123)
    assert res == 30
    assert cassette.agent_name == "calc_agent"
    assert cassette.seed == 123
    assert len(cassette.interactions) == 1
    assert cassette.interactions[0].name == "calc_add"
    assert cassette.interactions[0].response == 30


@pytest.mark.asyncio
async def test_cassette_arecord() -> None:
    @ap.trace_tool(name="async_fetch")
    async def async_fetch(val: str) -> str:
        return f"result_{val}"

    @ap.trace_agent(name="async_agent")
    async def run_async(param: str) -> str:
        return await async_fetch(param)

    res, cassette = await Cassette.arecord(run_async, "test_input")
    assert res == "result_test_input"
    assert len(cassette.interactions) == 1
    assert cassette.interactions[0].name == "async_fetch"
    assert cassette.interactions[0].response == "result_test_input"


def test_cassette_from_jsonl(tmp_path: Path) -> None:
    jsonl_file = tmp_path / "traces.jsonl"
    cfg = ap.config.AgentProbeConfig(exporter="jsonl", jsonl_path=str(jsonl_file))
    tracer = ap.Tracer(config=cfg)

    @tracer.trace_tool(name="jsonl_tool")
    def jsonl_tool(x: int) -> int:
        return x + 1

    @tracer.trace_agent(name="jsonl_agent")
    def run_agent(x: int) -> int:
        return jsonl_tool(x)

    run_agent(5)
    tracer.shutdown()

    assert jsonl_file.exists()
    cassette = Cassette.from_jsonl(jsonl_file)
    assert cassette.agent_name == "jsonl_agent"
    assert len(cassette.interactions) == 1
    assert cassette.interactions[0].name == "jsonl_tool"
    assert cassette.interactions[0].response == 6

"""Tests for AgentProbe CLI (pull, inspect, replay, exit codes)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentprobe.cassette.fingerprint import compute_fingerprint
from agentprobe.cassette.models import Cassette, Interaction


def _make_dummy_cassette(file_path: Path) -> Cassette:
    """Create and persist a minimal test cassette."""
    interactions = [
        Interaction(
            index=0,
            kind="tool",
            name="test_tool",
            request={"query": "test query"},
            response="test result",
            request_fingerprint="abc123def456",
            recorded_duration_ms=45.2,
        ),
        Interaction(
            index=1,
            kind="llm",
            name="generate_completion",
            request={"prompt": "hello"},
            response="world",
            request_fingerprint="fed987cba321",
            recorded_duration_ms=88.5,
            metadata={"usage": {"total_tokens": 42}},
        ),
    ]
    cas = Cassette(
        name="test_cli_cassette",
        agent_name="cli_agent",
        agent_version="1.0",
        agent_input={"args": ["hello"], "kwargs": {}},
        agent_output="world",
        interactions=interactions,
    )
    cas.save(file_path)
    return cas


def test_cli_inspect(tmp_path: Path) -> None:
    cas_file = tmp_path / "sample.cassette.json"
    _make_dummy_cassette(cas_file)

    cmd = [sys.executable, "-m", "agentprobe.cli", "cassette", "inspect", str(cas_file)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0
    assert "Cassette Details:" in res.stdout
    assert "cli_agent" in res.stdout
    assert "test_tool" in res.stdout
    assert "Total Tokens:  42" in res.stdout


def test_cli_inspect_missing_file() -> None:
    cmd = [
        sys.executable,
        "-m",
        "agentprobe.cli",
        "cassette",
        "inspect",
        "nonexistent.cassette.json",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 2
    assert "error:" in res.stderr


def test_cli_in_process_inspect_and_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentprobe.cli import build_parser, handle_cassette_inspect, handle_replay, main

    dummy_file = tmp_path / "inproc_agent.py"
    dummy_file.write_text(
        """
import agentprobe as ap
ap.init(project="test", exporter="none")
@ap.trace_tool(name="test_tool")
def test_tool(query): return "test result"
@ap.trace_agent(name="cli_agent")
def run_cli_agent(arg): return test_tool(arg)
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    import inproc_agent

    cas_file = tmp_path / "inproc.cassette.json"
    _, recorded_cas = Cassette.record(inproc_agent.run_cli_agent, "test query")
    recorded_cas.save(cas_file)

    # 1. Inspect
    parser = build_parser()
    inspect_args = parser.parse_args(["cassette", "inspect", str(cas_file)])
    ret = handle_cassette_inspect(inspect_args)
    assert ret == 0

    # 2. Clean replay with demo agent function
    replay_args = parser.parse_args(
        ["replay", "inproc_agent:run_cli_agent", "-c", str(cas_file), "--policy", "all_recorded"]
    )
    ret_replay = handle_replay(replay_args)
    assert ret_replay == 0

    # 3. Test main entrypoint with inspect
    monkeypatch.setattr(sys, "argv", ["agentprobe", "cassette", "inspect", str(cas_file)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0


def test_cli_replay_clean(tmp_path: Path) -> None:
    # Setup agent script
    agent_code = """
import agentprobe as ap

ap.init(project="test", exporter="none")

@ap.trace_tool(name="test_tool")
def dummy_tool(query: str) -> str:
    return "test result"

@ap.trace_agent(name="cli_agent")
def run_cli_agent(arg: str) -> str:
    res = dummy_tool("test query")
    return "world"
"""
    agent_file = tmp_path / "dummy_agent.py"
    agent_file.write_text(agent_code, encoding="utf-8")

    cas_file = tmp_path / "test.cassette.json"
    req_data = {"args": ["test query"], "kwargs": {}}
    cas = Cassette(
        name="test_replay_cas",
        agent_name="cli_agent",
        agent_input={"args": ["hello"], "kwargs": {}},
        interactions=[
            Interaction(
                index=0,
                kind="tool",
                name="test_tool",
                request=req_data,
                response="test result",
                request_fingerprint=compute_fingerprint(req_data),
                recorded_duration_ms=10.0,
            )
        ],
    )
    cas.save(cas_file)

    cmd = [
        sys.executable,
        "-m",
        "agentprobe.cli",
        "replay",
        "dummy_agent:run_cli_agent",
        "--cassette",
        str(cas_file),
        "--policy",
        "all_recorded",
    ]
    res = subprocess.run(cmd, cwd=str(tmp_path), capture_output=True, text=True)
    assert res.returncode == 0
    assert "SUCCESS" in res.stdout
    assert "Matched Steps:" in res.stdout


def test_cli_replay_divergence(tmp_path: Path) -> None:
    # Setup agent script that calls a different tool than expected
    agent_code = """
import agentprobe as ap

ap.init(project="test", exporter="none")

@ap.trace_tool(name="unexpected_tool")
def unexpected(x: str) -> str:
    return "unexpected"

@ap.trace_agent(name="cli_agent")
def run_divergent_agent(arg: str) -> str:
    return unexpected("arg")
"""
    agent_file = tmp_path / "divergent_agent.py"
    agent_file.write_text(agent_code, encoding="utf-8")

    cas_file = tmp_path / "expected.cassette.json"
    cas = Cassette(
        name="expected_cas",
        agent_name="cli_agent",
        agent_input={"args": ["hello"], "kwargs": {}},
        interactions=[
            Interaction(
                index=0,
                kind="tool",
                name="expected_tool",
                request={"args": ["hello"], "kwargs": {}},
                response="expected response",
                request_fingerprint="abc",
            )
        ],
    )
    cas.save(cas_file)

    cmd = [
        sys.executable,
        "-m",
        "agentprobe.cli",
        "replay",
        "divergent_agent:run_divergent_agent",
        "--cassette",
        str(cas_file),
        "--policy",
        "all_recorded",
        "--on-miss",
        "error",
    ]
    res = subprocess.run(cmd, cwd=str(tmp_path), capture_output=True, text=True)
    # Exit code 1 for divergence!
    assert res.returncode == 1
    assert "Divergence Report:" in res.stdout
    assert "FAILED" in res.stdout


def test_cli_usage_error() -> None:
    # Target missing colon
    cmd = [sys.executable, "-m", "agentprobe.cli", "replay", "invalid_target", "-c", "dummy.json"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 2
    assert "error:" in res.stderr


def test_cli_in_process_pull(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request
    from unittest.mock import MagicMock

    from agentprobe.cli import build_parser, handle_cassette_pull

    out_file = tmp_path / "pulled.cassette.json"
    fake_payload = {
        "content": {
            "name": "remote_cassette",
            "agent_name": "support_bot",
            "interactions": [],
        }
    }
    raw_bytes = json.dumps(fake_payload).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = raw_bytes
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    monkeypatch.setattr(urllib.request, "urlopen", lambda req: mock_resp)

    parser = build_parser()
    pull_args = parser.parse_args(
        ["cassette", "pull", "fake-run-id-1234", "-o", str(out_file), "--api-key", "test-key"]
    )
    code = handle_cassette_pull(pull_args)
    assert code == 0
    assert out_file.exists()


def test_cli_in_process_divergence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentprobe.cli import build_parser, handle_replay

    cas_file = tmp_path / "div.cassette.json"
    _make_dummy_cassette(cas_file)

    dummy_file = tmp_path / "div_agent.py"
    dummy_file.write_text(
        """
import agentprobe as ap
ap.init(project="test", exporter="none")
@ap.trace_tool(name="wrong_tool")
def wrong_tool(q): return "wrong"
@ap.trace_agent(name="cli_agent")
def run_cli_agent(arg): return wrong_tool("arg")
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    parser = build_parser()
    args = parser.parse_args(
        ["replay", "div_agent:run_cli_agent", "-c", str(cas_file), "--policy", "all_recorded"]
    )
    code = handle_replay(args)
    assert code == 1

"""Tests for AgentProbe deterministic replay engine, policies, matchers, and divergences."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

import agentprobe as ap
from agentprobe.cassette import Cassette
from agentprobe.replay import (
    Live,
    Mock,
    ReplayMismatchError,
    ReplayPolicy,
    areplay,
)


def test_replay_all_recorded_round_trip() -> None:
    call_counts = {"tool": 0, "llm": 0, "capture": 0}

    @ap.trace_tool(name="order_tool")
    def order_tool(order_id: str) -> dict[str, str]:
        call_counts["tool"] += 1
        return {"id": order_id, "status": "shipped"}

    @ap.trace_llm(name="mock_reasoner")
    def reasoner(prompt: str) -> str:
        call_counts["llm"] += 1
        return f"Response to: {prompt}"

    @ap.trace_agent(name="test_agent")
    def run_agent(oid: str) -> str:
        res = order_tool(oid)

        def make_msg_id() -> str:
            call_counts["capture"] += 1
            return "msg_fixed_123"

        _id = ap.capture("msg_id", make_msg_id)
        ans = reasoner(f"Order is {res['status']}, ref {_id}")
        return ans

    # 1. Record live execution
    initial_output, cassette = Cassette.record(run_agent, "ORD-555")
    assert initial_output == "Response to: Order is shipped, ref msg_fixed_123"
    assert call_counts["tool"] == 1
    assert call_counts["llm"] == 1
    assert len(cassette.interactions) == 3

    # Reset live counters
    call_counts["tool"] = 0
    call_counts["llm"] = 0
    call_counts["capture"] = 0

    # 2. Replay with all_recorded
    replay_res = ap.replay(run_agent, cassette, policy=ReplayPolicy.all_recorded())

    # Live functions must NOT have been called!
    assert call_counts["tool"] == 0
    assert call_counts["llm"] == 0
    assert call_counts["capture"] == 0

    assert replay_res.output == initial_output
    assert not replay_res.has_divergence
    assert len(replay_res.divergences) == 0
    assert len(replay_res.unconsumed) == 0
    assert replay_res.matched_count == 3
    assert len(replay_res.trace.steps) == 3
    assert replay_res.trace.tool_names() == ["order_tool"]


def test_replay_policy_llm_live_tools_recorded() -> None:
    tool_executed = False
    llm_executed = False

    @ap.trace_tool(name="frozen_tool")
    def frozen_tool() -> str:
        nonlocal tool_executed
        tool_executed = True
        return "live_tool_val"

    @ap.trace_llm(name="dynamic_llm")
    def dynamic_llm(text: str) -> str:
        nonlocal llm_executed
        llm_executed = True
        return f"live_llm_{text}"

    @ap.trace_agent(name="hybrid_agent")
    def run_agent() -> str:
        t = frozen_tool()
        return dynamic_llm(t)

    _, cassette = Cassette.record(run_agent)
    tool_executed = False
    llm_executed = False

    # Replay: LLM live, tools recorded
    res = ap.replay(run_agent, cassette, policy=ReplayPolicy.llm_live_tools_recorded())

    assert tool_executed is False  # Tool was frozen!
    assert llm_executed is True  # LLM executed live!
    assert res.matched_count == 1  # 1 tool matched
    assert res.live_count == 1  # 1 live LLM execution


def test_replay_named_overrides() -> None:
    tool_live_called = False

    @ap.trace_tool(name="tool_override_live")
    def tool_live() -> str:
        nonlocal tool_live_called
        tool_live_called = True
        return "live_executed"

    @ap.trace_tool(name="tool_override_mock")
    def tool_mock() -> str:
        return "original"

    @ap.trace_agent(name="override_agent")
    def run_agent() -> tuple[str, str]:
        return tool_live(), tool_mock()

    _, cassette = Cassette.record(run_agent)

    # Replay with per-name overrides
    res = ap.replay(
        run_agent,
        cassette,
        policy=ReplayPolicy.all_recorded(),
        overrides={
            "tool_override_live": Live(),
            "tool_override_mock": Mock(value="custom_mocked_value"),
        },
    )

    assert tool_live_called is True
    assert res.output == ("live_executed", "custom_mocked_value")
    assert res.mocked_count == 1
    assert res.live_count == 1


def test_replay_divergence_error_mode() -> None:
    @ap.trace_tool(name="search")
    def search(query: str) -> list[str]:
        return [f"result for {query}"]

    @ap.trace_agent(name="search_agent")
    def run_agent(q: str) -> list[str]:
        return search(q)

    _, cassette = Cassette.record(run_agent, "expected query")

    # Replay with altered query under on_miss="error"
    with pytest.raises(ReplayMismatchError) as exc_info:
        ap.replay(
            run_agent,
            cassette,
            on_miss="error",
            input_args=["unexpected query"],
        )

    err_msg = str(exc_info.value)
    assert "AgentProbe Replay Divergence at step #1" in err_msg
    assert "Expected fingerprint:" in err_msg
    assert "actual_request" in err_msg


def test_replay_divergence_live_and_skip_modes() -> None:
    @ap.trace_tool(name="lookup")
    def lookup(val: str) -> str:
        return f"live_{val}"

    @ap.trace_agent(name="divergent_agent")
    def run_agent(val: str) -> str:
        return lookup(val)

    _, cassette = Cassette.record(run_agent, "val1")

    # 1. on_miss="live" executes live function and records divergence
    res_live = ap.replay(
        run_agent,
        cassette,
        on_miss="live",
        input_args=["val2"],
    )
    assert res_live.output == "live_val2"
    assert len(res_live.divergences) == 1
    assert res_live.divergences[0].resolution == "live"
    assert res_live.has_divergence is True

    # 2. on_miss="skip" returns None and records divergence
    res_skip = ap.replay(
        run_agent,
        cassette,
        on_miss="skip",
        input_args=["val3"],
    )
    assert res_skip.output is None
    assert len(res_skip.divergences) == 1
    assert res_skip.divergences[0].resolution == "skip"


def test_replay_unconsumed_interactions() -> None:
    @ap.trace_tool(name="step_a")
    def step_a() -> str:
        return "a"

    @ap.trace_tool(name="step_b")
    def step_b() -> str:
        return "b"

    @ap.trace_agent(name="two_step_agent")
    def run_agent(skip_b: bool = False) -> list[str]:
        items = [step_a()]
        if not skip_b:
            items.append(step_b())
        return items

    _, cassette = Cassette.record(run_agent, False)
    assert len(cassette.interactions) == 2

    # Replay with skip_b=True -> step_b should be reported as unconsumed
    res = ap.replay(run_agent, cassette, input_args=[True])
    assert res.output == ["a"]
    assert len(res.unconsumed) == 1
    assert res.unconsumed[0].name == "step_b"
    assert res.has_divergence is True


def test_concurrent_matching_thread_safety() -> None:
    @ap.trace_tool(name="fetch_parallel")
    def fetch_parallel(item_id: int) -> str:
        return f"item_{item_id}"

    @ap.trace_agent(name="parallel_agent")
    def run_parallel() -> list[str]:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(fetch_parallel, i) for i in range(10)]
            return [f.result() for f in futures]

    _, cassette = Cassette.record(run_parallel)
    assert len(cassette.interactions) == 10

    # Replay concurrently
    res = ap.replay(run_parallel, cassette)
    assert len(res.output) == 10
    assert res.matched_count == 10
    assert not res.has_divergence


@pytest.mark.asyncio
async def test_async_replay_and_trace_inspection() -> None:
    @ap.trace_llm(name="async_llm")
    async def async_llm(p: str) -> str:
        return f"model_{p}"

    @ap.trace_tool(name="async_tool")
    async def async_tool(x: int) -> int:
        return x * 2

    @ap.trace_agent(name="full_async_agent")
    async def run_async() -> str:
        v = await async_tool(21)
        return await async_llm(f"val_{v}")

    _, cassette = await Cassette.arecord(run_async)
    assert len(cassette.interactions) == 2

    res = await areplay(run_async, cassette)
    assert res.output == "model_val_42"
    assert res.matched_count == 2

    # Test Trace class API
    trace = res.trace
    assert len(trace.llm_calls()) == 1
    assert len(trace.tool_calls()) == 1
    assert trace.tool_names() == ["async_tool"]
    assert len(trace.steps) == 2


def test_redaction_warnings_in_replay() -> None:
    @ap.trace_tool(name="secret_tool")
    def secret_tool() -> str:
        return "[REDACTED]"

    @ap.trace_agent(name="secret_agent")
    def run_secret() -> str:
        return secret_tool()

    _, cassette = Cassette.record(run_secret)
    assert cassette.interactions[0].redacted is True

    res = ap.replay(run_secret, cassette)
    assert res.output == "[REDACTED]"
    assert res.redaction_warnings == 1


def test_sequential_matcher_and_trace_methods() -> None:
    @ap.trace_tool(name="step_tool")
    def step_tool(param: str) -> str:
        return f"tool_{param}"

    @ap.trace_llm(name="step_llm")
    def step_llm(prompt: str) -> str:
        return f"llm_{prompt}"

    @ap.trace_agent(name="sequential_agent")
    def run_agent() -> tuple[str, str]:
        t = step_tool("first")
        return t, step_llm("second")

    _, cassette = Cassette.record(run_agent)

    # Replay using sequential matcher
    res = ap.replay(run_agent, cassette, matcher="sequential")
    assert res.output == ("tool_first", "llm_second")
    assert res.matched_count == 2
    assert not res.has_divergence

    # Test Trace properties & filters
    trace = res.trace
    assert trace.find_spans(name="step_tool")[0].name == "step_tool"
    assert len(trace.find_spans(span_type="tool")) == 1
    assert "Trace(spans=" in repr(trace)

    # Test ReplayResult summary formatting
    summary = res.summary()
    assert "AgentProbe Replay Summary" in summary
    assert "Matched Steps:       2" in summary

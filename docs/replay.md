# Deterministic Replay & Interception Guide

AgentProbe enables record-and-replay testing for LLM agents ("Selenium for AI agents"). Any traced agent execution can be recorded to a portable cassette and replayed deterministically in continuous integration (CI) or local environments.

---

## 1. Core Concepts

### Cassette
A **cassette** (`.cassette.json`) is a portable, self-contained record of an agent execution run. It captures:
- Top-level agent inputs and outputs
- Every model completion request, response, and token usage
- Every tool call arguments and results
- Non-deterministic entropy sources (UUIDs, timestamps) captured via `ap.capture`
- Monotonic timing measurements and structural parent/child hierarchy

### Interception
During replay, agent execution runs within an active `ReplaySession` context. Instrumentations evaluate policy rules before executing underlying code:
- **`@ap.trace_llm(name=..., model=...)`**: In replay mode, returns the recorded LLM response directly without invoking the external model provider.
- **`@ap.trace_tool(name=...)`**: In replay mode, returns the recorded tool payload without calling the real backend API, database, or email service.
- **`ap.capture(name, fn, *args)`**: Executes `fn` during recording and captures its return value. During replay, returns the recorded value without calling `fn`.
- **`ap.span(...)`**: Context managers record hierarchical execution traces, but cannot skip arbitrary code blocks. Replay interception requires function decorators or `ap.capture`.

---

## 2. Replay Policies

Replay behavior is governed by `ReplayPolicy`, allowing engineers to freeze everything or selectively run components live:

### 1. `all_recorded` (Default)
```python
policy = ap.ReplayPolicy.all_recorded()
```
- **LLM**: Recorded
- **Tools**: Recorded
- **Capture**: Recorded
- **Use Case**: Fast regression benchmarks in CI. Replays multi-turn agent logic with zero token costs, zero external network calls, and sub-millisecond execution speeds.

### 2. `llm_live_tools_recorded`
```python
policy = ap.ReplayPolicy.llm_live_tools_recorded()
```
- **LLM**: Live
- **Tools**: Recorded
- **Capture**: Recorded
- **Use Case**: Prompt engineering and model evaluation. Test updated prompt instructions or new foundation models (e.g., upgrading from GPT-4o to Claude 3.5 Sonnet) while guaranteeing external tools and database state remain completely frozen and safe from side-effects.

### 3. `tools_live_llm_recorded`
```python
policy = ap.ReplayPolicy.tools_live_llm_recorded()
```
- **LLM**: Recorded
- **Tools**: Live
- **Capture**: Recorded
- **Use Case**: Backend integration testing. Verify that changes to internal microservices, databases, or third-party APIs still satisfy the agent's expected schemas without incurring LLM API costs or dealing with model non-determinism.

---

## 3. Tool Overrides API

You can override policies for individual tools or model calls by name using `overrides`:

```python
result = ap.replay(
    run_agent,
    cassette,
    policy=ap.ReplayPolicy.all_recorded(),
    overrides={
        # Force a critical tool to run live against staging
        "get_account_balance": ap.Live(),
        # Inject an error or synthetic response to test error handling
        "charge_credit_card": ap.Mock(
            error=RuntimeError("Insufficient funds")
        ),
        # Return a static payload
        "search_knowledge_base": ap.Mock(value={"results": []}),
    },
)
```

- **`ap.Live()`**: Bypass recorded cassette data and execute the instrumented function live.
- **`ap.Mock(value=..., error=...)`**: Return a synthetic value or raise an exception to test resilience.

---

## 4. Matching Algorithms

When an agent invokes an instrumented function during replay, the `BaseMatcher` determines which recorded interaction to return:

### `FingerprintMatcher` (Default)
Matches interactions by SHA-256 request fingerprint using occurrence counters:
```python
ap.replay(run_agent, cassette, matcher="fingerprint")
```
- **Occurrence Counting**: Repeated identical calls (e.g. calling `get_order_status("ORD-1")` multiple times) consume matching interactions in the exact sequence they were recorded.
- **Concurrency-Safe**: Allows concurrent or out-of-order execution across distinct calls (e.g., `asyncio.gather(search_web(), get_weather())`).
- **Unconsumed Step Reporting**: Any recorded steps that were not executed by the agent are reported in `result.unconsumed`.

### `SequentialMatcher`
Enforces strict index-based sequential ordering:
```python
ap.replay(run_agent, cassette, matcher="sequential")
```
- **Strict Ordering**: Expects interactions in the exact order they were originally recorded.
- **Ordering Regression Detection**: Immediately catches ordering violations (e.g., agent attempting to send an email before checking database status) and raises a `ReplayMismatchError`.

---

## 5. Divergence Detection & Diffs

When an agent invocation diverges from the cassette under `on_miss="error"`, AgentProbe raises a `ReplayMismatchError` containing a unified diff and nearest neighbor suggestions:

```
AgentProbe Replay Divergence at step #2 (tool: 'send_email'):
  Expected fingerprint: de8b48e6a3454bf37deab55fe90ab6f2840e2b2a6aa1b3f557bed0856720ff2a
  Actual fingerprint:   37055c100e492c2e563ebfb6d983e9b75ceb245ecaa75d60c852fb55d5f5dac1
  Structured Request Diff:
    --- recorded_request
    +++ actual_request
    @@ -2,5 +2,7 @@
       "args": [],
       "kwargs": {
    -    "order_id": "ORD-88123"
    +    "body": "Customer reported headphones damaged in transit.",
    +    "recipient": "support@example.com",
    +    "subject": "Replacement Request"
       }
     }
  Closest recorded interactions in cassette:
    - Step #1 (tool: 'get_order_status'): fingerprint=de8b48e6a345...
```

### Miss Handling (`on_miss`)
Configure what happens when a call does not match:
- `"error"` (Default): Immediately aborts execution and raises `ReplayMismatchError`.
- `"live"`: Falls back to executing the live function, marking the generated span with `live_fallback: true`.
- `"skip"`: Swallows the mismatch and returns `None`.

---

## 6. Limits of Determinism

AgentProbe provides deterministic execution for instrumented Python code. However, certain patterns cannot be made deterministic automatically:

1. **Streaming Responses**:
   Streaming token-by-token generators (`stream=True`) are not replayed at the chunk level in Phase 2; record full completions at the function boundary.
2. **Multi-Process Concurrency without IPC Instrumentation**:
   Agents fanning out across multiple OS processes (`multiprocessing.Process`) require IPC context propagation. Contextvars propagate automatically within threads and asyncio tasks, but not across separate OS processes.
3. **Uninstrumented Side Effects**:
   Direct OS system calls, raw `requests` calls without `@ap.trace_tool`, or uncaptured `datetime.now()` calls outside `ap.capture` remain live and non-deterministic. Always instrument external calls or wrap non-deterministic generators with `ap.capture`.

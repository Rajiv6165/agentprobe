# AgentProbe 🕵️

> **"Selenium for AI Agents"** — Deterministic testing, tracing, cassette recording, and regression detection framework for autonomous LLM agents.

*Status: **Phase 2 of 8: Cassette Recording & Deterministic Replay (Work in progress)***

---

## What is AgentProbe?

AgentProbe instruments autonomous AI agents to record every step, tool invocation, and LLM reasoning call in production and testing environments.

- **Deterministic Recording**: Capture agent executions to portable, self-contained `.cassette.json` files.
- **Deterministic Replay**: Replay agents against recorded cassettes with frozen tool and model responses, or run models live against recorded backend databases (`llm_live_tools_recorded`).
- **Instant Divergence Detection**: Precision structural diffs and nearest neighbor suggestions when an agent behaves differently or calls tools out of order.
- **Fail-Safe & Non-Blocking**: Monotonic latency tracking, cross-batch idempotent ingestion, and background exporters that never degrade live agent execution.
- **Query & Hierarchy Engine**: Scalable PostgreSQL backend exposing runs, spans, and cassettes via REST API and CLI.

---

## Quickstart

Run the complete stack with standard Python tools or Make:

```bash
# 1. Start PostgreSQL and FastAPI Ingest Service (Docker)
python tasks.py up      # or: make up

# 2. Run the customer support demo agent (Phase 1)
python tasks.py demo    # or: make demo

# 3. Run the cassette recording & regression detection demo (Phase 2)
python tasks.py replay-demo  # or: make replay-demo
```

Interactive services:
- REST API Documentation: [http://localhost:8000/docs](http://localhost:8000/docs)
- Health Checks: [http://localhost:8000/health](http://localhost:8000/health) and [http://localhost:8000/ready](http://localhost:8000/ready)

---

## Cassette Recording & Deterministic Replay

Record an agent run live and replay it deterministically:

```python
import agentprobe as ap
from agentprobe.cassette import Cassette
from agentprobe.replay import replay, ReplayPolicy

# Instrument agent and tools
@ap.trace_tool(name="get_order_status")
def get_order_status(order_id: str):
    return {"status": "delivered"}

@ap.trace_llm(name="generate_completion")
def call_llm(messages, tools):
    return {"content": "Order delivered."}

@ap.trace_agent(name="support_agent", version="1.0")
def run_agent(question: str) -> str:
    # Autonomous agent tool loop
    ...

# 1. Record live execution to a portable cassette
output, cassette = Cassette.record(run_agent, "Where is order ORD-123?")
cassette.save("order.cassette.json")

# 2. Replay deterministically (100% frozen interactions, zero API costs)
result = replay(run_agent, cassette, policy=ReplayPolicy.all_recorded())
assert result.output == output
assert result.matched_count > 0

# 3. Test a new prompt or model live against recorded database tools
result = replay(
    run_agent,
    cassette,
    policy=ReplayPolicy.llm_live_tools_recorded(),
    on_miss="error",
)
```

For comprehensive guides, see:
- [Deterministic Replay & Interception Guide](docs/replay.md)
- [Cassette Format Specification](docs/cassette-format.md)
- [Cassette JSON Schema](docs/cassette.schema.json)

---

## CLI Usage

AgentProbe provides a pure standard-library CLI:

```bash
# Pull a recorded cassette from the API server
agentprobe cassette pull <run_id> -o test.cassette.json

# Inspect cassette metadata and recorded steps
agentprobe cassette inspect test.cassette.json

# Replay an agent entrypoint against a cassette
agentprobe replay my_agent:run_support_agent -c test.cassette.json --policy all_recorded
```

---

## Local Development & Cross-Platform Tasks

All commands run through `tasks.py` (standard library only, works on Windows, macOS, and Linux):

```bash
# Run pytest suites for both SDK and API with coverage reports
python tasks.py test    # or: make test

# Run ruff lint, format check, strict mypy, and schema drift checks
python tasks.py lint    # or: make lint

# Apply database migrations
python tasks.py migrate # or: make migrate
```


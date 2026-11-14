# AgentProbe 🕵️

> **"Selenium for AI Agents"** — Deterministic testing, tracing, adversarial fuzzing, and regression detection framework for autonomous LLM agents.

*Status: **Phase 1 of 8: Ingestion & Core Tracing Slice (Work in progress)***

---

## What is AgentProbe?

AgentProbe instruments autonomous AI agents to record every step, tool invocation, and LLM reasoning call in production and testing environments.

- **Deterministic Recording**: Captures inputs, outputs, errors, latency, and token metrics across nested agent hierarchies.
- **Fail-Safe & Non-Blocking**: Background-batched HTTP and offline JSONL exporters that never interrupt or degrade your agent execution.
- **Query & Hierarchy Engine**: Fast, scalable PostgreSQL backend exposing flat spans and assembled execution trees via REST API.

---

## Quickstart

Run the complete stack with one command:

```bash
# 1. Start PostgreSQL and FastAPI Ingest Service
make up

# 2. Run the customer support demo agent (no API keys required)
make demo
```

Once running, you can explore:
- Interactive API documentation: [http://localhost:8000/docs](http://localhost:8000/docs)
- Health check: [http://localhost:8000/health](http://localhost:8000/health)
- Readiness check: [http://localhost:8000/ready](http://localhost:8000/ready)

---

## SDK Usage Example

```python
import agentprobe as ap

# Initialize tracer
ap.init(project="production-support", endpoint="http://localhost:8000")

# Instrument agent workflow
@ap.trace_agent(name="support_agent", version="1.0")
def run_agent(question: str) -> str:
    results = search_kb(question)
    with ap.span("llm_reasoning", type="llm", model="gpt-4o") as s:
        s.set_output("Here is your resolution")
        s.set_usage(prompt_tokens=150, completion_tokens=40, cost=0.001)
    return "Resolution"

# Instrument tools
@ap.trace_tool(name="search_kb")
def search_kb(query: str) -> list[str]:
    return ["relevant article"]
```

---

## Local Development & Testing

```bash
# Run test suites with coverage reports
make test

# Run linter and strict type checker
make lint

# Stop Docker containers
make down
```

For more details, see [CONTRIBUTING.md](CONTRIBUTING.md).


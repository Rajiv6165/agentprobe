# AgentProbe Cassette Format Specification

Version: **1.0**  
Schema Definition: [`docs/cassette.schema.json`](cassette.schema.json)

---

## 1. Overview

An AgentProbe **Cassette** (`.cassette.json`) is a portable, structured JSON document containing the deterministic interaction log of an LLM agent execution. Cassettes decouple test playback from live foundation model APIs and external infrastructure.

### File Naming Convention
Cassette files use the extension `.cassette.json` (e.g. `damaged_order.cassette.json`).

---

## 2. Top-Level Structure

```json
{
  "schema_version": "1.0",
  "id": "e4b52b31-3037-4d43-a6d1-e6e2aa0b22a0",
  "name": "damaged_order_scenario",
  "created_at": "2026-11-19T14:51:16.692073Z",
  "source_run_id": "c29e51d2-5f8f-4c6f-a1b0-8d7a49f4bc47",
  "agent_name": "customer_support_agent",
  "agent_version": "1.0",
  "agent_input": {
    "args": ["My headphones arrived crushed and broken. Order ID is ORD-88123."],
    "kwargs": {}
  },
  "agent_output": "I verified order ORD-88123 was delivered yesterday...",
  "metadata": {
    "environment": "ci",
    "git_commit": "abcdef123"
  },
  "interactions": []
}
```

### Top-Level Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `schema_version` | string | Yes | Semantic format version. Must be `"1.0"`. |
| `id` | string (UUID) | Yes | Unique identifier for the cassette instance. |
| `name` | string | Yes | Human-readable scenario name. |
| `created_at` | string (ISO-8601) | Yes | UTC timestamp of cassette creation. |
| `source_run_id` | string | No | ID of the traced execution run that produced this cassette. |
| `agent_name` | string | Yes | Identifier of the instrumented agent. |
| `agent_version` | string | No | Version tag of the agent. |
| `agent_input` | object | Yes | Input arguments (`args`, `kwargs`) passed to the agent entrypoint. |
| `agent_output` | any | No | Returned value or synthesized output of the agent run. |
| `metadata` | object | No | Arbitrary tags, environment variables, or execution parameters. |
| `interactions` | array | Yes | Chronological sequence of intercepted interaction steps. |

---

## 3. Interaction Schema

Each entry in `interactions` represents a single model query, tool invocation, or non-deterministic entropy capture:

```json
{
  "index": 1,
  "kind": "tool",
  "name": "get_order_status",
  "request": {
    "args": [],
    "kwargs": {
      "order_id": "ORD-88123"
    }
  },
  "response": {
    "found": true,
    "order": {
      "id": "ORD-88123",
      "status": "Delivered"
    }
  },
  "error": null,
  "request_fingerprint": "de8b48e6a3454bf37deab55fe90ab6f2840e2b2a6aa1b3f557bed0856720ff2a",
  "recorded_duration_ms": 70.8,
  "parent_index": null,
  "redacted": false,
  "metadata": {}
}
```

### Interaction Fields

| Field | Type | Description |
|---|---|---|
| `index` | integer | Zero-based sequence index. |
| `kind` | string | `"llm"`, `"tool"`, or `"capture"`. |
| `name` | string | Function name or model identifier. |
| `request` | any | Canonicalized input arguments or prompt structure. |
| `response` | any | Recorded output or model response payload. |
| `error` | object \| null | Captured exception details (`type`, `message`, `traceback`). |
| `request_fingerprint` | string | 64-character SHA-256 hex digest of the normalized request. |
| `recorded_duration_ms` | float | Monotonic execution latency in milliseconds. |
| `parent_index` | integer \| null | Pointer to parent interaction index (e.g. capture inside a tool). |
| `redacted` | boolean | True if request/response contains sanitized sensitive data. |
| `metadata` | object | Model configuration, token usage, cost, and custom attributes. |

---

## 4. Canonicalization & Fingerprinting

AgentProbe computes deterministic request fingerprints to ensure semantically identical requests match even when dynamic values or dictionary ordering vary.

### Normalization Pipeline (`FingerprintNormalizer`):
1. **Unicode NFC**: Text strings are normalized to Unicode Standard Normalization Form C.
2. **Key Sorting**: Dictionaries and JSON maps are recursively sorted by key.
3. **Float Formatting**: Numbers are formatted with fixed 6-decimal-place precision (`%.6f`), preventing floating-point representation drift across architectures.
4. **Pointer Sanitization**: Ephemeral memory pointer representations (e.g. `<MockLLM object at 0x7f9a12b3c4d0>`) have addresses stripped.
5. **Pattern Masking**:
   - `UUID_REGEX`: Dynamic UUIDs (`[0-9a-fA-F-]{36}`) are masked to `<UUID>`.
   - `TIMESTAMP_REGEX`: ISO-8601 timestamps are masked to `<TIMESTAMP>`.
   - `HEX_ADDR_REGEX`: Hexadecimal pointer addresses are masked to `<ADDR>`.
6. **Configurable `ignore_keys`**: Volatile request header keys (such as `request_id`, `timestamp`, `nonce`) can be excluded from fingerprint calculation.

### Digest
The normalized JSON bytes are hashed using SHA-256:
$$\text{fingerprint} = \text{SHA256}(\text{CanonicalJSON}(\text{request}))$$

---

## 5. Redaction Handling

AgentProbe guarantees that sensitive credentials (API keys, authorization headers, credit cards) redacted during live tracing remain redacted in cassettes:
- Scrubbed values retain their `[REDACTED]` tokens.
- Affected interactions are tagged with `redacted: true`.
- During replay, the mocked output returns the redacted payload as recorded, incrementing `result.redaction_warnings` so test suites can verify sensitive data was not leaked.

---

## 6. Schema Validation & Drift Prevention

The canonical schema is exported at `docs/cassette.schema.json`. CI includes a validation check to guarantee the documentation and JSON Schema never drift from the Pydantic v2 `Cassette` implementation:

```bash
python -c "
import json
from agentprobe.cassette.models import Cassette
schema = json.loads(open('docs/cassette.schema.json').read())
assert schema == Cassette.model_json_schema()
"
```

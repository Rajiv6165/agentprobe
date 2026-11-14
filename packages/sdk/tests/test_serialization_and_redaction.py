"""Tests for safe serialization, truncation, and secret redaction."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from agentprobe.config import AgentProbeConfig
from agentprobe.transport import redact_secrets, sanitize_and_truncate


class SampleModel(BaseModel):
    name: str
    count: int


@dataclass
class SampleDataclass:
    id: int
    label: str


def test_sanitize_pydantic_and_dataclass() -> None:
    config = AgentProbeConfig()
    payload = {
        "pydantic": SampleModel(name="test", count=42),
        "dataclass": SampleDataclass(id=1, label="dc"),
        "raw_bytes": b"binary data \x00\x01",
        "timestamp": datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        "uuid": uuid.UUID("12345678-1234-5678-1234-567812345678"),
    }

    sanitized = sanitize_and_truncate(payload, config)
    assert sanitized["pydantic"] == {"name": "test", "count": 42}
    assert sanitized["dataclass"] == {"id": 1, "label": "dc"}
    assert sanitized["raw_bytes"] == "<bytes len=14>"
    assert sanitized["timestamp"] == "2026-01-01T12:00:00+00:00"
    assert sanitized["uuid"] == "12345678-1234-5678-1234-567812345678"


def test_sanitize_circular_reference() -> None:
    config = AgentProbeConfig()
    circular_dict: dict[str, Any] = {"name": "root"}
    circular_dict["self"] = circular_dict

    sanitized = sanitize_and_truncate(circular_dict, config)
    assert sanitized["name"] == "root"
    assert sanitized["self"] == "<CircularReference>"


def test_payload_truncation() -> None:
    # Set max payload size to 200 bytes
    config = AgentProbeConfig(max_payload_size_bytes=200)
    large_payload = {"huge_data": "x" * 1000}

    sanitized = sanitize_and_truncate(large_payload, config)
    assert isinstance(sanitized, dict)
    assert sanitized["_agentprobe_truncated"] is True
    assert sanitized["original_size_bytes"] > 1000
    assert "..." in sanitized["preview"]


def test_default_secret_redaction() -> None:
    config = AgentProbeConfig(redact_secrets=True)
    payload = {
        "auth_header": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.dummysecretpayload",
        "api_key_text": "using sk-1234567890abcdef1234567890 for auth",
        "nested": {
            "password": "SuperSecretPassword123!",
            "normal_field": "public value",
            "api_key": "my-secret-key",
            "user_token": "token-value-xyz",
        },
    }

    sanitized = redact_secrets(payload, config)
    assert "[REDACTED]" in sanitized["auth_header"]
    assert "[REDACTED]" in sanitized["api_key_text"]
    assert sanitized["nested"]["password"] == "[REDACTED]"
    assert sanitized["nested"]["api_key"] == "[REDACTED]"
    assert sanitized["nested"]["user_token"] == "[REDACTED]"
    assert sanitized["nested"]["normal_field"] == "public value"


def test_custom_redaction_hooks() -> None:
    def custom_mask_credit_card(val: Any) -> Any:
        if isinstance(val, str) and val.startswith("4111"):
            return "4111-XXXX-XXXX-XXXX"
        return val

    config = AgentProbeConfig(
        redact_secrets=True,
        custom_redactors=[custom_mask_credit_card],
    )

    payload = {"cc": "4111222233334444", "safe": "hello"}
    sanitized = redact_secrets(payload, config)
    assert sanitized["cc"] == "4111-XXXX-XXXX-XXXX"
    assert sanitized["safe"] == "hello"

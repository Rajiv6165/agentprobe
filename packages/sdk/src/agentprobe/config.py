"""Configuration management for the AgentProbe SDK."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

# Default regex patterns for sensitive data redaction
DEFAULT_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)(bearer\s+)[a-zA-Z0-9_\-\.]{15,}"),
    re.compile(r"(?i)(sk-[a-zA-Z0-9]{20,})"),
    re.compile(r"(?i)(key-[a-zA-Z0-9]{20,})"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*['\"]?)[a-zA-Z0-9_\-]{16,}"),
]

SENSITIVE_KEY_NAMES = {
    "password",
    "secret",
    "token",
    "auth_token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "private_key",
    "authorization",
}

NON_SENSITIVE_KEY_NAMES = {
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "tokens",
}


class AgentProbeConfig(BaseModel):
    """SDK runtime configuration options."""

    endpoint: str = Field(default="http://localhost:8000")
    project: str = Field(default="default")
    api_key: str | None = Field(default=None)
    exporter: str = Field(default="http")  # "http", "jsonl", "none"
    jsonl_path: str = Field(default="traces.jsonl")
    max_queue_size: int = Field(default=10000, gt=0)
    batch_size: int = Field(default=100, gt=0)
    flush_interval_seconds: float = Field(default=1.0, gt=0.0)
    max_payload_size_bytes: int = Field(default=65536, gt=0)
    redact_secrets: bool = Field(default=True)
    custom_redactors: list[Callable[[Any], Any]] = Field(default_factory=list)

    @classmethod
    def from_env(cls, **overrides: Any) -> AgentProbeConfig:
        """Construct configuration initialized from environment variables with overrides."""
        env_config: dict[str, Any] = {}

        if endpoint := os.getenv("AGENTPROBE_ENDPOINT"):
            env_config["endpoint"] = endpoint.rstrip("/")
        if project := os.getenv("AGENTPROBE_PROJECT"):
            env_config["project"] = project
        if api_key := os.getenv("AGENTPROBE_API_KEY"):
            env_config["api_key"] = api_key
        if exporter := os.getenv("AGENTPROBE_EXPORTER"):
            env_config["exporter"] = exporter.lower()
        if jsonl_path := os.getenv("AGENTPROBE_JSONL_PATH"):
            env_config["jsonl_path"] = jsonl_path
        if queue_size := os.getenv("AGENTPROBE_MAX_QUEUE_SIZE"):
            try:
                env_config["max_queue_size"] = int(queue_size)
            except ValueError:
                pass
        if batch_size := os.getenv("AGENTPROBE_BATCH_SIZE"):
            try:
                env_config["batch_size"] = int(batch_size)
            except ValueError:
                pass
        if flush_interval := os.getenv("AGENTPROBE_FLUSH_INTERVAL"):
            try:
                env_config["flush_interval_seconds"] = float(flush_interval)
            except ValueError:
                pass
        if max_payload := os.getenv("AGENTPROBE_MAX_PAYLOAD_SIZE"):
            try:
                env_config["max_payload_size_bytes"] = int(max_payload)
            except ValueError:
                pass

        env_config.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**env_config)

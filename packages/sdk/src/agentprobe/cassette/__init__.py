"""AgentProbe Cassette package: deterministic recording and replay format."""

from __future__ import annotations

from agentprobe.cassette.builder import from_jsonl, from_spans
from agentprobe.cassette.fingerprint import (
    FingerprintNormalizer,
    canonical_json,
    compute_fingerprint,
)
from agentprobe.cassette.models import (
    Cassette,
    Interaction,
    UnsupportedSchemaVersionError,
)

__all__ = [
    "Cassette",
    "FingerprintNormalizer",
    "Interaction",
    "UnsupportedSchemaVersionError",
    "canonical_json",
    "compute_fingerprint",
    "from_jsonl",
    "from_spans",
]

"""Canonicalization and deterministic SHA-256 fingerprinting for requests and payloads."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import is_dataclass
from typing import Any

from pydantic import BaseModel

DEFAULT_IGNORE_KEYS: set[str] = {
    "request_id",
    "timestamp",
    "started_at",
    "ended_at",
    "duration_ms",
    "message_id",
    "call_id",
    "created_at",
    "trace_id",
    "span_id",
}

UUID_REGEX: re.Pattern[str] = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
ISO_TIMESTAMP_REGEX: re.Pattern[str] = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
HEX_ADDR_REGEX: re.Pattern[str] = re.compile(r"\b0x[0-9a-fA-F]{6,16}\b")
OBJECT_ADDR_REGEX: re.Pattern[str] = re.compile(r" at 0x[0-9a-fA-F]+\b")

DEFAULT_REGEX_NORMALIZERS: list[tuple[re.Pattern[str], str]] = [
    (OBJECT_ADDR_REGEX, ""),
    (UUID_REGEX, "<UUID>"),
    (ISO_TIMESTAMP_REGEX, "<TIMESTAMP>"),
    (HEX_ADDR_REGEX, "<ADDR>"),
]


class FingerprintNormalizer:
    """Configurable normalization engine stripping volatile values before fingerprinting."""

    def __init__(
        self,
        ignore_keys: set[str] | None = None,
        regex_normalizers: list[tuple[re.Pattern[str], str]] | None = None,
        custom_normalizers: list[Callable[[Any], Any]] | None = None,
    ) -> None:
        self.ignore_keys = set(ignore_keys) if ignore_keys is not None else set(DEFAULT_IGNORE_KEYS)
        self.regex_normalizers = (
            list(regex_normalizers)
            if regex_normalizers is not None
            else list(DEFAULT_REGEX_NORMALIZERS)
        )
        self.custom_normalizers = list(custom_normalizers or [])

    @classmethod
    def default(cls) -> FingerprintNormalizer:
        """Create a normalizer with default UUID and timestamp regex masks and ignored keys."""
        return cls()

    def normalize(self, value: Any, seen: set[int] | None = None) -> Any:
        """Recursively scrub ignored keys and mask volatile regex patterns."""
        if seen is None:
            seen = set()

        if value is None or isinstance(value, (bool, int)):
            return value

        if isinstance(value, float):
            # Normalize float precision to avoid platform jitter
            return round(value, 8)

        if isinstance(value, str):
            res = unicodedata.normalize("NFC", value)
            for pattern, repl in self.regex_normalizers:
                res = pattern.sub(repl, res)
            for custom_fn in self.custom_normalizers:
                try:
                    res = custom_fn(res)
                except Exception:
                    pass
            return res

        val_id = id(value)
        if val_id in seen:
            return "<CircularReference>"
        seen.add(val_id)

        if isinstance(value, BaseModel):
            return self.normalize(value.model_dump(mode="json"), seen)

        if is_dataclass(value) and not isinstance(value, type):
            from dataclasses import asdict

            return self.normalize(asdict(value), seen)

        if isinstance(value, dict):
            normalized_dict: dict[str, Any] = {}
            for k, v in value.items():
                k_str = str(k)
                if k_str.lower() in self.ignore_keys:
                    continue
                normalized_dict[k_str] = self.normalize(v, seen)
            return normalized_dict

        if isinstance(value, (list, tuple, set)):
            return [self.normalize(item, seen) for item in value]

        return self.normalize(str(value), seen)


def canonical_json(data: Any) -> str:
    """Produce a deterministic, unicode-normalized JSON string with sorted keys."""

    def _prepare(obj: Any) -> Any:
        if obj is None or isinstance(obj, (bool, int)):
            return obj
        if isinstance(obj, float):
            # Stable representation of floats
            if obj.is_integer():
                return int(obj)
            return round(obj, 8)
        if isinstance(obj, str):
            return unicodedata.normalize("NFC", obj)
        if isinstance(obj, dict):
            return {k: _prepare(v) for k, v in sorted(obj.items(), key=lambda item: str(item[0]))}
        if isinstance(obj, (list, tuple)):
            return [_prepare(x) for x in obj]
        if isinstance(obj, set):
            return sorted([_prepare(x) for x in obj], key=lambda x: str(x))
        return str(obj)

    prepared = _prepare(data)
    return json.dumps(prepared, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_fingerprint(
    data: Any,
    normalizer: FingerprintNormalizer | None = None,
) -> str:
    """Compute deterministic SHA-256 hex digest of normalized canonical request data."""
    norm = normalizer or FingerprintNormalizer.default()
    normalized = norm.normalize(data)
    c_json = canonical_json(normalized)
    return hashlib.sha256(c_json.encode("utf-8")).hexdigest()

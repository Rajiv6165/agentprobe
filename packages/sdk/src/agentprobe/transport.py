"""Safe serialization, redaction, queueing, and background exporters for AgentProbe."""

from __future__ import annotations

import atexit
import json
import logging
import queue
import re
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from agentprobe.config import (
    DEFAULT_SECRET_PATTERNS,
    NON_SENSITIVE_KEY_NAMES,
    SENSITIVE_KEY_NAMES,
    AgentProbeConfig,
)
from agentprobe.models import IngestBatch, Run, Span

logger = logging.getLogger("agentprobe")


def redact_secrets(
    value: Any,
    config: AgentProbeConfig,
    seen: set[int] | None = None,
) -> Any:
    """Recursively scrub known credential patterns and sensitive keys from structures."""
    if not config.redact_secrets and not config.custom_redactors:
        return value

    if seen is None:
        seen = set()

    def _scrub(val: Any) -> Any:
        if val is None or isinstance(val, (bool, int, float)):
            return val

        val_id = id(val)
        if val_id in seen:
            return "<CircularReference>"

        # Run custom redactor callables if provided
        for custom_fn in config.custom_redactors:
            try:
                val = custom_fn(val)
            except Exception:
                pass

        if not config.redact_secrets:
            return val

        if isinstance(val, str):
            res = val
            for pattern in DEFAULT_SECRET_PATTERNS:
                res = pattern.sub(r"\1[REDACTED]", res)
            return res

        seen.add(val_id)

        if isinstance(val, dict):
            scrubbed_dict: dict[str, Any] = {}
            for k, v in val.items():
                k_str = str(k).lower()
                is_sensitive = k_str not in NON_SENSITIVE_KEY_NAMES and any(
                    secret_term in k_str for secret_term in SENSITIVE_KEY_NAMES
                )
                if is_sensitive:
                    scrubbed_dict[k] = "[REDACTED]"
                else:
                    scrubbed_dict[k] = _scrub(v)
            return scrubbed_dict

        if isinstance(val, (list, tuple)):
            return [_scrub(item) for item in val]

        return val

    return _scrub(value)


def sanitize_and_truncate(
    value: Any,
    config: AgentProbeConfig,
) -> Any:
    """Convert non-JSON-serializable objects and enforce payload size limits with markers."""
    # Redact secrets before serialization using dedicated cycle detection
    redacted = redact_secrets(value, config)
    seen: set[int] = set()

    def _convert(obj: Any) -> Any:
        if obj is None or isinstance(obj, (bool, int, float, str)):
            return obj

        obj_id = id(obj)
        if obj_id in seen:
            return "<CircularReference>"
        seen.add(obj_id)

        try:
            if isinstance(obj, BaseModel):
                return obj.model_dump(mode="json")
            if is_dataclass(obj) and not isinstance(obj, type):
                return asdict(obj)
            if isinstance(obj, (bytes, bytearray)):
                return f"<bytes len={len(obj)}>"
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            if isinstance(obj, uuid.UUID):
                return str(obj)
            if isinstance(obj, (list, tuple, set)):
                return [_convert(x) for x in obj]
            if isinstance(obj, dict):
                return {str(k): _convert(v) for k, v in obj.items()}
            return re.sub(r" at 0x[0-9a-fA-F]+", "", str(obj))
        except Exception:
            return re.sub(r" at 0x[0-9a-fA-F]+", "", repr(obj))

    converted = _convert(redacted)

    # Payload size enforcement
    try:
        raw_json = json.dumps(converted, default=str)
        raw_size = len(raw_json.encode("utf-8"))
        if raw_size > config.max_payload_size_bytes:
            if isinstance(converted, str):
                truncated_preview = converted[: min(len(converted), 500)]
            else:
                truncated_preview = raw_json[:500]
            return {
                "_agentprobe_truncated": True,
                "original_size_bytes": raw_size,
                "preview": f"{truncated_preview}...",
            }
    except Exception:
        pass

    return converted


class BaseExporter(ABC):
    """Abstract interface for trace exporters."""

    @abstractmethod
    def enqueue_run(self, run: Run) -> None:
        """Enqueue a Run object for export."""

    @abstractmethod
    def enqueue_span(self, span: Span) -> None:
        """Enqueue a Span object for export."""

    @abstractmethod
    def flush(self, timeout: float = 5.0) -> None:
        """Flush any pending spans or runs."""

    @abstractmethod
    def close(self) -> None:
        """Cleanly shutdown and release resources."""


class NullExporter(BaseExporter):
    """No-op exporter for testing or offline silence."""

    def enqueue_run(self, run: Run) -> None:
        pass

    def enqueue_span(self, span: Span) -> None:
        pass

    def flush(self, timeout: float = 5.0) -> None:
        pass

    def close(self) -> None:
        pass


class JsonlExporter(BaseExporter):
    """Exports traces locally to a newline-delimited JSON file."""

    def __init__(self, config: AgentProbeConfig) -> None:
        self.config = config
        self.path = Path(config.jsonl_path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def enqueue_run(self, run: Run) -> None:
        self._write_entry("run", run.model_dump(mode="python"))

    def enqueue_span(self, span: Span) -> None:
        self._write_entry("span", span.model_dump(mode="python"))

    def _write_entry(self, entry_type: str, data: dict[str, Any]) -> None:
        try:
            sanitized = sanitize_and_truncate(data, self.config)
            payload = json.dumps({"type": entry_type, "data": sanitized}) + "\n"
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(payload)
        except Exception as e:
            logger.warning("AgentProbe JsonlExporter write failed: %s", e)

    def flush(self, timeout: float = 5.0) -> None:
        pass

    def close(self) -> None:
        pass


class HttpExporter(BaseExporter):
    """Non-blocking, background-batched HTTP exporter sending traces to /v1/ingest."""

    def __init__(self, config: AgentProbeConfig) -> None:
        self.config = config
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=config.max_queue_size)
        self._shutdown_event = threading.Event()
        self._wake_event = threading.Event()
        self._empty_event = threading.Event()
        self._empty_event.set()
        self._lock = threading.Lock()

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "agentprobe-python-sdk/0.1.0",
        }
        if config.api_key:
            headers["X-AgentProbe-Key"] = config.api_key

        self._client = httpx.Client(
            base_url=config.endpoint,
            headers=headers,
            timeout=10.0,
        )

        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="agentprobe-exporter-worker",
            daemon=True,
        )
        self._worker_thread.start()
        atexit.register(self.close)

    def _enqueue(self, item_type: str, item: Any) -> None:
        try:
            self._queue.put_nowait((item_type, item))
            self._empty_event.clear()
            if self._queue.qsize() >= self.config.batch_size:
                self._wake_event.set()
        except queue.Full:
            # Drop-oldest policy: drop front item to make room for newest
            try:
                _ = self._queue.get_nowait()
                self._queue.task_done()
                logger.warning(
                    "AgentProbe exporter queue full (%d items). Dropped oldest item.",
                    self.config.max_queue_size,
                )
                self._queue.put_nowait((item_type, item))
            except Exception:
                pass

    def enqueue_run(self, run: Run) -> None:
        self._enqueue("run", run)

    def enqueue_span(self, span: Span) -> None:
        self._enqueue("span", span)

    def _worker_loop(self) -> None:
        while not self._shutdown_event.is_set():
            # Wait for flush interval or batch threshold trigger
            self._wake_event.wait(timeout=self.config.flush_interval_seconds)
            self._wake_event.clear()
            self._process_batch()

        # Final drain on shutdown
        self._process_batch()

    def _process_batch(self) -> None:
        runs: list[Run] = []
        spans: list[Span] = []

        while len(runs) + len(spans) < self.config.batch_size:
            try:
                item_type, item = self._queue.get_nowait()
                if item_type == "run":
                    runs.append(item)
                elif item_type == "span":
                    spans.append(item)
                self._queue.task_done()
            except queue.Empty:
                break

        if not runs and not spans:
            if self._queue.empty():
                self._empty_event.set()
            return

        batch = IngestBatch(runs=runs, spans=spans)
        self._send_batch(batch)

        if self._queue.empty():
            self._empty_event.set()

    def _send_batch(self, batch: IngestBatch) -> None:
        try:
            payload = batch.model_dump(mode="python")
            sanitized = sanitize_and_truncate(payload, self.config)
            response = self._client.post("/v1/ingest", json=sanitized)
            if response.status_code >= 400:
                logger.warning(
                    "AgentProbe ingest returned status %d: %s",
                    response.status_code,
                    response.text,
                )
        except Exception as exc:
            # SDK must NEVER crash or interrupt agent execution
            logger.warning("AgentProbe export failed (swallowed): %s", exc)

    def flush(self, timeout: float = 5.0) -> None:
        """Trigger immediate export of pending items and wait until queue is drained."""
        self._wake_event.set()
        # Wait until empty event is set or timeout occurs
        self._empty_event.wait(timeout=timeout)

    def close(self) -> None:
        """Shutdown exporter cleanly."""
        with self._lock:
            if self._shutdown_event.is_set():
                return
            self._shutdown_event.set()
            self._wake_event.set()

        try:
            self._worker_thread.join(timeout=2.0)
        except Exception:
            pass

        try:
            self._client.close()
        except Exception:
            pass


def create_exporter(config: AgentProbeConfig) -> BaseExporter:
    """Factory creating an exporter instance based on configuration."""
    exporter_type = config.exporter.lower().strip()
    if exporter_type == "none":
        return NullExporter()
    if exporter_type == "jsonl":
        return JsonlExporter(config)
    return HttpExporter(config)

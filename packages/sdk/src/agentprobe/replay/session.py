"""Active replay session state, context propagation, and interception coordinator."""

from __future__ import annotations

import contextvars
import threading
from typing import Any, Literal

from agentprobe.cassette.fingerprint import FingerprintNormalizer, compute_fingerprint
from agentprobe.cassette.models import Cassette
from agentprobe.replay.matcher import BaseMatcher, create_matcher
from agentprobe.replay.policy import Live, Mock, ReplayPolicy
from agentprobe.replay.result import (
    Divergence,
    ReplayMismatchError,
    compute_readable_diff,
)

_current_replay_session: contextvars.ContextVar[ReplaySession | None] = contextvars.ContextVar(
    "agentprobe_current_replay_session", default=None
)
_global_active_replay_session: ReplaySession | None = None
_session_lock = threading.Lock()


def get_current_replay_session() -> ReplaySession | None:
    """Retrieve active replay session, returning None if live execution is unintercepted."""
    sess = _current_replay_session.get()
    if sess is not None:
        return sess
    with _session_lock:
        return _global_active_replay_session


def set_current_replay_session(session: ReplaySession) -> contextvars.Token[ReplaySession | None]:
    """Set the active replay session for the current asynchronous context."""
    global _global_active_replay_session
    with _session_lock:
        _global_active_replay_session = session
    return _current_replay_session.set(session)


def reset_current_replay_session(token: contextvars.Token[ReplaySession | None]) -> None:
    """Restore previous replay session context."""
    global _global_active_replay_session
    _current_replay_session.reset(token)
    with _session_lock:
        _global_active_replay_session = _current_replay_session.get()


class ReplaySession:
    """Coordinates interaction interception, matching, policies, and divergence recording."""

    def __init__(
        self,
        cassette: Cassette,
        policy: ReplayPolicy | None = None,
        overrides: dict[str, Any] | None = None,
        on_miss: Literal["error", "live", "skip"] = "error",
        matcher: BaseMatcher | str | None = None,
        normalizer: FingerprintNormalizer | None = None,
    ) -> None:
        self.cassette = cassette
        self.policy = policy or ReplayPolicy.all_recorded()
        self.overrides = overrides or {}
        self.on_miss = on_miss
        self.normalizer = normalizer or FingerprintNormalizer.default()

        if isinstance(matcher, BaseMatcher):
            self.matcher = matcher
        elif isinstance(matcher, str):
            self.matcher = create_matcher(cassette, matcher, policy=self.policy)
        else:
            self.matcher = create_matcher(cassette, "fingerprint", policy=self.policy)

        self.divergences: list[Divergence] = []
        self.matched_count: int = 0
        self.live_count: int = 0
        self.mocked_count: int = 0
        self.redaction_warnings: int = 0
        self.step_counter: int = 0

    def intercept(
        self,
        kind: str,
        name: str,
        request: Any,
    ) -> tuple[str, Any]:
        """Intercept an execution step according to policy and overrides.

        Returns:
            Tuple of (resolution: "recorded" | "live" | "mock" | "skip", payload: Any)
        """
        self.step_counter += 1

        # 1. Per-name overrides win over per-kind policies
        if name in self.overrides:
            override_val = self.overrides[name]
            if isinstance(override_val, Live) or override_val == "live":
                self.live_count += 1
                return "live", None
            if isinstance(override_val, Mock):
                self.mocked_count += 1
                return "mock", override_val
            # Static value or callable
            self.mocked_count += 1
            return "mock", Mock(value=override_val)

        # 2. Check per-kind policy
        kind_attr = kind if hasattr(self.policy, kind) else f"{kind}s"
        kind_policy = getattr(self.policy, kind_attr, "recorded")
        if kind_policy == "live":
            self.live_count += 1
            return "live", None
        if kind_policy == "mock":
            self.mocked_count += 1
            return "mock", Mock(value=None)

        # 3. Recorded policy: query matcher
        fingerprint = compute_fingerprint(request, normalizer=self.normalizer)
        matched, nearest = self.matcher.match(
            kind=kind, name=name, request=request, fingerprint=fingerprint
        )

        if matched is not None:
            self.matched_count += 1
            if matched.redacted:
                self.redaction_warnings += 1
            return "recorded", matched

        # 4. Miss handling
        expected_fp = nearest.request_fingerprint if nearest else None
        expected_req = nearest.request if nearest else None
        diff_str = (
            compute_readable_diff(expected_req, request)
            if nearest is not None
            else "No interaction found in cassette."
        )

        divergence = Divergence(
            index=self.step_counter,
            kind=kind,
            name=name,
            expected_fingerprint=expected_fp,
            actual_fingerprint=fingerprint,
            resolution=self.on_miss,
            expected_request=expected_req,
            actual_request=request,
            diff=diff_str,
        )
        self.divergences.append(divergence)

        if self.on_miss == "error":
            nearest_list = [nearest] if nearest else []
            raise ReplayMismatchError(divergence, nearest_interactions=nearest_list)
        elif self.on_miss == "live":
            self.live_count += 1
            return "live", None
        else:  # "skip"
            return "skip", None

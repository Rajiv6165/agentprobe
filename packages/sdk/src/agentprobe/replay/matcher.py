"""Thread-safe and asyncio-safe cassette interaction matching algorithms."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from agentprobe.cassette.models import Cassette, Interaction

if TYPE_CHECKING:
    from agentprobe.replay.policy import ReplayPolicy


def _is_recorded(interaction: Interaction, policy: ReplayPolicy | None) -> bool:
    if policy is None:
        return True
    kind_attr = interaction.kind if hasattr(policy, interaction.kind) else f"{interaction.kind}s"
    return getattr(policy, kind_attr, "recorded") == "recorded"


class BaseMatcher(ABC):
    """Abstract strategy for matching runtime invocations against cassette interactions."""

    @abstractmethod
    def match(
        self,
        kind: str,
        name: str,
        request: Any,
        fingerprint: str,
    ) -> tuple[Interaction | None, Interaction | None]:
        """Attempt to match an invocation.

        Returns:
            Tuple of (matched_interaction, nearest_candidate_if_miss)
        """

    @abstractmethod
    def get_unconsumed(self) -> list[Interaction]:
        """Return all interactions that were never consumed during replay."""


class FingerprintMatcher(BaseMatcher):
    """Matches interactions by deterministic request fingerprint with per-fingerprint occurrence counters.

    This ensures that repeated identical calls consume interactions in sequence, while independent or concurrent
    calls match out of order safely.
    """

    def __init__(self, cassette: Cassette, policy: ReplayPolicy | None = None) -> None:
        self.cassette = cassette
        self.policy = policy
        self._lock = threading.Lock()
        self._consumed_indices: set[int] = set()

        # Group interactions by fingerprint in recorded order
        self._by_fingerprint: dict[str, list[Interaction]] = {}
        for item in cassette.interactions:
            self._by_fingerprint.setdefault(item.request_fingerprint, []).append(item)

        # Occurrence counter per fingerprint
        self._fingerprint_counters: dict[str, int] = {fp: 0 for fp in self._by_fingerprint}

    def match(
        self,
        kind: str,
        name: str,
        request: Any,
        fingerprint: str,
    ) -> tuple[Interaction | None, Interaction | None]:
        with self._lock:
            candidates = self._by_fingerprint.get(fingerprint, [])
            current_idx = self._fingerprint_counters.get(fingerprint, 0)

            if current_idx < len(candidates):
                interaction = candidates[current_idx]
                self._fingerprint_counters[fingerprint] = current_idx + 1
                self._consumed_indices.add(interaction.index)
                return interaction, None

            # Miss: Find nearest candidates (e.g. same name or same kind)
            nearest = self._find_nearest(kind, name)
            return None, nearest

    def _find_nearest(self, kind: str, name: str) -> Interaction | None:
        """Find the earliest unconsumed interaction matching same name or kind for diagnostic reporting."""
        unconsumed = [
            i for i in self.cassette.interactions if i.index not in self._consumed_indices
        ]
        # First preference: matching name and kind
        for u in unconsumed:
            if u.name == name and u.kind == kind:
                return u
        # Second preference: matching name
        for u in unconsumed:
            if u.name == name:
                return u
        # Third preference: any unconsumed
        return unconsumed[0] if unconsumed else None

    def get_unconsumed(self) -> list[Interaction]:
        with self._lock:
            return [
                i
                for i in self.cassette.interactions
                if i.index not in self._consumed_indices
                and _is_recorded(i, self.policy)
                and (i.parent_index is None or i.parent_index not in self._consumed_indices)
            ]


class SequentialMatcher(BaseMatcher):
    """Matches interactions strictly by index-based sequential ordering, raising on ordering differences."""

    def __init__(self, cassette: Cassette, policy: ReplayPolicy | None = None) -> None:
        self.cassette = cassette
        self.policy = policy
        self._lock = threading.Lock()
        self._consumed_indices: set[int] = set()
        self._cursor: int = 0

        # Build sequence of expected interactions:
        # Filter to interactions that are recorded under policy,
        # and whose parent is not also recorded (parent mock satisfies child).
        self._expected: list[Interaction] = []
        for item in cassette.interactions:
            if not _is_recorded(item, policy):
                continue
            if item.parent_index is not None:
                parent_item = next(
                    (p for p in cassette.interactions if p.index == item.parent_index),
                    None,
                )
                if parent_item is not None and _is_recorded(parent_item, policy):
                    # Parent is recorded/mocked, so child won't be called directly
                    continue
            self._expected.append(item)

    def match(
        self,
        kind: str,
        name: str,
        request: Any,
        fingerprint: str,
    ) -> tuple[Interaction | None, Interaction | None]:
        with self._lock:
            if self._cursor < len(self._expected):
                expected_interaction = self._expected[self._cursor]
                # Check if actual invocation matches the next expected interaction
                if (
                    expected_interaction.kind == kind
                    and expected_interaction.name == name
                    and expected_interaction.request_fingerprint == fingerprint
                ):
                    self._cursor += 1
                    self._consumed_indices.add(expected_interaction.index)
                    return expected_interaction, None
                else:
                    # Mismatch: Return None and the expected candidate interaction that was anticipated
                    return None, expected_interaction

            # If all expected interactions are already consumed, any new call is a miss
            unconsumed = [
                i for i in self.cassette.interactions if i.index not in self._consumed_indices
            ]
            nearest = unconsumed[0] if unconsumed else None
            return None, nearest

    def get_unconsumed(self) -> list[Interaction]:
        with self._lock:
            return [
                i for i in self._expected[self._cursor :] if i.index not in self._consumed_indices
            ]


def create_matcher(
    cassette: Cassette,
    matcher_type: str = "fingerprint",
    policy: ReplayPolicy | None = None,
) -> BaseMatcher:
    """Factory creating appropriate matcher strategy."""
    m_type = matcher_type.lower().strip()
    if m_type == "sequential":
        return SequentialMatcher(cassette, policy=policy)
    return FingerprintMatcher(cassette, policy=policy)

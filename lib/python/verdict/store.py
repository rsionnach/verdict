"""Verdict store interface and query operations."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from verdict.core import resolve as _core_resolve
from verdict.models import AccuracyReport, GroundTruth, Outcome, Override, Verdict


@dataclass
class VerdictFilter:
    producer_system: str | None = None
    subject_type: str | None = None
    subject_agent: str | None = None
    subject_service: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    from_time: datetime | None = None
    to_time: datetime | None = None
    limit: int = 100


@dataclass
class AccuracyFilter:
    producer_system: str
    from_time: datetime | None = None
    to_time: datetime | None = None
    dimension: str | None = None


class VerdictStore(ABC):
    """Abstract interface for verdict storage."""

    @abstractmethod
    def put(self, verdict: Verdict) -> None:
        """Store a verdict."""

    @abstractmethod
    def get(self, verdict_id: str) -> Verdict | None:
        """Retrieve a verdict by ID."""

    @abstractmethod
    def query(self, criteria: VerdictFilter) -> list[Verdict]:
        """Query verdicts matching a filter."""

    @abstractmethod
    def update_outcome(self, verdict_id: str, outcome: Outcome) -> Verdict:
        """Update a verdict's outcome."""

    @abstractmethod
    def accuracy(self, criteria: AccuracyFilter) -> AccuracyReport:
        """Compute accuracy metrics from resolved verdicts."""

    @abstractmethod
    def by_lineage(
        self, verdict_id: str, direction: str = "both",
    ) -> list[Verdict]:
        """Traverse the verdict chain.

        Args:
            verdict_id: Starting verdict ID.
            direction: "up" (parents/context), "down" (children), or "both".
        """

    @abstractmethod
    def expire(self) -> int:
        """Expire pending verdicts past their TTL. Returns count expired."""

    def resolve(
        self,
        verdict_id: str,
        status: str,
        override: dict | Override | None = None,
        ground_truth: dict | GroundTruth | None = None,
        resolution: str | None = None,
    ) -> Verdict:
        """Resolve a verdict in the store: get, validate, persist in one call.

        Delegates validation to core.resolve(). Returns the persisted verdict.
        Thread-safety depends on the concrete store's get/update_outcome methods.

        Raises:
            KeyError: If verdict_id not found in store.
            ValueError: If verdict is not pending, or status is invalid.
        """
        verdict = self.get(verdict_id)
        if verdict is None:
            raise KeyError(f"Verdict {verdict_id} not found")
        _core_resolve(
            verdict, status, override=override,
            ground_truth=ground_truth, resolution=resolution,
        )
        return self.update_outcome(verdict_id, verdict.outcome)


class MemoryStore(VerdictStore):
    """In-memory verdict store for testing and development. Not thread-safe."""

    def __init__(self) -> None:
        self._verdicts: dict[str, Verdict] = {}
        self._lock = threading.Lock()

    def put(self, verdict: Verdict) -> None:
        with self._lock:
            self._verdicts[verdict.id] = verdict

    def get(self, verdict_id: str) -> Verdict | None:
        with self._lock:
            return self._verdicts.get(verdict_id)

    def query(self, criteria: VerdictFilter) -> list[Verdict]:
        with self._lock:
            results = list(self._verdicts.values())

        if criteria.producer_system:
            results = [v for v in results if v.producer.system == criteria.producer_system]
        if criteria.subject_type:
            results = [v for v in results if v.subject.type == criteria.subject_type]
        if criteria.subject_agent:
            results = [v for v in results if v.subject.agent == criteria.subject_agent]
        if criteria.subject_service:
            results = [v for v in results if v.subject.service == criteria.subject_service]
        if criteria.status:
            results = [v for v in results if v.outcome.status == criteria.status]
        if criteria.tags:
            results = [
                v for v in results
                if v.judgment.tags and set(criteria.tags) & set(v.judgment.tags)
            ]
        if criteria.from_time:
            results = [v for v in results if v.timestamp >= criteria.from_time]
        if criteria.to_time:
            results = [v for v in results if v.timestamp <= criteria.to_time]

        results.sort(key=lambda v: v.timestamp, reverse=True)

        if criteria.limit > 0:
            return results[: criteria.limit]
        return results

    def _query_all(
        self,
        producer_system: str | None = None,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
    ) -> list[Verdict]:
        """Internal query without limit, for accuracy computation."""
        return self.query(VerdictFilter(
            producer_system=producer_system,
            from_time=from_time,
            to_time=to_time,
            limit=0,
        ))

    def update_outcome(self, verdict_id: str, outcome: Outcome) -> Verdict:
        with self._lock:
            verdict = self._verdicts.get(verdict_id)
            if verdict is None:
                raise KeyError(f"Verdict {verdict_id} not found")
            verdict.outcome = outcome
            return verdict

    def accuracy(self, criteria: AccuracyFilter) -> AccuracyReport:
        verdicts = self._query_all(
            producer_system=criteria.producer_system,
            from_time=criteria.from_time,
            to_time=criteria.to_time,
        )

        total = len(verdicts)
        confirmed = [v for v in verdicts if v.outcome.status == "confirmed"]
        overridden = [v for v in verdicts if v.outcome.status == "overridden"]
        partial = [v for v in verdicts if v.outcome.status == "partial"]
        pending = [v for v in verdicts if v.outcome.status == "pending"]

        total_resolved = len(confirmed) + len(overridden) + len(partial)

        def safe_div(a: float, b: float) -> float:
            return a / b if b > 0 else 0.0

        def mean_confidence(vs: list[Verdict]) -> float:
            if not vs:
                return 0.0
            return sum(v.judgment.confidence for v in vs) / len(vs)

        confirmation_rate = safe_div(len(confirmed), total_resolved)
        override_rate = safe_div(len(overridden), total_resolved)

        mean_conf_confirmed = mean_confidence(confirmed)
        mean_conf_overridden = mean_confidence(overridden)

        return AccuracyReport(
            producer=criteria.producer_system,
            total=total,
            total_resolved=total_resolved,
            confirmation_rate=confirmation_rate,
            override_rate=override_rate,
            partial_rate=safe_div(len(partial), total_resolved),
            pending_rate=safe_div(len(pending), total),
            mean_confidence_on_confirmed=mean_conf_confirmed,
            mean_confidence_on_overridden=mean_conf_overridden,
            dimension=criteria.dimension,
        )

    def by_lineage(
        self, verdict_id: str, direction: str = "both",
    ) -> list[Verdict]:
        if direction not in ("up", "down", "both"):
            raise ValueError(f"direction must be 'up', 'down', or 'both', got '{direction}'")

        visited: set[str] = set()
        result: list[Verdict] = []

        def traverse_up(vid: str) -> None:
            with self._lock:
                v = self._verdicts.get(vid)
            if v is None or vid in visited:
                return
            visited.add(vid)
            result.append(v)
            if v.lineage.parent:
                traverse_up(v.lineage.parent)
            for ctx_id in v.lineage.context:
                traverse_up(ctx_id)

        def traverse_down(vid: str) -> None:
            with self._lock:
                v = self._verdicts.get(vid)
            if v is None or vid in visited:
                return
            visited.add(vid)
            result.append(v)
            for child_id in v.lineage.children:
                traverse_down(child_id)

        # Always skip the starting verdict itself
        visited.add(verdict_id)

        with self._lock:
            start = self._verdicts.get(verdict_id)
        if start is None:
            return []

        if direction in ("up", "both"):
            if start.lineage.parent:
                traverse_up(start.lineage.parent)
            for ctx_id in start.lineage.context:
                traverse_up(ctx_id)

        if direction in ("down", "both"):
            for child_id in start.lineage.children:
                traverse_down(child_id)

        return result

    def expire(self) -> int:
        now = datetime.now(timezone.utc)
        count = 0
        with self._lock:
            for verdict in self._verdicts.values():
                if verdict.outcome.status != "pending":
                    continue
                expiry_time = verdict.timestamp + timedelta(seconds=verdict.metadata.ttl)
                if expiry_time < now:
                    verdict.outcome.status = "expired"
                    verdict.outcome.closed_at = now
                    count += 1
        return count

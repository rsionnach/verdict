"""Tests for verdict store operations."""

from datetime import datetime, timedelta, timezone

import pytest

from verdict.core import create, resolve
from verdict.sqlite_store import SQLiteVerdictStore
from verdict.store import AccuracyFilter, MemoryStore, VerdictFilter


def _make_verdict(system="test", confidence=0.8, **overrides):
    kwargs = dict(
        subject={"type": "review", "ref": "git:abc123", "summary": "Test"},
        judgment={"action": "approve", "confidence": confidence},
        producer={"system": system},
    )
    kwargs.update(overrides)
    return create(**kwargs)


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryStore()
    return SQLiteVerdictStore(tmp_path / "test.db")


class TestStorePutAndGet:
    def test_put_and_get(self, store):
        v = _make_verdict()
        store.put(v)
        retrieved = store.get(v.id)
        assert retrieved is not None
        assert retrieved.id == v.id
        assert retrieved.producer.system == v.producer.system
        assert retrieved.subject.type == v.subject.type
        assert retrieved.judgment.confidence == v.judgment.confidence

    def test_get_missing_returns_none(self, store):
        assert store.get("vrd-nonexistent") is None


class TestStoreQuery:
    def test_query_by_producer(self, store):
        v1 = _make_verdict(system="alpha")
        v2 = _make_verdict(system="beta")
        store.put(v1)
        store.put(v2)
        results = store.query(VerdictFilter(producer_system="alpha"))
        assert len(results) == 1
        assert results[0].id == v1.id

    def test_query_by_status(self, store):
        v1 = _make_verdict()
        v2 = _make_verdict()
        store.put(v1)
        store.put(v2)
        resolve(v1, status="confirmed")
        store.update_outcome(v1.id, v1.outcome)
        results = store.query(VerdictFilter(status="confirmed"))
        assert len(results) == 1

    def test_query_by_tags(self, store):
        v1 = _make_verdict(judgment={"action": "approve", "confidence": 0.8, "tags": ["auth", "security"]})
        v2 = _make_verdict(judgment={"action": "approve", "confidence": 0.8, "tags": ["database"]})
        store.put(v1)
        store.put(v2)
        results = store.query(VerdictFilter(tags=["auth"]))
        assert len(results) == 1
        assert results[0].id == v1.id

    def test_query_empty_store(self, store):
        results = store.query(VerdictFilter())
        assert results == []

    def test_query_respects_limit(self, store):
        for _ in range(10):
            store.put(_make_verdict())
        results = store.query(VerdictFilter(limit=3))
        assert len(results) == 3

    def test_query_limit_zero_returns_all(self, store):
        for _ in range(10):
            store.put(_make_verdict())
        results = store.query(VerdictFilter(limit=0))
        assert len(results) == 10

    def test_query_by_time_range(self, store):
        v = _make_verdict()
        store.put(v)
        # Should be found within a wide time range
        now = datetime.now(timezone.utc)
        results = store.query(VerdictFilter(
            from_time=now - timedelta(hours=1),
            to_time=now + timedelta(hours=1),
        ))
        assert len(results) == 1

    def test_query_by_subject_type(self, store):
        v = _make_verdict(subject={"type": "correlation", "ref": "r", "summary": "s"})
        store.put(v)
        store.put(_make_verdict())
        results = store.query(VerdictFilter(subject_type="correlation"))
        assert len(results) == 1


class TestStoreAccuracy:
    def test_accuracy_all_confirmed(self, store):
        for _ in range(5):
            v = _make_verdict(confidence=0.9)
            store.put(v)
            resolve(v, status="confirmed")
            store.update_outcome(v.id, v.outcome)
        report = store.accuracy(AccuracyFilter(producer_system="test"))
        assert report.confirmation_rate == 1.0
        assert report.override_rate == 0.0
        assert report.total == 5
        assert report.total_resolved == 5
        assert report.pending_rate == 0.0

    def test_accuracy_mixed(self, store):
        for _ in range(3):
            v = _make_verdict(confidence=0.9)
            store.put(v)
            resolve(v, status="confirmed")
            store.update_outcome(v.id, v.outcome)
        for _ in range(2):
            v = _make_verdict(confidence=0.6)
            store.put(v)
            resolve(v, status="overridden")
            store.update_outcome(v.id, v.outcome)
        report = store.accuracy(AccuracyFilter(producer_system="test"))
        assert report.total == 5
        assert report.total_resolved == 5
        assert report.confirmation_rate == 3 / 5
        assert report.override_rate == 2 / 5

    def test_accuracy_with_pending(self, store):
        v1 = _make_verdict()
        v2 = _make_verdict()
        store.put(v1)
        store.put(v2)
        resolve(v1, status="confirmed")
        store.update_outcome(v1.id, v1.outcome)
        report = store.accuracy(AccuracyFilter(producer_system="test"))
        assert report.total == 2
        assert report.total_resolved == 1
        assert report.pending_rate == 0.5

    def test_accuracy_empty_store(self, store):
        report = store.accuracy(AccuracyFilter(producer_system="test"))
        assert report.total == 0
        assert report.confirmation_rate == 0.0
        assert report.override_rate == 0.0

    def test_accuracy_not_truncated_by_limit(self, store):
        """Accuracy must consider ALL matching verdicts, not just the first 100."""
        for _ in range(150):
            v = _make_verdict(confidence=0.9)
            store.put(v)
            resolve(v, status="confirmed")
            store.update_outcome(v.id, v.outcome)
        report = store.accuracy(AccuracyFilter(producer_system="test"))
        assert report.total == 150
        assert report.total_resolved == 150
        assert report.confirmation_rate == 1.0

    def test_accuracy_consistent_denominators(self, store):
        """confirmation_rate + override_rate + partial_rate should equal 1.0 when all resolved."""
        for _ in range(3):
            v = _make_verdict()
            store.put(v)
            resolve(v, status="confirmed")
            store.update_outcome(v.id, v.outcome)
        for _ in range(2):
            v = _make_verdict()
            store.put(v)
            resolve(v, status="overridden")
            store.update_outcome(v.id, v.outcome)
        for _ in range(1):
            v = _make_verdict()
            store.put(v)
            resolve(v, status="partial")
            store.update_outcome(v.id, v.outcome)
        report = store.accuracy(AccuracyFilter(producer_system="test"))
        total_rate = report.confirmation_rate + report.override_rate + report.partial_rate
        assert abs(total_rate - 1.0) < 1e-9


class TestStoreExpire:
    def test_expire_respects_ttl(self, store):
        v = _make_verdict(metadata={"ttl": 1})  # 1 second TTL
        # Backdate the timestamp
        v.timestamp = datetime.now(timezone.utc) - timedelta(seconds=10)
        store.put(v)
        count = store.expire()
        assert count == 1
        assert store.get(v.id).outcome.status == "expired"
        assert store.get(v.id).outcome.closed_at is not None

    def test_expire_does_not_expire_fresh_verdicts(self, store):
        v = _make_verdict()  # default 90-day TTL
        store.put(v)
        count = store.expire()
        assert count == 0
        assert store.get(v.id).outcome.status == "pending"

    def test_expire_skips_resolved_verdicts(self, store):
        v = _make_verdict(metadata={"ttl": 1})
        v.timestamp = datetime.now(timezone.utc) - timedelta(seconds=10)
        store.put(v)
        resolve(v, status="confirmed")
        store.update_outcome(v.id, v.outcome)
        count = store.expire()
        assert count == 0
        assert store.get(v.id).outcome.status == "confirmed"


class TestStoreLineage:
    def test_by_lineage_down(self, store):
        parent = _make_verdict()
        child1 = _make_verdict()
        child2 = _make_verdict()
        child1.lineage.parent = parent.id
        child2.lineage.parent = parent.id
        parent.lineage.children = [child1.id, child2.id]
        store.put(parent)
        store.put(child1)
        store.put(child2)
        results = store.by_lineage(parent.id, direction="down")
        assert len(results) == 2
        result_ids = {v.id for v in results}
        assert child1.id in result_ids
        assert child2.id in result_ids

    def test_by_lineage_up(self, store):
        parent = _make_verdict()
        child = _make_verdict()
        child.lineage.parent = parent.id
        parent.lineage.children = [child.id]
        store.put(parent)
        store.put(child)
        results = store.by_lineage(child.id, direction="up")
        assert len(results) == 1
        assert results[0].id == parent.id

    def test_by_lineage_nonexistent_returns_empty(self, store):
        results = store.by_lineage("vrd-nonexistent")
        assert results == []

    def test_by_lineage_invalid_direction_raises(self, store):
        v = _make_verdict()
        store.put(v)
        with pytest.raises(ValueError, match="direction must be"):
            store.by_lineage(v.id, direction="sideways")


class TestStoreUpdateOutcome:
    def test_update_outcome(self, store):
        from verdict.models import Outcome
        v = _make_verdict()
        store.put(v)
        new_outcome = Outcome(status="confirmed", resolution="All good")
        updated = store.update_outcome(v.id, new_outcome)
        assert updated.outcome.status == "confirmed"
        assert updated.outcome.resolution == "All good"

    def test_update_outcome_missing_raises(self, store):
        from verdict.models import Outcome
        with pytest.raises(KeyError, match="not found"):
            store.update_outcome("vrd-nonexistent", Outcome())


class TestStoreCommunicationSubjectType:
    def test_communication_subject_type(self, store):
        v = create(
            subject={"type": "communication", "ref": "msg:123", "summary": "Incident update"},
            judgment={"action": "approve", "confidence": 0.9},
            producer={"system": "test"},
        )
        store.put(v)
        retrieved = store.get(v.id)
        assert retrieved is not None
        assert retrieved.subject.type == "communication"


class TestStoreResolve:
    def test_resolve_confirmed(self, store):
        v = _make_verdict()
        store.put(v)
        updated = store.resolve(v.id, "confirmed")
        assert updated.outcome.status == "confirmed"
        assert updated.outcome.closed_at is not None
        # Verify persisted
        retrieved = store.get(v.id)
        assert retrieved.outcome.status == "confirmed"

    def test_resolve_with_override(self, store):
        v = _make_verdict()
        store.put(v)
        updated = store.resolve(
            v.id, "overridden",
            override={"by": "human", "reasoning": "wrong"},
        )
        assert updated.outcome.status == "overridden"
        assert updated.outcome.override.by == "human"
        assert updated.outcome.override.reasoning == "wrong"
        # Verify persisted
        retrieved = store.get(v.id)
        assert retrieved.outcome.override.by == "human"

    def test_resolve_with_ground_truth(self, store):
        v = _make_verdict()
        store.put(v)
        updated = store.resolve(
            v.id, "confirmed",
            ground_truth={"signal": "test_failure", "value": "passed"},
        )
        assert updated.outcome.ground_truth.signal == "test_failure"
        assert updated.outcome.ground_truth.value == "passed"
        # Verify persisted
        retrieved = store.get(v.id)
        assert retrieved.outcome.ground_truth.signal == "test_failure"

    def test_resolve_with_resolution(self, store):
        v = _make_verdict()
        store.put(v)
        updated = store.resolve(v.id, "confirmed", resolution="All good")
        assert updated.outcome.resolution == "All good"
        # Verify persisted
        retrieved = store.get(v.id)
        assert retrieved.outcome.resolution == "All good"

    def test_resolve_missing_raises(self, store):
        with pytest.raises(KeyError, match="not found"):
            store.resolve("vrd-nonexistent", "confirmed")

    def test_resolve_already_resolved_raises(self, store):
        v = _make_verdict()
        store.put(v)
        store.resolve(v.id, "confirmed")
        with pytest.raises(ValueError, match="expected 'pending'"):
            store.resolve(v.id, "overridden")

    def test_resolve_invalid_status_raises(self, store):
        v = _make_verdict()
        store.put(v)
        with pytest.raises(ValueError, match="Invalid status"):
            store.resolve(v.id, "bogus")

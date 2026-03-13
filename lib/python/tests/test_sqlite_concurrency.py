"""Concurrent access tests for SQLiteVerdictStore."""

import threading
from datetime import datetime, timedelta, timezone

import pytest

from verdict.core import create, resolve
from verdict.models import Outcome
from verdict.sqlite_store import SQLiteVerdictStore
from verdict.store import AccuracyFilter, VerdictFilter


def _make_verdict(system="test", confidence=0.8, **overrides):
    kwargs = dict(
        subject={"type": "review", "ref": "git:abc123", "summary": "Test"},
        judgment={"action": "approve", "confidence": confidence},
        producer={"system": system},
    )
    kwargs.update(overrides)
    return create(**kwargs)


class TestSQLiteConcurrency:
    def test_concurrent_puts(self, tmp_path):
        """10 threads each put 50 verdicts simultaneously."""
        store = SQLiteVerdictStore(tmp_path / "test.db")
        barrier = threading.Barrier(10)
        errors: list[Exception] = []

        def put_batch(thread_id):
            try:
                barrier.wait(timeout=5)
                for _ in range(50):
                    store.put(_make_verdict(system=f"thread-{thread_id}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=put_batch, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors
        results = store.query(VerdictFilter(limit=0))
        assert len(results) == 500

    def test_concurrent_reads_during_writes(self, tmp_path):
        """Writer thread puts verdicts while 5 reader threads query."""
        store = SQLiteVerdictStore(tmp_path / "test.db")
        for _ in range(10):
            store.put(_make_verdict())

        barrier = threading.Barrier(6)
        errors: list[Exception] = []
        stop = threading.Event()

        def writer():
            try:
                barrier.wait(timeout=5)
                for _ in range(100):
                    store.put(_make_verdict())
                stop.set()
            except Exception as e:
                errors.append(e)
                stop.set()

        def reader():
            try:
                barrier.wait(timeout=5)
                while not stop.is_set():
                    results = store.query(VerdictFilter(limit=10))
                    assert all(r.id is not None for r in results)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer)]
        threads += [threading.Thread(target=reader) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors

    def test_concurrent_accuracy_during_resolves(self, tmp_path):
        """One thread resolves verdicts while another queries accuracy."""
        store = SQLiteVerdictStore(tmp_path / "test.db")
        verdicts = []
        for _ in range(50):
            v = _make_verdict()
            store.put(v)
            verdicts.append(v)

        barrier = threading.Barrier(2)
        errors: list[Exception] = []

        def resolver():
            try:
                barrier.wait(timeout=5)
                for v in verdicts:
                    resolve(v, status="confirmed")
                    store.update_outcome(v.id, v.outcome)
            except Exception as e:
                errors.append(e)

        def accuracy_checker():
            try:
                barrier.wait(timeout=5)
                for _ in range(20):
                    report = store.accuracy(AccuracyFilter(producer_system="test"))
                    assert report.total_resolved <= report.total
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=resolver)
        t2 = threading.Thread(target=accuracy_checker)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert not errors

    def test_concurrent_update_outcome(self, tmp_path):
        """5 threads each update different verdicts' outcomes simultaneously."""
        store = SQLiteVerdictStore(tmp_path / "test.db")
        batches: list[list] = []
        for _ in range(5):
            batch = []
            for _ in range(20):
                v = _make_verdict()
                store.put(v)
                batch.append(v)
            batches.append(batch)

        barrier = threading.Barrier(5)
        errors: list[Exception] = []

        def updater(batch):
            try:
                barrier.wait(timeout=5)
                for v in batch:
                    outcome = Outcome(status="confirmed", resolution="ok")
                    store.update_outcome(v.id, outcome)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=updater, args=(b,)) for b in batches]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors
        for batch in batches:
            for v in batch:
                retrieved = store.get(v.id)
                assert retrieved.outcome.status == "confirmed"

    def test_concurrent_expire(self, tmp_path):
        """Expire runs while puts and queries happen concurrently."""
        store = SQLiteVerdictStore(tmp_path / "test.db")
        for _ in range(20):
            v = _make_verdict(metadata={"ttl": 1})
            v.timestamp = datetime.now(timezone.utc) - timedelta(seconds=10)
            store.put(v)

        barrier = threading.Barrier(3)
        errors: list[Exception] = []

        def expirer():
            try:
                barrier.wait(timeout=5)
                store.expire()
            except Exception as e:
                errors.append(e)

        def putter():
            try:
                barrier.wait(timeout=5)
                for _ in range(20):
                    store.put(_make_verdict())
            except Exception as e:
                errors.append(e)

        def querier():
            try:
                barrier.wait(timeout=5)
                for _ in range(10):
                    store.query(VerdictFilter(limit=10))
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=expirer),
            threading.Thread(target=putter),
            threading.Thread(target=querier),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors

    def test_concurrent_lineage_traversal(self, tmp_path):
        """Traverse lineage while new verdicts are being added to the chain."""
        store = SQLiteVerdictStore(tmp_path / "test.db")
        root = _make_verdict()
        children = []
        for _ in range(5):
            child = _make_verdict()
            child.lineage.parent = root.id
            children.append(child)
        root.lineage.children = [c.id for c in children]
        store.put(root)
        for c in children:
            store.put(c)

        barrier = threading.Barrier(2)
        errors: list[Exception] = []

        def traverser():
            try:
                barrier.wait(timeout=5)
                for _ in range(20):
                    results = store.by_lineage(root.id, direction="down")
                    assert len(results) == 5
            except Exception as e:
                errors.append(e)

        def adder():
            try:
                barrier.wait(timeout=5)
                for _ in range(20):
                    store.put(_make_verdict())
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=traverser)
        t2 = threading.Thread(target=adder)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert not errors

    def test_wal_mode_enabled(self, tmp_path):
        """Verify PRAGMA journal_mode returns wal after construction."""
        store = SQLiteVerdictStore(tmp_path / "test.db")
        row = store._conn().execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal"

"""Tests for Evaluation record creation in the retrospective flow."""

from nthlayer_common.records.hashing import verify_hash
from nthlayer_common.records.models import (
    EvaluationMethod,
    EvaluationOutcome,
)
from nthlayer_common.records.sqlite_store import SQLiteDecisionRecordStore
from nthlayer_learn import MemoryStore, create, link
from nthlayer_learn.retrospective import build_retrospective


def _build_chain(store):
    """Build a realistic verdict chain: evaluation → correlation → triage."""
    eval_v = create(
        subject={"type": "evaluation", "ref": "fraud-detect",
                 "summary": "reversal_rate BREACH: 0.08 (target 0.015)"},
        judgment={"action": "flag", "confidence": 0.85},
        producer={"system": "nthlayer-measure"},
        metadata={"custom": {
            "slo_type": "judgment", "slo_name": "reversal_rate",
            "target": 0.015, "current_value": 0.08, "breach": True, "consecutive": 3,
        }},
    )
    store.put(eval_v)

    corr_v = create(
        subject={"type": "correlation", "ref": "fraud-detect",
                 "summary": "fraud-detect model_regression — 3 services affected"},
        judgment={"action": "escalate", "confidence": 0.82},
        producer={"system": "nthlayer-correlate"},
        metadata={"custom": {
            "trigger_verdict": eval_v.id,
            "root_causes": [{"service": "fraud-detect", "type": "model_regression", "confidence": 0.82}],
            "blast_radius": [{"service": "fraud-detect"}, {"service": "payment-api"}, {"service": "checkout-svc"}],
        }},
    )
    link(corr_v, context=[eval_v.id])
    store.put(corr_v)

    incident_v = create(
        subject={"type": "triage", "ref": "fraud-detect",
                 "summary": "SEV-1: fraud-detect model regression"},
        judgment={"action": "flag", "confidence": 0.9},
        producer={"system": "nthlayer-respond"},
        metadata={"custom": {
            "incident_id": "INC-FRAUD-20260411",
            "severity": 1,
            "blast_radius": ["fraud-detect", "payment-api", "checkout-svc"],
            "root_causes": [{"service": "fraud-detect", "type": "model_regression"}],
        }},
    )
    link(incident_v, context=[corr_v.id])
    store.put(incident_v)

    return eval_v, corr_v, incident_v


class TestEvaluationRecordCreation:
    def test_retrospective_writes_evaluation_record(self, tmp_path):
        db = str(tmp_path / "decisions.db")
        verdict_store = MemoryStore()
        decision_store = SQLiteDecisionRecordStore(db)

        eval_v, corr_v, incident_v = _build_chain(verdict_store)

        retro = build_retrospective(
            incident_v.id, verdict_store, decision_store=decision_store,
        )
        assert retro.subject.type == "retrospective"

        # Should have written an Evaluation record
        chain = decision_store.get_chain("evaluation", "INC-FRAUD-20260411")
        assert len(chain) >= 1
        ev = chain[0]
        assert ev.schema_version == "evaluation/v1"
        assert ev.method == EvaluationMethod.METRIC_RECOVERY
        # decisions_affected=1 and duration < 60m → EFFECTIVE
        assert ev.outcome == EvaluationOutcome.EFFECTIVE
        assert ev.incident_id == "INC-FRAUD-20260411"

    def test_evaluation_hash_is_valid(self, tmp_path):
        db = str(tmp_path / "decisions.db")
        verdict_store = MemoryStore()
        decision_store = SQLiteDecisionRecordStore(db)

        eval_v, corr_v, incident_v = _build_chain(verdict_store)
        build_retrospective(incident_v.id, verdict_store, decision_store=decision_store)

        chain = decision_store.get_chain("evaluation", "INC-FRAUD-20260411")
        assert len(chain) >= 1
        assert verify_hash(chain[0]) is True

    def test_no_evaluation_when_no_decision_store(self, tmp_path):
        verdict_store = MemoryStore()
        eval_v, corr_v, incident_v = _build_chain(verdict_store)

        # Should not raise — decision_store defaults to None
        retro = build_retrospective(incident_v.id, verdict_store)
        assert retro.subject.type == "retrospective"

    def test_evaluation_references_incident_id(self, tmp_path):
        db = str(tmp_path / "decisions.db")
        verdict_store = MemoryStore()
        decision_store = SQLiteDecisionRecordStore(db)

        eval_v, corr_v, incident_v = _build_chain(verdict_store)
        build_retrospective(incident_v.id, verdict_store, decision_store=decision_store)

        chain = decision_store.get_chain("evaluation", "INC-FRAUD-20260411")
        ev = chain[0]
        assert ev.incident_id == "INC-FRAUD-20260411"

    def test_evaluation_payload_has_duration_and_decisions(self, tmp_path):
        db = str(tmp_path / "decisions.db")
        verdict_store = MemoryStore()
        decision_store = SQLiteDecisionRecordStore(db)

        eval_v, corr_v, incident_v = _build_chain(verdict_store)
        build_retrospective(incident_v.id, verdict_store, decision_store=decision_store)

        chain = decision_store.get_chain("evaluation", "INC-FRAUD-20260411")
        ev = chain[0]
        assert "duration_minutes" in ev.payload
        assert "decisions_affected" in ev.payload
        assert "verdict_count" in ev.payload

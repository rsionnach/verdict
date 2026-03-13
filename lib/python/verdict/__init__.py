"""Verdict — The Atomic Unit of AI Judgment.

A transport library for creating, linking, resolving, and querying verdicts.
No model calls. No judgment. Just structured decision records.
"""

from verdict.core import create, link, resolve, supersede
from verdict.models import AccuracyReport, Verdict
from verdict.serialise import from_dict, from_json, to_dict, to_json
from verdict.sqlite_store import SQLiteVerdictStore
from verdict.store import AccuracyFilter, MemoryStore, VerdictFilter, VerdictStore

__all__ = [
    "create",
    "link",
    "resolve",
    "supersede",
    "Verdict",
    "AccuracyReport",
    "VerdictStore",
    "MemoryStore",
    "SQLiteVerdictStore",
    "VerdictFilter",
    "AccuracyFilter",
    "to_dict",
    "to_json",
    "from_dict",
    "from_json",
]

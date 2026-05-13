"""Phase B: decisions_io dual-write to SQLite + read-side cutover."""

import json
from pathlib import Path

import pytest

from trading_agent_skills.decisions_io import (
    DecisionSchemaError,
    _canonical_payload,
    _dedup_key,
    _normalize,
)


def test_normalize_keeps_ts_when_present() -> None:
    rec = {"ts": "2026-05-11T00:00:00Z", "type": "stage1"}
    out = _normalize(rec)
    assert out["ts"] == "2026-05-11T00:00:00Z"
    assert out["record_type"] == "stage1"


def test_normalize_coalesces_timestamp_into_ts() -> None:
    rec = {"timestamp": "2026-05-11T00:00:00Z", "type": "stage2-complete"}
    out = _normalize(rec)
    assert out["ts"] == "2026-05-11T00:00:00Z"
    # original `timestamp` is preserved (round-trippable via payload).
    assert out["timestamp"] == "2026-05-11T00:00:00Z"
    assert out["record_type"] == "stage2-complete"


def test_normalize_kind_wins_over_type_for_record_type() -> None:
    # decision_log.py rows have `kind`; if a record somehow has both, kind wins
    # because that's the canonical writer.
    rec = {"ts": "2026-05-11T00:00:00Z", "kind": "open", "type": "stage2-complete"}
    out = _normalize(rec)
    assert out["record_type"] == "open"


def test_normalize_falls_back_to_type_when_no_kind() -> None:
    rec = {"ts": "2026-05-11T00:00:00Z", "type": "stage1"}
    assert _normalize(rec)["record_type"] == "stage1"


def test_normalize_record_type_is_none_when_neither_present() -> None:
    rec = {"ts": "2026-05-11T00:00:00Z", "fire": "stage1", "decision": "no-trigger"}
    assert _normalize(rec)["record_type"] is None


def test_normalize_raises_when_no_timestamp() -> None:
    rec = {"type": "stage1", "decision": "no-trigger"}
    with pytest.raises(DecisionSchemaError, match="timestamp"):
        _normalize(rec)


def test_normalize_does_not_mutate_input() -> None:
    rec = {"timestamp": "2026-05-11T00:00:00Z", "type": "stage1"}
    snapshot = json.dumps(rec, sort_keys=True)
    _normalize(rec)
    assert json.dumps(rec, sort_keys=True) == snapshot


def test_canonical_payload_is_stable_across_key_orders() -> None:
    a = {"ts": "2026-05-11T00:00:00Z", "type": "stage1", "fire": "stage1"}
    b = {"fire": "stage1", "type": "stage1", "ts": "2026-05-11T00:00:00Z"}
    assert _canonical_payload(a) == _canonical_payload(b)


def test_dedup_key_is_64char_hex() -> None:
    payload = _canonical_payload({"ts": "2026-05-11T00:00:00Z", "type": "stage1"})
    key = _dedup_key(payload)
    assert len(key) == 64
    assert all(c in "0123456789abcdef" for c in key)


def test_dedup_key_differs_for_different_records() -> None:
    a = _dedup_key(_canonical_payload({"ts": "2026-05-11T00:00:00Z", "type": "stage1"}))
    b = _dedup_key(_canonical_payload({"ts": "2026-05-11T00:00:00Z", "type": "stage2-complete"}))
    assert a != b

"""journal_io: write/read append-only JSONL with schema validation + update chains."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent_skills.journal_io import (
    SCHEMA_VERSION,
    SchemaError,
    default_journal_path,
    filter_resolved,
    find_uuid_by_ticket,
    read_raw,
    read_resolved,
    read_resolved_with_events,
    suggest_tags,
    write_close,
    write_open,
    write_partial_closed,
    write_sl_trailed,
    write_update,
)


# --- fixtures --------------------------------------------------------------


def _open_kwargs(**overrides) -> dict:
    base = dict(
        symbol="UKOIL",
        side="buy",
        volume="1.0",
        entry_price="75.42",
        exit_price="78.10",
        entry_time=datetime(2026, 4, 29, 7, 30, tzinfo=timezone.utc),
        exit_time=datetime(2026, 5, 2, 15, 45, tzinfo=timezone.utc),
        original_stop_distance_points=80,
        original_risk_amount="80.00",
        realized_pnl="268.00",
        swap_accrued="375.00",
        commission="-7.50",
        setup_type="swap-harvest-long",
        rationale="Geopolitical tension intact; oversold on D1; positive carry.",
        risk_classification_at_close="LOCKED_PROFIT",
        ticket=12345,
    )
    base.update(overrides)
    return base


# --- write_open: schema validation ----------------------------------------


def test_write_open_returns_uuid_and_appends(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    uid = write_open(journal, **_open_kwargs())
    assert isinstance(uid, str) and len(uid) >= 32
    raw = read_raw(journal)
    assert len(raw) == 1
    assert raw[0]["uuid"] == uid
    assert raw[0]["type"] == "open"
    assert raw[0]["schema_version"] == SCHEMA_VERSION


def test_write_open_creates_parent_dirs(tmp_path: Path):
    journal = tmp_path / "nested" / "deep" / "journal.jsonl"
    write_open(journal, **_open_kwargs())
    assert journal.exists()


def test_write_open_appends_not_overwrites(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    write_open(journal, **_open_kwargs())
    write_open(journal, **_open_kwargs(symbol="XAUUSD"))
    raw = read_raw(journal)
    assert len(raw) == 2
    assert {r["symbol"] for r in raw} == {"UKOIL", "XAUUSD"}


def test_write_open_rejects_invalid_side(tmp_path: Path):
    with pytest.raises(SchemaError, match="side"):
        write_open(tmp_path / "j.jsonl", **_open_kwargs(side="LONG"))


def test_write_open_rejects_invalid_risk_classification(tmp_path: Path):
    with pytest.raises(SchemaError, match="risk_classification"):
        write_open(
            tmp_path / "j.jsonl",
            **_open_kwargs(risk_classification_at_close="kinda-risky"),
        )


def test_write_open_rejects_naive_datetime(tmp_path: Path):
    with pytest.raises(SchemaError, match="timezone-aware"):
        write_open(
            tmp_path / "j.jsonl",
            **_open_kwargs(entry_time=datetime(2026, 4, 29, 7, 30)),
        )


def test_write_open_rejects_float_decimal(tmp_path: Path):
    with pytest.raises(SchemaError):
        write_open(tmp_path / "j.jsonl", **_open_kwargs(volume=1.0))


def test_write_open_rejects_zero_stop_distance(tmp_path: Path):
    # Zero stop distance breaks R-multiple math; reject early.
    with pytest.raises(SchemaError, match="original_stop_distance_points"):
        write_open(
            tmp_path / "j.jsonl",
            **_open_kwargs(original_stop_distance_points=0),
        )


def test_write_open_rejects_empty_setup_type(tmp_path: Path):
    with pytest.raises(SchemaError, match="setup_type"):
        write_open(tmp_path / "j.jsonl", **_open_kwargs(setup_type=""))


def test_write_open_rejects_empty_rationale(tmp_path: Path):
    with pytest.raises(SchemaError, match="rationale"):
        write_open(tmp_path / "j.jsonl", **_open_kwargs(rationale=""))


def test_write_open_accepts_iso_string_timestamps(tmp_path: Path):
    write_open(
        tmp_path / "j.jsonl",
        **_open_kwargs(
            entry_time="2026-04-29T07:30:00+00:00",
            exit_time="2026-05-02T15:45:00+00:00",
        ),
    )
    raw = read_raw(tmp_path / "j.jsonl")
    assert raw[0]["entry_time"].startswith("2026-04-29T07:30:00")


def test_write_open_normalises_non_utc_aware_to_utc(tmp_path: Path):
    aest = timezone(timedelta(hours=10))
    write_open(
        tmp_path / "j.jsonl",
        **_open_kwargs(
            entry_time=datetime(2026, 4, 29, 17, 30, tzinfo=aest),  # = 07:30 UTC
        ),
    )
    raw = read_raw(tmp_path / "j.jsonl")
    assert raw[0]["entry_time"] == "2026-04-29T07:30:00+00:00"


def test_write_open_persists_structured_sl_tp_run_id_paper_mode(tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    write_open(
        journal,
        **_open_kwargs(
            sl="74.50", tp="78.10", run_id="abc123", paper_mode=False,
        ),
    )
    raw = read_raw(journal)
    assert raw[0]["sl"] == "74.50"
    assert raw[0]["tp"] == "78.10"
    assert raw[0]["run_id"] == "abc123"
    assert raw[0]["paper_mode"] is False


def test_write_open_omits_structured_fields_when_unset(tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    write_open(journal, **_open_kwargs())
    raw = read_raw(journal)
    for field in ("sl", "tp", "run_id", "paper_mode"):
        assert field not in raw[0]


def test_write_open_rejects_float_sl(tmp_path: Path):
    with pytest.raises(SchemaError, match="sl"):
        write_open(tmp_path / "j.jsonl", **_open_kwargs(sl=74.5))


def test_write_open_rejects_empty_run_id(tmp_path: Path):
    with pytest.raises(SchemaError, match="run_id"):
        write_open(tmp_path / "j.jsonl", **_open_kwargs(run_id="   "))


def test_write_open_rejects_non_bool_paper_mode(tmp_path: Path):
    with pytest.raises(SchemaError, match="paper_mode"):
        write_open(tmp_path / "j.jsonl", **_open_kwargs(paper_mode="false"))


# --- write_update ---------------------------------------------------------


def test_write_update_appends_patch(tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    uid = write_open(journal, **_open_kwargs())
    write_update(
        journal,
        uuid=uid,
        outcome_notes="Held longer than planned; market kept trending.",
    )
    raw = read_raw(journal)
    assert len(raw) == 2
    assert raw[1]["type"] == "update"
    assert raw[1]["uuid"] == uid
    assert raw[1]["outcome_notes"] == "Held longer than planned; market kept trending."


def test_write_update_requires_at_least_one_field(tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    uid = write_open(journal, **_open_kwargs())
    with pytest.raises(SchemaError, match="at least one"):
        write_update(journal, uuid=uid)


def test_write_update_validates_risk_classification(tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    uid = write_open(journal, **_open_kwargs())
    with pytest.raises(SchemaError, match="risk_classification"):
        write_update(journal, uuid=uid, risk_classification_at_close="oops")


# --- read_resolved -------------------------------------------------------


def test_read_resolved_applies_update(tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    uid = write_open(journal, **_open_kwargs(outcome_notes=None))
    write_update(journal, uuid=uid, outcome_notes="Add later.")
    resolved = read_resolved(journal)
    assert len(resolved) == 1
    assert resolved[0]["outcome_notes"] == "Add later."


def test_read_resolved_applies_multiple_updates_in_order(tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    uid = write_open(journal, **_open_kwargs())
    write_update(journal, uuid=uid, outcome_notes="First note.")
    write_update(journal, uuid=uid, outcome_notes="Second note (replaces first).")
    write_update(journal, uuid=uid, setup_type="renamed-setup")
    resolved = read_resolved(journal)
    assert len(resolved) == 1
    assert resolved[0]["outcome_notes"] == "Second note (replaces first)."
    assert resolved[0]["setup_type"] == "renamed-setup"


def test_read_resolved_drops_orphan_updates(tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    write_update(journal, uuid="missing-uuid", outcome_notes="dangling")
    resolved = read_resolved(journal)
    assert resolved == []


def test_read_resolved_orders_by_entry_time(tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    write_open(
        journal,
        **_open_kwargs(
            symbol="LATER",
            entry_time=datetime(2026, 5, 1, tzinfo=timezone.utc),
            exit_time=datetime(2026, 5, 2, tzinfo=timezone.utc),
        ),
    )
    write_open(
        journal,
        **_open_kwargs(
            symbol="EARLIER",
            entry_time=datetime(2026, 4, 1, tzinfo=timezone.utc),
            exit_time=datetime(2026, 4, 2, tzinfo=timezone.utc),
        ),
    )
    resolved = read_resolved(journal)
    assert [e["symbol"] for e in resolved] == ["EARLIER", "LATER"]


def test_read_resolved_returns_empty_for_missing_file(tmp_path: Path):
    assert read_resolved(tmp_path / "nope.jsonl") == []


def test_read_resolved_rejects_unsupported_schema_version(tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    journal.write_text('{"schema_version": 99, "uuid": "x", "type": "open"}\n')
    with pytest.raises(SchemaError, match="schema_version"):
        read_resolved(journal)


def test_read_resolved_rejects_invalid_json(tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    journal.write_text("{not json\n")
    with pytest.raises(SchemaError, match="invalid JSON"):
        read_resolved(journal)


def test_read_resolved_tolerates_blank_lines(tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    write_open(journal, **_open_kwargs())
    # Append a blank line manually.
    with open(journal, "a") as f:
        f.write("\n")
    assert len(read_resolved(journal)) == 1


# --- filter_resolved -----------------------------------------------------


def test_filter_by_period(tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    write_open(
        journal,
        **_open_kwargs(
            symbol="OLD",
            entry_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            exit_time=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ),
    )
    write_open(
        journal,
        **_open_kwargs(
            symbol="NEW",
            entry_time=datetime(2026, 4, 28, tzinfo=timezone.utc),
            exit_time=datetime(2026, 4, 29, tzinfo=timezone.utc),
        ),
    )
    resolved = read_resolved(journal)
    cutoff = datetime(2026, 4, 1, tzinfo=timezone.utc)
    filtered = filter_resolved(resolved, since=cutoff)
    assert [e["symbol"] for e in filtered] == ["NEW"]


def test_filter_by_symbol_and_side(tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    write_open(journal, **_open_kwargs(symbol="UKOIL", side="buy"))
    write_open(journal, **_open_kwargs(symbol="XAUUSD", side="buy"))
    write_open(journal, **_open_kwargs(symbol="UKOIL", side="sell"))
    resolved = read_resolved(journal)
    out = filter_resolved(resolved, symbol="UKOIL", side="buy")
    assert len(out) == 1


def test_filter_by_setup_type_and_risk_classification(tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    write_open(journal, **_open_kwargs(setup_type="pullback-long",
                                       risk_classification_at_close="AT_RISK"))
    write_open(journal, **_open_kwargs(setup_type="pullback-long",
                                       risk_classification_at_close="LOCKED_PROFIT"))
    write_open(journal, **_open_kwargs(setup_type="breakout-long",
                                       risk_classification_at_close="LOCKED_PROFIT"))
    resolved = read_resolved(journal)
    assert len(filter_resolved(resolved, setup_type="pullback-long")) == 2
    assert len(filter_resolved(resolved, risk_classification="LOCKED_PROFIT")) == 2


# --- suggest_tags ---------------------------------------------------------


def test_suggest_tags_orders_by_frequency(tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    write_open(journal, **_open_kwargs(setup_type="pullback-long"))
    write_open(journal, **_open_kwargs(setup_type="pullback-long"))
    write_open(journal, **_open_kwargs(setup_type="breakout-long"))
    write_open(journal, **_open_kwargs(setup_type="swap-harvest-long"))
    write_open(journal, **_open_kwargs(setup_type="swap-harvest-long"))
    write_open(journal, **_open_kwargs(setup_type="swap-harvest-long"))
    tags = suggest_tags(journal)
    assert tags[0] == ("swap-harvest-long", 3)
    assert tags[1] == ("pullback-long", 2)
    assert tags[2] == ("breakout-long", 1)


def test_suggest_tags_empty_for_missing_journal(tmp_path: Path):
    assert suggest_tags(tmp_path / "nope.jsonl") == []


# --- default_journal_path -----------------------------------------------


def test_default_path_with_account_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    path = default_journal_path(account_id="12345678")
    expected = tmp_path / ".trading-agent-skills" / "accounts" / "12345678" / "journal.jsonl"
    assert path == expected


def test_default_path_without_account_id_is_legacy(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    path = default_journal_path(account_id=None)
    expected = tmp_path / ".trading-agent-skills" / "journal.jsonl"
    assert path == expected


# --- Stage 3 events --------------------------------------------------------


def test_write_sl_trailed_appends_event(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    uid = write_open(journal, **_open_kwargs())
    write_sl_trailed(
        journal,
        uuid=uid,
        old_sl="74.62",
        new_sl="75.42",
        reason="1R reached, lock breakeven",
        old_tp="78.00",
        new_tp="78.00",
        paper_mode=False,
    )
    raw = read_raw(journal)
    assert len(raw) == 2
    ev = raw[1]
    assert ev["type"] == "sl-trailed"
    assert ev["uuid"] == uid
    assert ev["old_sl"] == "74.62"
    assert ev["new_sl"] == "75.42"
    assert ev["reason"] == "1R reached, lock breakeven"
    assert ev["paper_mode"] is False
    assert "ts" in ev


def test_write_sl_trailed_optional_tp_omitted(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    uid = write_open(journal, **_open_kwargs())
    write_sl_trailed(journal, uuid=uid, old_sl="74.62", new_sl="75.42", reason="trail")
    raw = read_raw(journal)
    assert raw[1]["old_tp"] is None
    assert raw[1]["new_tp"] is None


def test_write_sl_trailed_rejects_empty_reason(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    uid = write_open(journal, **_open_kwargs())
    with pytest.raises(SchemaError, match="reason"):
        write_sl_trailed(journal, uuid=uid, old_sl="74.62", new_sl="75.42", reason="")


def test_write_partial_closed_appends_event(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    uid = write_open(journal, **_open_kwargs())
    write_partial_closed(
        journal,
        uuid=uid,
        closed_lots="0.5",
        remaining_lots="0.5",
        realized_pnl="134.00",
        reason="2R, take half",
        paper_mode=True,
    )
    raw = read_raw(journal)
    assert raw[1]["type"] == "partial-closed"
    assert raw[1]["closed_lots"] == "0.5"
    assert raw[1]["remaining_lots"] == "0.5"
    assert raw[1]["realized_pnl"] == "134.00"
    assert raw[1]["paper_mode"] is True


def test_write_partial_closed_rejects_empty_reason(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    uid = write_open(journal, **_open_kwargs())
    with pytest.raises(SchemaError, match="reason"):
        write_partial_closed(
            journal, uuid=uid, closed_lots="0.5", remaining_lots="0.5",
            realized_pnl="134.00", reason="",
        )


def test_write_close_invalidation(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    uid = write_open(journal, **_open_kwargs())
    write_close(
        journal,
        uuid=uid,
        exit_price="76.20",
        realized_pnl="78.00",
        close_kind="invalidation",
        reason="H4 lower-low printed; thesis broken",
    )
    raw = read_raw(journal)
    assert raw[1]["type"] == "closed"
    assert raw[1]["close_kind"] == "invalidation"
    assert raw[1]["exit_price"] == "76.20"


def test_write_close_manual(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    uid = write_open(journal, **_open_kwargs())
    write_close(
        journal, uuid=uid, exit_price="76.20", realized_pnl="78.00",
        close_kind="manual", reason="discretionary"
    )
    assert read_raw(journal)[1]["close_kind"] == "manual"


def test_write_close_rejects_invalid_kind(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    uid = write_open(journal, **_open_kwargs())
    with pytest.raises(SchemaError, match="close_kind"):
        write_close(
            journal, uuid=uid, exit_price="76.20", realized_pnl="78.00",
            close_kind="garbage", reason="x",
        )


def test_read_resolved_ignores_stage3_events(tmp_path: Path):
    """Existing read_resolved consumers don't see Stage 3 events — only opens+updates."""
    journal = tmp_path / "journal.jsonl"
    uid = write_open(journal, **_open_kwargs())
    write_sl_trailed(journal, uuid=uid, old_sl="74.62", new_sl="75.42", reason="trail")
    write_partial_closed(
        journal, uuid=uid, closed_lots="0.5", remaining_lots="0.5",
        realized_pnl="134.00", reason="half",
    )
    resolved = read_resolved(journal)
    assert len(resolved) == 1
    assert resolved[0]["uuid"] == uid
    # Stage 3 fields don't leak into the resolved open
    assert "old_sl" not in resolved[0]
    assert "closed_lots" not in resolved[0]


def test_read_resolved_with_events_attaches_chronologically(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    uid = write_open(journal, **_open_kwargs())
    write_sl_trailed(journal, uuid=uid, old_sl="74.62", new_sl="75.42", reason="be")
    write_partial_closed(
        journal, uuid=uid, closed_lots="0.5", remaining_lots="0.5",
        realized_pnl="134.00", reason="half",
    )
    write_close(
        journal, uuid=uid, exit_price="76.20", realized_pnl="78.00",
        close_kind="invalidation", reason="thesis broken",
    )
    resolved = read_resolved_with_events(journal)
    assert len(resolved) == 1
    events = resolved[0]["_events"]
    assert [e["type"] for e in events] == ["sl-trailed", "partial-closed", "closed"]


def test_read_resolved_with_events_empty_for_no_stage3_events(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    write_open(journal, **_open_kwargs())
    resolved = read_resolved_with_events(journal)
    assert resolved[0]["_events"] == []


def test_find_uuid_by_ticket_returns_uuid(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    uid_a = write_open(journal, **_open_kwargs(ticket=100))
    write_open(journal, **_open_kwargs(ticket=200, symbol="XAUUSD"))
    assert find_uuid_by_ticket(journal, 100) == uid_a


def test_find_uuid_by_ticket_returns_latest_when_reused(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    write_open(journal, **_open_kwargs(ticket=100))
    uid_b = write_open(journal, **_open_kwargs(ticket=100, symbol="XAUUSD"))
    assert find_uuid_by_ticket(journal, 100) == uid_b


def test_find_uuid_by_ticket_returns_none_when_missing(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    write_open(journal, **_open_kwargs(ticket=100))
    assert find_uuid_by_ticket(journal, 999) is None

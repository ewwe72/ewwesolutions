"""Alert rule for the Discord webhook wrapper.

The wrapper itself ``scripts.run_live`` orchestrates subprocess + file I/O +
HTTP, so it's awkward to integration-test cleanly. The interesting logic is
the pure function ``decide_alert(RunSummary) -> AlertDecision | None`` plus
the small parsing helpers. Those are what these tests cover.

What is intentionally NOT tested:
  * Actual Discord HTTP POST — exercised by manual invocation. The payload
    shape ``build_discord_payload`` produces IS tested below; the network
    leg is best-effort by design (never raises).
  * Subprocess invocation of ``src.live`` — exercised by ``test_live.py``
    against the real CLI.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from scripts.run_live import (
    AlertDecision,
    RunSummary,
    _build_summary,
    _read_recent_log_events,
    _read_state_snapshot,
    build_discord_payload,
    decide_alert,
)


# --------------------------------------------------------------------------- #
# decide_alert — error branches                                               #
# --------------------------------------------------------------------------- #

def test_exit_2_reconciliation_alerts_red_with_divergences() -> None:
    decision = decide_alert(RunSummary(
        exit_code=2,
        run_date=date(2025, 6, 3),
        mode="paper",
        reconciliation_divergences=(
            "  AAPL: persisted=10, broker=12",
            "  MSFT: persisted=5, broker=0",
        ),
    ))
    assert decision is not None
    assert "RECONCILIATION" in decision.title
    assert decision.color == 0xE74C3C
    # Divergence detail must be in the payload — that's what makes the alert
    # actionable rather than just an event notification.
    field_text = "\n".join(v for _, v in decision.fields)
    assert "AAPL" in field_text
    assert "MSFT" in field_text


def test_exit_3_feed_unhealthy_alerts_red_with_reasons_and_fresh_pct() -> None:
    decision = decide_alert(RunSummary(
        exit_code=3,
        run_date=date(2025, 6, 3),
        mode="paper",
        feed_health_reasons=("fresh_pct 42.00% < min 85.00% (210/500 fresh; cutoff 2025-05-30)",),
        feed_fresh_pct=0.42,
    ))
    assert decision is not None
    assert "FEED" in decision.title.upper()
    assert decision.color == 0xE74C3C
    field_text = "\n".join(v for _, v in decision.fields)
    assert "fresh_pct" in field_text
    assert "42.0%" in field_text or "42%" in field_text


def test_exit_4_misuse_alert_explains_no_state_change() -> None:
    decision = decide_alert(RunSummary(
        exit_code=4, run_date=date(2025, 6, 3), mode="paper",
    ))
    assert decision is not None
    assert decision.color == 0xE74C3C
    assert "NOT modified" in decision.description


def test_unexpected_nonzero_exit_alerts_red_with_last_errors() -> None:
    decision = decide_alert(RunSummary(
        exit_code=99,
        run_date=date(2025, 6, 3),
        mode="paper",
        error_messages=("ConnectionError: Alpaca unreachable", "RetryError: 3/3 attempts failed"),
    ))
    assert decision is not None
    assert "exit 99" in decision.title
    field_text = "\n".join(v for _, v in decision.fields)
    assert "Alpaca unreachable" in field_text


# --------------------------------------------------------------------------- #
# decide_alert — exit-0 transitions                                           #
# --------------------------------------------------------------------------- #

def test_halt_activated_alerts_orange() -> None:
    decision = decide_alert(RunSummary(
        exit_code=0, run_date=date(2025, 6, 3), mode="paper",
        halt_before=False, halt_after=True,
    ))
    assert decision is not None
    assert decision.color == 0xE67E22
    assert "HALT" in decision.title.upper()


def test_halt_resumed_alerts_blue() -> None:
    decision = decide_alert(RunSummary(
        exit_code=0, run_date=date(2025, 6, 3), mode="paper",
        halt_before=True, halt_after=False,
    ))
    assert decision is not None
    assert decision.color == 0x3498DB
    assert "resumed" in decision.title.lower()


def test_rebalance_executed_alerts_green_with_order_counts() -> None:
    decision = decide_alert(RunSummary(
        exit_code=0, run_date=date(2025, 6, 3), mode="paper",
        rebalanced_today=True,
        rebalance_n_buys=18, rebalance_n_sells=15, rebalance_n_targets=50,
    ))
    assert decision is not None
    assert decision.color == 0x2ECC71
    field_dict = dict(decision.fields)
    assert field_dict["Buys"] == "18"
    assert field_dict["Sells"] == "15"
    assert field_dict["Targets"] == "50"


def test_halt_activation_outranks_rebalance_executed() -> None:
    """If both happen in the same run, the halt activation is the louder
    signal — alert on that, not the rebalance. (In practice this is rare:
    halt activates daily on DD sampling, and the rebalance happens before
    the post-rebalance equity is sampled.)"""
    decision = decide_alert(RunSummary(
        exit_code=0, run_date=date(2025, 6, 3), mode="paper",
        halt_before=False, halt_after=True,
        rebalanced_today=True,
        rebalance_n_buys=10, rebalance_n_sells=10, rebalance_n_targets=50,
    ))
    assert decision is not None
    assert "HALT" in decision.title.upper()


# --------------------------------------------------------------------------- #
# decide_alert — silent branches                                              #
# --------------------------------------------------------------------------- #

def test_quiet_no_op_day_returns_none() -> None:
    """Exit 0, no state transition, no rebalance — the channel stays quiet."""
    decision = decide_alert(RunSummary(
        exit_code=0, run_date=date(2025, 6, 3), mode="paper",
    ))
    assert decision is None


def test_reset_invocation_returns_none_even_on_success() -> None:
    """Operator-driven recovery is silent; the operator is at their terminal."""
    decision = decide_alert(RunSummary(
        exit_code=0, run_date=date(2025, 6, 3), mode="paper",
        is_reset_invocation=True,
        # Reset rewrites holdings, which could otherwise look like a rebalance.
        rebalanced_today=False,
    ))
    assert decision is None


def test_reset_invocation_returns_none_even_on_misuse() -> None:
    """Even exit 4 from misuse of --reset is silent — the operator typed
    the flag and can see the stderr message themselves."""
    decision = decide_alert(RunSummary(
        exit_code=4, run_date=date(2025, 6, 3), mode="paper",
        is_reset_invocation=True,
    ))
    assert decision is None


# --------------------------------------------------------------------------- #
# Log parsing                                                                 #
# --------------------------------------------------------------------------- #

def test_read_log_events_filters_by_date_and_skips_bad_lines(tmp_path: Path) -> None:
    log = tmp_path / "momentum-bot.log"
    log.write_text(
        "\n".join([
            json.dumps({"timestamp": "2025-06-02 09:30:00,000", "event_type": "yesterday"}),
            json.dumps({"timestamp": "2025-06-03 09:30:00,000", "event_type": "today_1"}),
            "this is not json",
            json.dumps({"timestamp": "2025-06-03 14:00:00,000", "event_type": "today_2"}),
            json.dumps({"timestamp": "2025-06-04 09:30:00,000", "event_type": "tomorrow"}),
            "",
        ]),
        encoding="utf-8",
    )
    events = _read_recent_log_events(log, date(2025, 6, 3))
    assert [e["event_type"] for e in events] == ["today_1", "today_2"]


def test_read_log_events_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _read_recent_log_events(tmp_path / "missing.log", date(2025, 6, 3)) == []


# --------------------------------------------------------------------------- #
# State snapshot                                                              #
# --------------------------------------------------------------------------- #

def test_read_state_snapshot_missing_returns_defaults(tmp_path: Path) -> None:
    snap = _read_state_snapshot(tmp_path / "no_state.json")
    assert snap.halt_active is False
    assert snap.last_rebalance_date is None


def test_read_state_snapshot_handles_corrupt_json(tmp_path: Path) -> None:
    """Corrupt state shouldn't crash the alerter — silently degrade so the
    wrapper still tells you about the underlying failure."""
    path = tmp_path / "bad.json"
    path.write_text("{not json}", encoding="utf-8")
    snap = _read_state_snapshot(path)
    assert snap.halt_active is False


def test_read_state_snapshot_extracts_halt_and_rebalance_date(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "version": 1,
        "halt_active": True,
        "last_rebalance_date": "2025-06-02",
        "last_run_date": "2025-06-03",
        "peak_equity": 100000.0,
        "holdings": {},
    }), encoding="utf-8")
    snap = _read_state_snapshot(path)
    assert snap.halt_active is True
    assert snap.last_rebalance_date == date(2025, 6, 2)


# --------------------------------------------------------------------------- #
# _build_summary                                                              #
# --------------------------------------------------------------------------- #

class _S:
    """Test-only helper: state snapshot literal."""
    def __init__(self, halt: bool = False, last_reb: date | None = None) -> None:
        self.halt_active = halt
        self.last_rebalance_date = last_reb


def test_build_summary_detects_rebalance_executed_today() -> None:
    summary = _build_summary(
        exit_code=0,
        run_date=date(2025, 6, 3),
        mode="paper",
        before=_S(last_reb=date(2025, 5, 1)),   # type: ignore[arg-type]
        after=_S(last_reb=date(2025, 6, 3)),    # type: ignore[arg-type]
        events=[{
            "event_type": "rebalance_planned",
            "payload": {"n_buys": 18, "n_sells": 15, "n_targets": 50},
            "timestamp": "2025-06-03 09:30:00,000",
        }],
        is_reset_invocation=False,
    )
    assert summary.rebalanced_today is True
    assert summary.rebalance_n_buys == 18
    assert summary.rebalance_n_sells == 15
    assert summary.rebalance_n_targets == 50


def test_build_summary_does_not_double_count_already_rebalanced() -> None:
    """If state shows last_rebalance_date already == today before the run,
    a same-day re-run shouldn't re-fire the rebalance alert."""
    summary = _build_summary(
        exit_code=0,
        run_date=date(2025, 6, 3),
        mode="paper",
        before=_S(last_reb=date(2025, 6, 3)),   # type: ignore[arg-type]
        after=_S(last_reb=date(2025, 6, 3)),    # type: ignore[arg-type]
        events=[],
        is_reset_invocation=False,
    )
    assert summary.rebalanced_today is False


def test_build_summary_collects_error_messages_and_caps_at_5() -> None:
    events = [
        {
            "event_type": f"err_{i}",
            "levelname": "ERROR",
            "message": f"problem {i}",
            "timestamp": "2025-06-03 09:30:00,000",
        }
        for i in range(10)
    ]
    summary = _build_summary(
        exit_code=99,
        run_date=date(2025, 6, 3),
        mode="paper",
        before=_S(),  # type: ignore[arg-type]
        after=_S(),   # type: ignore[arg-type]
        events=events,
        is_reset_invocation=False,
    )
    assert len(summary.error_messages) == 5
    # Latest five — i.e. problems 5..9, not 0..4
    assert "problem 9" in summary.error_messages
    assert "problem 5" in summary.error_messages
    assert "problem 4" not in summary.error_messages


# --------------------------------------------------------------------------- #
# Discord payload shape                                                       #
# --------------------------------------------------------------------------- #

def test_discord_payload_truncates_long_strings() -> None:
    """Discord enforces 256-char title, 4096-char description, 1024-char
    field values. The builder must clip rather than let the POST fail."""
    decision = AlertDecision(
        title="X" * 500,
        color=0,
        description="Y" * 5000,
        fields=(("name", "Z" * 2000),),
    )
    payload = build_discord_payload(decision)
    embed = payload["embeds"][0]
    assert len(embed["title"]) == 256
    assert len(embed["description"]) == 4096
    assert len(embed["fields"][0]["value"]) == 1024


def test_discord_payload_includes_embed_color_and_footer() -> None:
    decision = AlertDecision(
        title="t", color=0xE74C3C, description="d", fields=(),
    )
    payload = build_discord_payload(decision)
    assert payload["embeds"][0]["color"] == 0xE74C3C
    assert payload["embeds"][0]["footer"]["text"] == "momentum-bot alert"

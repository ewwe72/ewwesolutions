"""Discord alert rule tests for the crypto wrapper.

Same shape as the stocks bot's test_alert; only the title strings and
threshold-description text differ. Pure-function decide_alert is tested
directly; payload truncation is tested; the actual HTTP POST is not
exercised here (best-effort, mocked in transport layer).
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
# Error-branch decisions                                                      #
# --------------------------------------------------------------------------- #

def test_exit_2_reconciliation_alerts_red() -> None:
    decision = decide_alert(RunSummary(
        exit_code=2,
        run_date=date(2025, 6, 9),
        mode="paper",
        reconciliation_divergences=(
            "  BTC/USD: persisted=0.5, broker=0.6",
        ),
    ))
    assert decision is not None
    assert "RECONCILIATION" in decision.title
    assert "crypto" in decision.title.lower()
    assert decision.color == 0xE74C3C
    field_text = "\n".join(v for _, v in decision.fields)
    assert "BTC/USD" in field_text


def test_exit_3_feed_unhealthy_includes_fresh_pct() -> None:
    decision = decide_alert(RunSummary(
        exit_code=3,
        run_date=date(2025, 6, 9),
        mode="paper",
        feed_health_reasons=("fresh_pct 60.00% < min 80.00%",),
        feed_fresh_pct=0.60,
    ))
    assert decision is not None
    assert "FEED" in decision.title.upper()
    field_text = "\n".join(v for _, v in decision.fields)
    assert "60" in field_text


def test_exit_4_reset_misuse_explains_no_change() -> None:
    decision = decide_alert(RunSummary(
        exit_code=4, run_date=date(2025, 6, 9), mode="paper",
    ))
    assert decision is not None
    assert "NOT modified" in decision.description


# --------------------------------------------------------------------------- #
# Exit-0 transitions                                                          #
# --------------------------------------------------------------------------- #

def test_halt_activated_uses_crypto_threshold_in_description() -> None:
    """Description should reference the crypto-default -50% threshold,
    not the stocks-default -35%."""
    decision = decide_alert(RunSummary(
        exit_code=0, run_date=date(2025, 6, 9), mode="paper",
        halt_before=False, halt_after=True,
    ))
    assert decision is not None
    assert decision.color == 0xE67E22
    assert "-50%" in decision.description


def test_halt_resumed_uses_crypto_threshold_in_description() -> None:
    decision = decide_alert(RunSummary(
        exit_code=0, run_date=date(2025, 6, 9), mode="paper",
        halt_before=True, halt_after=False,
    ))
    assert decision is not None
    assert decision.color == 0x3498DB
    assert "-25%" in decision.description


def test_rebalance_executed_says_weekly() -> None:
    decision = decide_alert(RunSummary(
        exit_code=0, run_date=date(2025, 6, 9), mode="paper",
        rebalanced_today=True,
        rebalance_n_buys=7, rebalance_n_sells=0, rebalance_n_targets=7,
    ))
    assert decision is not None
    assert decision.color == 0x2ECC71
    assert "Weekly" in decision.description
    assert "Crypto rebalance" in decision.title


# --------------------------------------------------------------------------- #
# Silent branches                                                             #
# --------------------------------------------------------------------------- #

def test_no_op_day_silent() -> None:
    decision = decide_alert(RunSummary(
        exit_code=0, run_date=date(2025, 6, 9), mode="paper",
    ))
    assert decision is None


def test_reset_invocation_silent_even_on_misuse() -> None:
    decision = decide_alert(RunSummary(
        exit_code=4, run_date=date(2025, 6, 9), mode="paper",
        is_reset_invocation=True,
    ))
    assert decision is None


# --------------------------------------------------------------------------- #
# Log parsing                                                                 #
# --------------------------------------------------------------------------- #

def test_read_log_events_filters_by_date(tmp_path: Path) -> None:
    log = tmp_path / "crypto-bot.log"
    log.write_text(
        "\n".join([
            json.dumps({"timestamp": "2025-06-08 23:55:00,000", "event_type": "yesterday"}),
            json.dumps({"timestamp": "2025-06-09 14:00:00,000", "event_type": "today"}),
            json.dumps({"timestamp": "2025-06-10 01:00:00,000", "event_type": "tomorrow"}),
        ]),
        encoding="utf-8",
    )
    events = _read_recent_log_events(log, date(2025, 6, 9))
    assert [e["event_type"] for e in events] == ["today"]


# --------------------------------------------------------------------------- #
# State snapshot                                                              #
# --------------------------------------------------------------------------- #

def test_read_state_snapshot_extracts_halt_and_rebalance_date(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "version": 1,
        "halt_active": True,
        "last_rebalance_date": "2025-06-02",
        "last_run_date": "2025-06-09",
        "peak_equity": 100_000.0,
        "holdings": {},
    }), encoding="utf-8")
    snap = _read_state_snapshot(path)
    assert snap.halt_active is True
    assert snap.last_rebalance_date == date(2025, 6, 2)


# --------------------------------------------------------------------------- #
# _build_summary                                                              #
# --------------------------------------------------------------------------- #

class _S:
    def __init__(self, halt: bool = False, last_reb: date | None = None) -> None:
        self.halt_active = halt
        self.last_rebalance_date = last_reb


def test_build_summary_detects_weekly_rebalance() -> None:
    summary = _build_summary(
        exit_code=0,
        run_date=date(2025, 6, 9),
        mode="paper",
        before=_S(last_reb=date(2025, 6, 2)),   # type: ignore[arg-type]
        after=_S(last_reb=date(2025, 6, 9)),    # type: ignore[arg-type]
        events=[{
            "event_type": "rebalance_planned",
            "payload": {"n_buys": 5, "n_sells": 2, "n_targets": 7},
            "timestamp": "2025-06-09 14:00:00,000",
        }],
        is_reset_invocation=False,
    )
    assert summary.rebalanced_today is True
    assert summary.rebalance_n_buys == 5
    assert summary.rebalance_n_sells == 2


# --------------------------------------------------------------------------- #
# Payload truncation                                                          #
# --------------------------------------------------------------------------- #

def test_payload_truncates_long_strings() -> None:
    decision = AlertDecision(
        title="X" * 500, color=0, description="Y" * 5000,
        fields=(("name", "Z" * 2000),),
    )
    payload = build_discord_payload(decision)
    embed = payload["embeds"][0]
    assert len(embed["title"]) == 256
    assert len(embed["description"]) == 4096
    assert len(embed["fields"][0]["value"]) == 1024


def test_payload_includes_crypto_footer() -> None:
    """Footer text must distinguish crypto alerts from the stocks bot's."""
    decision = AlertDecision(title="t", color=0xE74C3C, description="d", fields=())
    payload = build_discord_payload(decision)
    assert payload["embeds"][0]["footer"]["text"] == "crypto-momentum alert"

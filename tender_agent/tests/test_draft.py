"""Unit tests for the section splitter + cost estimator.

The Anthropic API call itself is not exercised here — it lives in
`draft_for_announcement` and depends on the live API. Tests stay
deterministic: they feed canned model responses to the splitter and
known token counts to the cost estimator.
"""

from __future__ import annotations

import pytest

from tender_agent.draft import (
    PRICING_USD_PER_MILLION,
    _estimate_cost_usd,
    _split_sections,
)


# ---------- _split_sections ----------


def test_split_well_formed_response_returns_all_four() -> None:
    raw = """## A. Oświadczenie

Niniejszym oświadczam.

## B. JEDZ — Część I

Wypełnienie.

## C. Szkic listu

Pierwsza paragraf.

## D. Uwagi szkicownika

- punkt 1
- punkt 2
"""
    out = _split_sections(raw)
    assert set(out) == {"A", "B", "C", "D"}
    assert "oświadczam" in out["A"]
    assert "Wypełnienie" in out["B"]
    assert "Pierwsza paragraf" in out["C"]
    assert "punkt 1" in out["D"]
    assert "punkt 2" in out["D"]


def test_split_handles_leading_preamble() -> None:
    """Some models open with a sentence before the first heading."""
    raw = """Oto wymagane sekcje:

## A. Oświadczenie

Treść A.

## B. JEDZ — Część I

Treść B.

## C. Szkic listu

Treść C.
"""
    out = _split_sections(raw)
    assert set(out) >= {"A", "B", "C"}
    assert out["A"] == "Treść A."


def test_split_missing_section_returns_partial_dict() -> None:
    """Caller is responsible for checking the keys present."""
    raw = """## A. Oświadczenie
Tylko A jest.
"""
    out = _split_sections(raw)
    assert set(out) == {"A"}
    assert "Tylko A" in out["A"]


def test_split_extra_sections_beyond_G_ignored() -> None:
    """Phase 0.7+ valid range is A-G. Anything past G (H, I, ...) is dropped silently.

    Lets the splitter ignore noise like Roman-numeral sub-headings (`### I.1)`)
    that look syntactically like a top-level section letter."""
    raw = """## A. Oświadczenie
A.
## B. JEDZ Część I
B.
## C. List
C.
## D. Uwagi
D.
## E. JEDZ Część II
E.
## F. JEDZ Część III
F.
## G. JEDZ Część IV
G.
## H. Bonus
H should be dropped.
"""
    out = _split_sections(raw)
    assert set(out) == {"A", "B", "C", "D", "E", "F", "G"}
    assert "H" not in out
    assert "should be dropped" not in (out.get("G") or "")


def test_split_section_body_can_contain_hash_lines() -> None:
    """A real prompt sometimes emits sub-headings like `### I.1` inside a section.
    Those must NOT trip the splitter into a new top-level section."""
    raw = """## A. Oświadczenie

### I.1) Sub-heading inside A

This stays in A.

## B. JEDZ — Część I

### I.2) Another sub
This stays in B.

## C. Szkic listu

Body C.

## D. Uwagi

Body D.
"""
    out = _split_sections(raw)
    assert "I.1) Sub-heading" in out["A"]
    assert "I.2) Another sub" in out["B"]
    assert "stays in A" in out["A"]
    assert "stays in B" in out["B"]


def test_split_no_headings_returns_empty_dict() -> None:
    raw = "model returned plain prose — no sections"
    assert _split_sections(raw) == {}


# ---------- _estimate_cost_usd ----------


def test_cost_haiku_input_only() -> None:
    """Haiku 4.5: $1/M input. 1000 input tokens → $0.001."""
    cost = _estimate_cost_usd(
        model="claude-haiku-4-5",
        input_tokens=1000,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        output_tokens=0,
    )
    assert cost == pytest.approx(0.001, abs=1e-6)


def test_cost_haiku_input_plus_output() -> None:
    """Haiku 4.5: $1/M input + $5/M output. 1000 in + 1000 out → $0.006."""
    cost = _estimate_cost_usd(
        model="claude-haiku-4-5",
        input_tokens=1000,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        output_tokens=1000,
    )
    assert cost == pytest.approx(0.006, abs=1e-6)


def test_cost_cache_read_is_cheaper_than_fresh_input() -> None:
    """Cache reads cost 10% of fresh-input rate."""
    fresh = _estimate_cost_usd(
        model="claude-haiku-4-5",
        input_tokens=1000,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        output_tokens=0,
    )
    cached = _estimate_cost_usd(
        model="claude-haiku-4-5",
        input_tokens=0,
        cache_creation_tokens=0,
        cache_read_tokens=1000,
        output_tokens=0,
    )
    assert cached < fresh
    assert cached == pytest.approx(fresh * 0.10, abs=1e-6)


def test_cost_cache_creation_is_higher_than_fresh_input() -> None:
    """Cache creation costs 1.25× fresh-input rate."""
    fresh = _estimate_cost_usd(
        model="claude-haiku-4-5",
        input_tokens=1000,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        output_tokens=0,
    )
    creation = _estimate_cost_usd(
        model="claude-haiku-4-5",
        input_tokens=0,
        cache_creation_tokens=1000,
        cache_read_tokens=0,
        output_tokens=0,
    )
    assert creation > fresh
    assert creation == pytest.approx(fresh * 1.25, abs=1e-6)


def test_cost_sonnet_is_more_expensive_than_haiku() -> None:
    """Sanity: Sonnet is the pricier of the two production-grade models."""
    haiku = _estimate_cost_usd(
        model="claude-haiku-4-5",
        input_tokens=10_000,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        output_tokens=3_000,
    )
    sonnet = _estimate_cost_usd(
        model="claude-sonnet-4-6",
        input_tokens=10_000,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        output_tokens=3_000,
    )
    assert sonnet > haiku
    # Sonnet ratio per the rate table is 3× input, 3× output → ~3× total.
    assert sonnet == pytest.approx(haiku * 3.0, abs=1e-4)


def test_cost_unknown_model_falls_back_to_default() -> None:
    """An unknown model id shouldn't crash — it returns a rough estimate."""
    cost = _estimate_cost_usd(
        model="claude-some-future-model",
        input_tokens=1000,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        output_tokens=1000,
    )
    assert cost > 0


def test_pricing_table_keys_match_models_used_in_code() -> None:
    """Guard against typos: every model id wired in the code must be priced."""
    # The default model from env (set at import time) plus the two explicit
    # production picks should all be in the pricing table.
    for model in ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-7"):
        assert model in PRICING_USD_PER_MILLION, f"missing price for {model}"
        in_rate, out_rate = PRICING_USD_PER_MILLION[model]
        assert in_rate > 0
        assert out_rate > 0
        assert out_rate >= in_rate, "output is never cheaper than input"

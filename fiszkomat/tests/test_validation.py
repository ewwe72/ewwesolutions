"""Unit tests for fiszkomat.core.validate_cards + fiszkomat.web.price_for_pages.

These tests assert the ACTUAL behavior of the source as of 2026-05-16:

  - `validate_cards` dedupes by *exact* SHA-1 of `(t.strip().lower() + "|"
    + d.strip().lower())`. It does NOT do Levenshtein / fuzzy dedup.
  - There are NO minimum-length bounds; only maxima
    (`t<=200`, `m<=800`, `i<=600`, `c<=600`, `n<=800`).
  - Required fields per the pydantic `Card` model: `z` (int 0–99) plus
    `t, d, m, i, c` (all required strings). `n` is optional, defaults to "".
  - Polish diacritics are preserved as-is — no normalization, no stripping.

  - `price_for_pages` walks the tier table in order and returns the first
    tier whose `max_pages` cap covers the input. Out-of-range counts (>500)
    fall through to the last tier's price.
"""
from __future__ import annotations

import pytest

from fiszkomat.core import Card, validate_cards
from fiszkomat.web import PRICE_TIERS, PRICE_TIERS_QP, price_for_pages


# ---------- validate_cards ----------


def _ok_card(**overrides) -> dict:
    """Build a minimal valid raw-card dict; override individual fields per test."""
    base = {
        "z": 1,
        "t": "Beta-blokery",
        "d": "metoprolol, bisoprolol",
        "m": "Blokada receptorow beta-1 w sercu.",
        "i": "Nadcisnienie, niewydolnosc serca.",
        "c": "Astma oskrzelowa, blok AV II/III.",
        "n": "Bradykardia, hipotonia.",
    }
    base.update(overrides)
    return base


class TestValidateCardsDedup:
    """`validate_cards` dedups on EXACT (t.strip().lower(), d.strip().lower()).
    There is no Levenshtein / fuzzy step in the source."""

    def test_exact_duplicate_t_and_d_dropped(self):
        raw = [_ok_card(), _ok_card()]
        valid, rejected = validate_cards(raw)
        assert len(valid) == 1
        assert len(rejected) == 1
        assert "duplicate" in rejected[0][1].lower()

    def test_dedup_is_case_insensitive_and_strips_whitespace(self):
        a = _ok_card(t="Beta-blokery", d="metoprolol, bisoprolol")
        b = _ok_card(t="  beta-blokery  ", d="METOPROLOL, BISOPROLOL")
        valid, rejected = validate_cards([a, b])
        assert len(valid) == 1
        assert len(rejected) == 1

    def test_near_duplicate_one_char_off_NOT_deduped(self):
        """No Levenshtein in the source — a single-character difference in `t`
        produces a different SHA-1 key, so both cards survive. If a fuzzy
        dedup step is ever added, flip this assertion."""
        a = _ok_card(t="Beta-blokery", d="metoprolol")
        b = _ok_card(t="Beta-blockery", d="metoprolol")  # one-char diff
        valid, _ = validate_cards([a, b])
        assert len(valid) == 2

    def test_different_d_with_same_t_kept(self):
        a = _ok_card(t="Beta-blokery", d="metoprolol")
        b = _ok_card(t="Beta-blokery", d="bisoprolol")
        valid, _ = validate_cards([a, b])
        assert len(valid) == 2


class TestValidateCardsLengthBounds:
    """Source has MAX-length caps only — no minimums. Tests pin actual maxes."""

    def test_t_too_long_rejected_at_201(self):
        raw = [_ok_card(t="x" * 201)]
        valid, rejected = validate_cards(raw)
        assert valid == []
        assert rejected[0][1] == "t too long"

    def test_t_exactly_200_kept(self):
        raw = [_ok_card(t="x" * 200)]
        valid, _ = validate_cards(raw)
        assert len(valid) == 1

    def test_m_too_long_rejected_at_801(self):
        raw = [_ok_card(m="x" * 801)]
        valid, rejected = validate_cards(raw)
        assert valid == []
        assert rejected[0][1] == "field too long"

    def test_i_too_long_rejected_at_601(self):
        raw = [_ok_card(i="x" * 601)]
        valid, rejected = validate_cards(raw)
        assert valid == []
        assert rejected[0][1] == "field too long"

    def test_c_too_long_rejected_at_601(self):
        raw = [_ok_card(c="x" * 601)]
        valid, rejected = validate_cards(raw)
        assert valid == []
        assert rejected[0][1] == "field too long"

    def test_n_too_long_rejected_at_801(self):
        raw = [_ok_card(n="x" * 801)]
        valid, rejected = validate_cards(raw)
        assert valid == []
        assert rejected[0][1] == "field too long"

    def test_empty_string_fields_pass_no_min_length(self):
        """No minimum length is enforced (per source). Empty strings for
        optional/short fields don't trigger rejection."""
        raw = [_ok_card(t="x", d="y", m="", i="", c="", n="")]
        valid, _ = validate_cards(raw)
        assert len(valid) == 1


class TestValidateCardsDiacritics:
    """Polish diacritics are preserved verbatim — no normalization step exists."""

    def test_all_polish_diacritics_preserved_round_trip(self):
        polish = "ąęćłńóśźż ĄĘĆŁŃÓŚŹŻ"
        raw = [_ok_card(t=polish, d="lek X", m=polish, i=polish, c=polish, n=polish)]
        valid, rejected = validate_cards(raw)
        assert rejected == []
        assert len(valid) == 1
        card = valid[0]
        # exact byte-for-byte preservation
        assert card.t == polish
        assert card.m == polish
        assert card.i == polish
        assert card.c == polish
        assert card.n == polish

    def test_diacritics_in_real_pharmacology_terms(self):
        raw = [_ok_card(
            t="Inhibitory konwertazy angiotensyny",
            d="enalapryl, ramipryl",
            m="Hamowanie ACE; spadek angiotensyny II i aldosteronu.",
            i="Nadciśnienie tętnicze, niewydolność serca, nefropatia cukrzycowa.",
            c="Ciąża, obrzęk naczynioruchowy w wywiadzie, obustronne zwężenie tętnic nerkowych.",
            n="Suchy kaszel, hiperkaliemia, obrzęk naczynioruchowy.",
        )]
        valid, rejected = validate_cards(raw)
        assert rejected == []
        assert "tętnicze" in valid[0].i
        assert "ciąża".lower() in valid[0].c.lower()


class TestValidateCardsEmpty:
    def test_empty_input_returns_empty_outputs(self):
        valid, rejected = validate_cards([])
        assert valid == []
        assert rejected == []


class TestValidateCardsRequiredFields:
    """Per the pydantic Card model: z (0–99), t, d, m, i, c are required.
    `n` is optional and defaults to "". Missing required field => schema reject."""

    @pytest.mark.parametrize("missing", ["t", "d", "m", "i", "c"])
    def test_missing_required_string_field_rejected(self, missing):
        raw = _ok_card()
        raw.pop(missing)
        valid, rejected = validate_cards([raw])
        assert valid == []
        assert len(rejected) == 1
        assert rejected[0][1].startswith("schema:")

    def test_missing_z_rejected(self):
        raw = _ok_card()
        raw.pop("z")
        valid, rejected = validate_cards([raw])
        assert valid == []
        assert rejected[0][1].startswith("schema:")

    def test_missing_n_OK_defaults_to_empty(self):
        """n is optional — its absence must NOT reject the card."""
        raw = _ok_card()
        raw.pop("n")
        valid, rejected = validate_cards([raw])
        assert rejected == []
        assert len(valid) == 1
        assert valid[0].n == ""

    def test_z_out_of_range_rejected(self):
        # z is bounded 0..99 in the Card model
        raw = _ok_card(z=100)
        valid, rejected = validate_cards([raw])
        assert valid == []
        assert rejected[0][1].startswith("schema:")

    def test_z_zero_allowed_unknown_chapter(self):
        raw = _ok_card(z=0)
        valid, rejected = validate_cards([raw])
        assert rejected == []
        assert len(valid) == 1
        assert valid[0].z == 0


class TestValidateCardsEnglishLeakage:
    """The crude leakage guard rejects when >=2 English stopwords appear in `m`."""

    def test_two_english_stopwords_in_m_rejected(self):
        raw = [_ok_card(m="The drug binds to the receptor and inhibits enzyme activity.")]
        valid, rejected = validate_cards(raw)
        assert valid == []
        assert "English leakage" in rejected[0][1]

    def test_polish_text_with_word_the_in_brand_name_kept(self):
        # Only one English stopword (" the ") -> below threshold (2), accepted.
        raw = [_ok_card(m="Lek z grupy the-blokerow stosowany w nadcisnieniu.")]
        valid, _ = validate_cards(raw)
        assert len(valid) == 1


# ---------- price_for_pages ----------


class TestPriceForPagesStandard:
    """Standard PRICE_TIERS: (50, 300), (150, 500), (300, 1000), (500, 1500).
    The loop returns first tier where `pages <= max_pages` — so 50 hits tier
    1, 51 hits tier 2; 150 hits tier 2, 151 hits tier 3, etc."""

    def test_tier1_boundaries(self):
        assert price_for_pages(1) == 300
        assert price_for_pages(50) == 300

    def test_tier2_boundaries(self):
        assert price_for_pages(51) == 500
        assert price_for_pages(150) == 500

    def test_tier3_boundaries(self):
        assert price_for_pages(151) == 1000
        assert price_for_pages(300) == 1000

    def test_tier4_boundaries(self):
        assert price_for_pages(301) == 1500
        assert price_for_pages(500) == 1500

    def test_pages_zero_falls_into_tier1(self):
        """0 <= 50 is True, so pages=0 returns the tier-1 price. The web
        layer separately enforces a >0 page-count check before pricing."""
        assert price_for_pages(0) == 300

    def test_pages_above_500_returns_last_tier_price(self):
        # Source: `return table[-1][1]` when nothing matches.
        assert price_for_pages(501) == 1500
        assert price_for_pages(10_000) == 1500

    def test_default_quality_pass_is_false(self):
        # No quality_pass arg => standard tiers used.
        assert price_for_pages(50) == PRICE_TIERS[0][1]


class TestPriceForPagesQualityPass:
    """Quality-pass PRICE_TIERS_QP: (50, 500), (150, 800), (300, 1600), (500, 2500)."""

    def test_tier1_boundaries_qp(self):
        assert price_for_pages(1, quality_pass=True) == 500
        assert price_for_pages(50, quality_pass=True) == 500

    def test_tier2_boundaries_qp(self):
        assert price_for_pages(51, quality_pass=True) == 800
        assert price_for_pages(150, quality_pass=True) == 800

    def test_tier3_boundaries_qp(self):
        assert price_for_pages(151, quality_pass=True) == 1600
        assert price_for_pages(300, quality_pass=True) == 1600

    def test_tier4_boundaries_qp(self):
        assert price_for_pages(301, quality_pass=True) == 2500
        assert price_for_pages(500, quality_pass=True) == 2500

    def test_pages_zero_qp(self):
        assert price_for_pages(0, quality_pass=True) == 500

    def test_pages_above_500_returns_last_tier_qp(self):
        assert price_for_pages(501, quality_pass=True) == 2500
        assert price_for_pages(10_000, quality_pass=True) == 2500

    def test_qp_always_at_least_standard_price(self):
        # Quality-pass tier prices should never undercut the standard tier
        # for the same page count — quality pass adds cost, not removes it.
        for pages in (1, 50, 51, 150, 151, 300, 301, 500, 501):
            assert price_for_pages(pages, quality_pass=True) >= price_for_pages(pages)

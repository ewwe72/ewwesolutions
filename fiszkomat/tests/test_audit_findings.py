"""Pinning tests for the 2026-05-16 prompt-safety audit.

Source of truth: `docs/fiszkomat-prompt-audit.md`. These tests pin the two
audit findings that are pure-unit and deterministic (no LLM calls):

  Section 1 — HTML-escape safety across the three render surfaces.
    The audit (§"TL;DR" / §2 "Downstream HTML safety: confirmed safe")
    asserts that card content passes through `html.escape`,
    `textContent`, and Mustache double-stache before reaching any user.
    A regression here would re-introduce stored XSS in the reviewer
    for malicious card content. These tests fail loudly if a future
    refactor swaps any escape for raw interpolation.

  Section 2 — English-leakage detector limits, pinned as xfail.
    The audit (§1 "Polish-only enforcement", "Gaps") documents that
    `validate_cards` only inspects field `m` and only matches 5
    hardcoded English stopwords with threshold >=2. Cards that are
    obviously English in *any other field*, or in `m` but with
    different vocabulary, slip through today. These tests are
    `xfail(strict=True)` so that the day the detector is tightened
    pytest reports XPASS → forces the test author to remove the
    xfail and rewrite as a positive assertion.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from fiszkomat.core import validate_cards
from fiszkomat.web import _render_one_card


# ---------- Section 1: HTML escape safety pins ----------


def _ok_card(**overrides) -> dict:
    """Mirror tests/test_validation.py — minimal valid card dict."""
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


XSS_PAYLOAD = "<script>alert(1)</script>X"
# html.escape produces &lt;script&gt;alert(1)&lt;/script&gt;X
ESCAPED_OPEN = "&lt;script&gt;"
ESCAPED_CLOSE = "&lt;/script&gt;"


class TestSampleHtmlEscapesXss:
    """`web._render_one_card` is the sample-deck renderer for the landing
    page. The audit pins this at `web.py:811-816` ("e = html_lib.escape").
    These tests fail if the function ever interpolates card fields raw."""

    def test_sample_html_escapes_script_in_card_t(self):
        out = _render_one_card(_ok_card(t=XSS_PAYLOAD))
        # The literal script tag must NOT appear in the rendered HTML.
        assert "<script>alert(1)</script>" not in out
        # The escaped form MUST appear.
        assert ESCAPED_OPEN in out
        assert ESCAPED_CLOSE in out

    @pytest.mark.parametrize("field", ["t", "d", "m", "i", "c"])
    def test_sample_html_escapes_in_all_visible_fields(self, field):
        """`_render_one_card` interpolates t, d, m, i, c (n is not shown
        on the sample landing — see web.py:817-839). Each visible field
        must be HTML-escaped."""
        out = _render_one_card(_ok_card(**{field: XSS_PAYLOAD}))
        assert "<script>alert(1)</script>" not in out
        assert ESCAPED_OPEN in out
        assert ESCAPED_CLOSE in out

    def test_sample_html_escapes_double_encoded_payload(self):
        """Input that is already `&lt;script&gt;` should render as
        `&amp;lt;script&amp;gt;` — proving the escape does not collapse
        a pre-escaped sequence back into an executable tag."""
        pre_escaped = "&lt;script&gt;alert(1)&lt;/script&gt;"
        out = _render_one_card(_ok_card(t=pre_escaped))
        # The ampersand itself must be re-escaped.
        assert "&amp;lt;script&amp;gt;" in out
        # And the un-escaped form must not appear.
        assert "<script>" not in out

    def test_sample_html_escapes_quote_and_event_handler(self):
        """A common XSS vector is `" onclick="alert(1)` inside an
        attribute. html.escape with default quote=True converts both
        " and ' so attribute-context injection is also blunted."""
        payload = '" onclick="alert(1)'
        out = _render_one_card(_ok_card(t=payload))
        # The literal unescaped double-quote-then-onclick must not appear.
        assert '" onclick="alert(1)' not in out
        # html.escape emits &quot; for "
        assert "&quot;" in out


class TestAnkiTemplateMustacheDoubleStache:
    """The .apkg card template uses Mustache `{{Field}}` (double-stache)
    interpolation. Anki auto-HTML-escapes double-stache; triple-stache
    `{{{Field}}}` would emit raw HTML. The audit pins this at core.py
    lines 434-446. This test reads the source as text and asserts the
    template strings never use the triple-stache form for a card field."""

    def test_card_template_uses_double_stache_not_triple(self):
        core_src = Path(__file__).resolve().parent.parent / "src" / "fiszkomat" / "core.py"
        text = core_src.read_text(encoding="utf-8")
        # All template-rendered card fields must appear as {{Field}} —
        # double stache. The triple-stache form {{{Field}}} would tell
        # Anki to render raw HTML for that field.
        for field in ("Tytul", "Leki", "Mechanizm", "Wskazania",
                      "Przeciwwskazania", "Dzialania", "Zajecia"):
            double = "{{" + field + "}}"
            triple = "{{{" + field + "}}}"
            assert double in text, (
                f"Anki template lost double-stache for {double}"
            )
            assert triple not in text, (
                f"Anki template uses triple-stache {triple} "
                f"— this disables auto-escape and re-introduces XSS"
            )


class TestStudyHtmlUsesTextContent:
    """The in-browser reviewer (`study_html.STUDY_HTML`) must build card
    DOM via `textContent`, never `innerHTML`. The audit pins this at
    study_html.py:422 (the `el(tag, cls, text)` helper) and lines 496-500,
    558, 609-610 (the call sites that pass card fields to it)."""

    def _read_study_html(self) -> str:
        path = Path(__file__).resolve().parent.parent / "src" / "fiszkomat" / "study_html.py"
        return path.read_text(encoding="utf-8")

    def test_study_html_uses_textcontent(self):
        text = self._read_study_html()
        # The reviewer's DOM helper sets text via .textContent.
        assert ".textContent" in text, (
            "study_html.py lost its textContent-based render helper"
        )

    def test_study_html_never_uses_innerhtml(self):
        text = self._read_study_html()
        # innerHTML on any node that could receive card content would
        # re-introduce XSS. The audit confirms zero innerHTML in this
        # file as of 2026-05-16. If a future refactor adds it (e.g. for
        # markdown rendering), this test fires and forces an explicit
        # sanitiser decision rather than a silent regression.
        assert "innerHTML" not in text, (
            "study_html.py now contains 'innerHTML' — check it does NOT "
            "receive card content (XSS risk). See "
            "docs/fiszkomat-prompt-audit.md §2."
        )


# ---------- Section 2: English-detector positive pins ----------
#
# These tests were registered as @xfail(strict=True) on 2026-05-16 to
# document detector gaps. Recommendation #2 in the audit landed on
# 2026-05-17: the detector now inspects every visible card text field
# (t, d, m, i, c, n) and matches a broader English-only stopword set
# (core.py `_ENGLISH_STOPWORDS`). The xfail decorators were removed
# once the assertions started passing — the assertions stay as the
# positive pin that the broadened behaviour does not regress.


class TestEnglishDetectorScope:
    """The detector inspects every visible text field, not just `m`.
    Cards that are obviously English in `t` or `d` are rejected even
    when `m` is Polish."""

    def test_english_in_t_field_rejected(self):
        raw = [_ok_card(t="What is the mechanism and how does it work")]
        valid, rejected = validate_cards(raw)
        assert valid == []
        assert len(rejected) == 1
        assert "English" in rejected[0][1]

    def test_english_in_d_field_rejected(self):
        raw = [_ok_card(d="the drug and its dose with notes of usage")]
        valid, rejected = validate_cards(raw)
        assert valid == []
        assert len(rejected) == 1
        assert "English" in rejected[0][1]


class TestEnglishDetectorVocabulary:
    """The detector matches a broad English-only stopword set. English
    text with low coverage of the original five-word set still trips
    the threshold (>= 2 total matches across all fields)."""

    def test_one_classic_stopword_plus_other_english_rejected(self):
        # "the" + "was" + "for" — three hits via the expanded set.
        raw = [_ok_card(m="the patient was treated for antibiotics recovery")]
        valid, rejected = validate_cards(raw)
        assert valid == []
        assert len(rejected) == 1
        assert "English" in rejected[0][1]

    def test_non_classic_english_words_rejected(self):
        # Zero of the original five stopwords (the/and/with/of/is) — only
        # the expanded set picks up "was" and "for" → 2 hits → rejected.
        raw = [_ok_card(m="patient was treated for cancer recovery rapid response")]
        valid, rejected = validate_cards(raw)
        assert valid == []
        assert len(rejected) == 1
        assert "English" in rejected[0][1]

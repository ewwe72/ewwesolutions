"""Unit tests for the cost-sanitization fence in fiszkomat.web.

Two surfaces:
  - `_sanitize_log_lines`: drops USD amounts and raw token counts from
    log lines before they're projected out to the client. Per source:

        _COST_PATTERN = re.compile(
            r"(?:[,\\s])?\\$\\d+\\.\\d+|in=\\d+|out=\\d+|cache_r=\\d+|cache_w=\\d+",
            re.IGNORECASE,
        )

    Then per-line: substitution, `.rstrip(" ,")`, and a `r" {2,}" -> " "`
    collapse. The leading `[,\\s]?` belongs to the `\\$\\d+\\.\\d+`
    alternative only (alternation has lower precedence than `?`), so
    `in=` / `out=` / `cache_r=` / `cache_w=` strip the token itself but
    not a preceding space — the space-collapse handles that.

  - `_stats_dict`: serializes RunStats for the customer. Must NEVER include
    `api_cost_usd` (or token counts) — operator's wholesale cost is private.
"""
from __future__ import annotations

import re

from fiszkomat.core import RunStats
from fiszkomat.web import _COST_PATTERN, _sanitize_log_lines, _stats_dict


# ---------- _sanitize_log_lines ----------


class TestSanitizeStripsDollarAmounts:
    def test_strips_simple_dollar_amount(self):
        out = _sanitize_log_lines(["Chunk 3 done $0.1234"])
        assert "$" not in out[0]
        assert "0.1234" not in out[0]
        assert "Chunk 3 done" in out[0]

    def test_strips_dollar_amount_with_leading_comma(self):
        out = _sanitize_log_lines(["Chunk 3 done, $0.42"])
        # The leading `,` is consumed by `[,\s]?`, then rstrip(" ,") tidies any tail.
        assert "$" not in out[0]
        assert "0.42" not in out[0]

    def test_strips_dollar_amount_with_many_decimals(self):
        out = _sanitize_log_lines(["cost: $12.345678"])
        assert "$" not in out[0]
        assert "345678" not in out[0]

    def test_strips_large_dollar_amount(self):
        out = _sanitize_log_lines(["total $9999.99 today"])
        assert "$" not in out[0]
        assert "9999.99" not in out[0]
        assert "total" in out[0]
        assert "today" in out[0]


class TestSanitizeStripsTokenCounts:
    def test_strips_in_count(self):
        out = _sanitize_log_lines(["chunk 0: in=4321 done"])
        assert "in=" not in out[0]
        assert "4321" not in out[0]

    def test_strips_out_count(self):
        out = _sanitize_log_lines(["chunk 0: out=987 done"])
        assert "out=" not in out[0]
        assert "987" not in out[0]

    def test_strips_cache_r_count(self):
        out = _sanitize_log_lines(["chunk 0: cache_r=512 done"])
        assert "cache_r=" not in out[0]
        assert "512" not in out[0]

    def test_strips_cache_w_count(self):
        out = _sanitize_log_lines(["chunk 0: cache_w=128 done"])
        assert "cache_w=" not in out[0]
        assert "128" not in out[0]

    def test_strips_all_token_counts_in_one_line(self):
        line = "chunk 2: in=4000 out=900 cache_r=200 cache_w=50 $0.012 done"
        out = _sanitize_log_lines([line])[0]
        for forbidden in ("in=", "out=", "cache_r=", "cache_w=", "$", "0.012", "4000", "900", "200", "50"):
            assert forbidden not in out, f"{forbidden!r} leaked through: {out!r}"
        # Surrounding labels preserved
        assert "chunk 2" in out
        assert "done" in out

    def test_token_count_case_insensitive(self):
        # Per `re.IGNORECASE` on _COST_PATTERN.
        out = _sanitize_log_lines(["IN=10 OUT=20 Cache_R=5 CACHE_W=3"])[0]
        for digit in ("10", "20", "5", "3"):
            assert digit not in out


class TestSanitizePreservesNormalText:
    def test_user_facing_message_untouched(self):
        msg = "Fiszki wygenerowane: 42 / 50 (kolokwium)"
        out = _sanitize_log_lines([msg])
        assert out == [msg]

    def test_error_reason_untouched(self):
        msg = "FAILED: nie udalo sie wyciagnac tekstu z PDFu"
        out = _sanitize_log_lines([msg])
        assert out == [msg]

    def test_chunk_progress_line_untouched(self):
        msg = "chunk 4/10: Zajecia 5"
        out = _sanitize_log_lines([msg])
        assert out == [msg]

    def test_empty_string_passes_through(self):
        out = _sanitize_log_lines([""])
        assert out == [""]

    def test_empty_list_returns_empty_list(self):
        assert _sanitize_log_lines([]) == []

    def test_filename_with_dollar_safe(self):
        # No `$<digits>.<digits>` pattern => "$path/file.pdf" survives intact.
        msg = "writing to $path/file.pdf"
        out = _sanitize_log_lines([msg])
        assert out == [msg]

    def test_decimal_without_dollar_safe(self):
        # Bare numbers like wall-time "12.5s" don't match `\$\d+\.\d+`.
        msg = "chunk 0 took 12.5s"
        out = _sanitize_log_lines([msg])
        assert out == [msg]


class TestSanitizeIdempotent:
    def test_sanitizing_clean_text_unchanged(self):
        clean = ["Talia gotowa.", "Pobierz .apkg ponizej."]
        assert _sanitize_log_lines(_sanitize_log_lines(clean)) == clean

    def test_double_sanitize_equals_single(self):
        dirty = [
            "chunk 0: in=100 out=50 cache_r=0 cache_w=0 $0.001 done",
            "chunk 1: in=200 out=80 cache_r=100 cache_w=20 $0.0021 done",
            "Talia gotowa.",
        ]
        once = _sanitize_log_lines(dirty)
        twice = _sanitize_log_lines(once)
        assert once == twice

    def test_sanitized_lines_contain_no_pattern_leftovers(self):
        dirty = ["chunk 0: in=100 out=50 cache_r=0 cache_w=0 $0.001 done"]
        cleaned = _sanitize_log_lines(dirty)[0]
        # The pattern must not match anywhere in the post-sanitization line.
        assert _COST_PATTERN.search(cleaned) is None


class TestSanitizeNoDoubleSpaceArtifacts:
    """After substitution, runs of >=2 spaces are collapsed to single space."""

    def test_substitution_does_not_leave_double_space(self):
        line = "before in=42 after"
        out = _sanitize_log_lines([line])[0]
        # `in=42` becomes "", leaving "before  after" pre-collapse.
        assert "  " not in out
        assert out == "before after"

    def test_trailing_comma_or_space_rstripped(self):
        # A trailing `, $0.01` would become a trailing space/comma after sub;
        # `.rstrip(" ,")` should remove it.
        out = _sanitize_log_lines(["chunk done, $0.01"])[0]
        assert not out.endswith(",")
        assert not out.endswith(" ")
        assert "chunk done" in out


# ---------- _stats_dict ----------


def _full_run_stats() -> RunStats:
    """RunStats is a dataclass with cost + token fields. Build one fully
    populated so the test verifies those fields are actively dropped."""
    return RunStats(
        pdf_pages=42,
        chunks=9,
        cards_raw=200,
        cards_valid=180,
        cards_rejected=20,
        input_tokens=12345,
        output_tokens=6789,
        cache_read_tokens=1000,
        cache_creation_tokens=500,
        api_cost_usd=0.4321,
        wall_seconds=33.7,
    )


class TestStatsDict:
    def test_none_input_returns_none(self):
        assert _stats_dict(None) is None

    def test_api_cost_usd_never_present(self):
        out = _stats_dict(_full_run_stats())
        assert out is not None
        assert "api_cost_usd" not in out

    def test_token_count_fields_never_present(self):
        out = _stats_dict(_full_run_stats())
        assert out is not None
        for forbidden in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
        ):
            assert forbidden not in out, f"{forbidden} leaked into customer-facing stats"

    def test_safe_fields_present_and_correct(self):
        stats = _full_run_stats()
        out = _stats_dict(stats)
        assert out is not None
        assert out["pdf_pages"] == 42
        assert out["chunks"] == 9
        assert out["cards_valid"] == 180
        assert out["cards_rejected"] == 20
        assert out["wall_seconds"] == 33.7

    def test_no_cost_substring_anywhere_in_serialized_output(self):
        """Belt-and-braces: even if a field was renamed, the word `cost`
        and a dollar marker shouldn't show up in the serialized values."""
        import json as _json
        out = _stats_dict(_full_run_stats())
        blob = _json.dumps(out)
        assert "cost" not in blob.lower()
        assert "$" not in blob
        # The actual cost value 0.4321 shouldn't appear as a number either.
        assert "0.4321" not in blob
        assert "0.43" not in blob

    def test_exact_keyset_pinned(self):
        """Pin the public surface — any new field added to `_stats_dict`
        is an intentional decision the operator must make. This guards
        against accidental cost leakage via a future refactor."""
        out = _stats_dict(_full_run_stats())
        assert out is not None
        assert set(out.keys()) == {
            "pdf_pages",
            "chunks",
            "cards_valid",
            "cards_rejected",
            "wall_seconds",
        }

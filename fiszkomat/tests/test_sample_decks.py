"""Unit tests for the curated sample-deck surface in fiszkomat.web.

Covers three surfaces:

  - `_valid_sample_slug(slug: str) -> bool`: cheap whitelist check against
    the hardcoded `SAMPLE_DECKS` table. Returns False for anything not in
    the table (including path-traversal payloads, null bytes, whitespace,
    "." and "..") — it does NOT raise. Returns True only for the exact
    `slug` field of one of the SAMPLE_DECKS entries.

  - `test_docs/out/<slug>.cards.json`: committed sample-card payloads,
    one per slug in SAMPLE_DECKS. Each file is a JSON list of dicts;
    each dict carries the keys required by the pydantic Card model
    (`z`, `t`, `d`, `m`, `i`, `c`) with `n` being optional (defaults to
    "" via the Card model). `z` is an int in [0, 99].

  - `test_docs/out/<slug>.apkg`: precomputed Anki package shipped
    alongside cards.json so the /sample/<slug>/deck route can serve a
    real file without re-running the worker. Must be non-empty.

The deck list is discovered from disk at collection time (not hardcoded)
so adding/removing a sample deck doesn't require touching this file.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fiszkomat.web import SAMPLE_DECKS, _samples_dir, _valid_sample_slug


# ---------- discovery ----------


def _discover_deck_slugs() -> list[str]:
    """List of slugs that have a `<slug>.cards.json` on disk under
    `test_docs/out/`. Sorted for deterministic parametrize IDs."""
    out = _samples_dir()
    if not out.is_dir():
        return []
    return sorted(p.stem.removesuffix(".cards") for p in out.glob("*.cards.json"))


SAMPLE_SLUGS: list[str] = _discover_deck_slugs()


# ---------- _valid_sample_slug ----------


class TestValidSampleSlugRejectsBadInput:
    """Slug validator is a strict whitelist — anything not in SAMPLE_DECKS
    is False, including obvious abuse payloads. Source returns bool, no
    exceptions."""

    @pytest.mark.parametrize(
        "bad",
        [
            "",                       # empty
            "..",                     # parent-dir traversal
            "../zaj13",               # combined traversal
            "zaj13/..",
            "zaj13/extra",            # path separator
            "/zaj13",
            "zaj13\\nope",            # backslash separator
            "zaj 13",                 # whitespace
            " zaj13",                 # leading whitespace
            "zaj13 ",                 # trailing whitespace
            "zaj\t13",                # tab
            "zaj\n13",                # newline
            "zaj13\x00",              # null byte
            "\x00",                   # bare null
            ".",                      # current dir
            "zaj99",                  # plausible but not in SAMPLE_DECKS
            "Zaj13",                  # case-sensitive — uppercase Z not in table
            "ZAJ13",
            "zaj13.cards.json",       # filename, not slug
            "zaj13.apkg",
        ],
    )
    def test_bad_input_returns_false(self, bad: str) -> None:
        assert _valid_sample_slug(bad) is False, f"slug {bad!r} should be rejected"

    def test_real_slugs_from_sample_decks_return_true(self) -> None:
        """Every slug declared in the SAMPLE_DECKS table must validate."""
        assert SAMPLE_DECKS, "SAMPLE_DECKS table is empty — source regression"
        for meta in SAMPLE_DECKS:
            slug = meta["slug"]
            assert _valid_sample_slug(slug) is True, f"slug {slug!r} should validate"

    @pytest.mark.parametrize("slug", SAMPLE_SLUGS)
    def test_discovered_disk_slug_validates(self, slug: str) -> None:
        """Every `<slug>.cards.json` on disk should also be registered in
        SAMPLE_DECKS — otherwise the file is unreachable via /sample/<slug>.
        If this fails: either remove the orphan file or add it to
        SAMPLE_DECKS."""
        assert _valid_sample_slug(slug) is True, (
            f"disk has {slug}.cards.json but SAMPLE_DECKS doesn't include it"
        )


# ---------- sample cards.json shape ----------


# Required keys per the pydantic Card model (see fiszkomat.core.Card and
# the test_validation.py docstring). `n` is optional; everything else is
# required.
_REQUIRED_CARD_KEYS = ("z", "t", "d", "m", "i", "c")


class TestSampleCardsJsonParseable:
    """Pins the on-disk shape of every committed sample deck. These files
    are hand-curated assets (see commit 4be85ba); these tests guard against
    accidental corruption (e.g. a JSON edit that drops a required key)."""

    @pytest.mark.parametrize("slug", SAMPLE_SLUGS)
    def test_cards_json_is_list_of_dicts(self, slug: str) -> None:
        path = _samples_dir() / f"{slug}.cards.json"
        assert path.is_file(), f"{path} missing"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, list), f"{slug}.cards.json top-level must be a list"
        assert len(data) > 0, f"{slug}.cards.json is empty"
        for idx, entry in enumerate(data):
            assert isinstance(entry, dict), (
                f"{slug}.cards.json[{idx}] is {type(entry).__name__}, expected dict"
            )

    @pytest.mark.parametrize("slug", SAMPLE_SLUGS)
    def test_cards_have_required_keys(self, slug: str) -> None:
        path = _samples_dir() / f"{slug}.cards.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for idx, card in enumerate(data):
            missing = [k for k in _REQUIRED_CARD_KEYS if k not in card]
            assert not missing, (
                f"{slug}.cards.json[{idx}] missing required keys: {missing}"
            )
            # `n` is optional per Card model (defaults to "") — don't assert it.

    @pytest.mark.parametrize("slug", SAMPLE_SLUGS)
    def test_z_is_int_in_range(self, slug: str) -> None:
        path = _samples_dir() / f"{slug}.cards.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for idx, card in enumerate(data):
            z = card["z"]
            # bools are ints in Python — exclude them explicitly.
            assert isinstance(z, int) and not isinstance(z, bool), (
                f"{slug}.cards.json[{idx}].z is {type(z).__name__}, expected int"
            )
            assert 0 <= z <= 99, (
                f"{slug}.cards.json[{idx}].z={z} out of [0, 99]"
            )

    @pytest.mark.parametrize("slug", SAMPLE_SLUGS)
    def test_question_field_non_empty(self, slug: str) -> None:
        """The visible front of a card must be non-empty after strip —
        a blank front would render an unstudyable card.

        For pharma (non-MCQ) cards the front is `t` (group name) — `t`
        must be non-empty.

        For MCQ cards (mikrobio) the front is `d` (the question stem) and
        `options` (A-E list); `t` may be empty if the bank parser didn't
        tag a correct option — operator decision 2026-05-18 to ship
        questions without correct markers rather than drop them. Reviewer
        renders these as "Odpowiedź: nieoznaczona w bazie".
        """
        path = _samples_dir() / f"{slug}.cards.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for idx, card in enumerate(data):
            options = card.get("options") or []
            if options:
                # MCQ: front is d + options; t can be empty for ambiguous
                d = card.get("d", "")
                assert isinstance(d, str) and d.strip(), (
                    f"{slug}.cards.json[{idx}] (MCQ) has blank `d` (stem)"
                )
                assert len(options) >= 2, (
                    f"{slug}.cards.json[{idx}] (MCQ) has <2 options"
                )
            else:
                # Pharma: front is t (group name)
                t = card.get("t", "")
                assert isinstance(t, str), (
                    f"{slug}.cards.json[{idx}].t is {type(t).__name__}, expected str"
                )
                assert t.strip(), f"{slug}.cards.json[{idx}].t is blank"


# ---------- sample .apkg files ----------


class TestSampleApkgFiles:
    """The `/sample/<slug>/deck` route streams the prebuilt `.apkg` straight
    from disk. Each curated slug must have a non-empty `.apkg` alongside
    its `.cards.json`."""

    @pytest.mark.parametrize("slug", SAMPLE_SLUGS)
    def test_apkg_exists_and_non_empty(self, slug: str) -> None:
        path = _samples_dir() / f"{slug}.apkg"
        assert path.is_file(), f"{path} missing"
        assert path.stat().st_size > 0, f"{path} is a zero-byte stub"


# ---------- discovery sanity ----------


class TestDiscovery:
    """Pin that discovery actually found decks — guards against the test
    file silently parametrizing over an empty list (which would
    pytest-report as 'collected 0 items' for the parametrized tests but
    still 'pass')."""

    def test_at_least_one_deck_discovered(self) -> None:
        assert SAMPLE_SLUGS, (
            f"no `.cards.json` files found under {_samples_dir()} — "
            "either checkout is broken or _samples_dir() resolution moved"
        )

    def test_samples_dir_resolves_to_expected_location(self) -> None:
        """`_samples_dir()` walks `web.py -> src/fiszkomat/web.py` up three
        parents to reach the project root. If web.py ever moves, this
        breaks and the landing page silently renders zero sample decks."""
        out = _samples_dir()
        assert out.name == "out"
        assert out.parent.name == "test_docs"

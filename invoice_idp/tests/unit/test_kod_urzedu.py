"""KodUrzedu (tax office code) shape validator."""

from __future__ import annotations

from src.utils.kod_urzedu import is_valid_kod_urzedu, normalize_kod_urzedu


def test_accepts_4_digit_code() -> None:
    # 0202 = US Warszawa-Mokotów (real code from the Ministry list).
    assert is_valid_kod_urzedu("0202")
    assert is_valid_kod_urzedu("1234")


def test_strips_whitespace_and_punctuation() -> None:
    assert is_valid_kod_urzedu(" 0202 ")
    assert is_valid_kod_urzedu("02-02")
    assert normalize_kod_urzedu("0202 — US Mokotów") == "0202"


def test_rejects_wrong_length() -> None:
    assert not is_valid_kod_urzedu("")
    assert not is_valid_kod_urzedu("020")
    assert not is_valid_kod_urzedu("02020")
    assert not is_valid_kod_urzedu("12345")


def test_rejects_non_digits() -> None:
    assert not is_valid_kod_urzedu("ABCD")
    assert not is_valid_kod_urzedu("02AB")

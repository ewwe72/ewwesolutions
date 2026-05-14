"""NIP checksum validator."""

from __future__ import annotations

from src.utils.nip import is_valid_nip, normalize_nip


def test_known_valid_nip() -> None:
    # Synthetic checksum-valid NIP (used across the test suite).
    assert is_valid_nip("1234567819")


def test_accepts_formatted_input() -> None:
    assert is_valid_nip("123-456-78-19")
    assert is_valid_nip("123 456 78 19")
    assert is_valid_nip("PL1234567819")


def test_rejects_bad_checksum() -> None:
    # Same digits, last one flipped
    assert not is_valid_nip("1234567810")


def test_rejects_wrong_length() -> None:
    assert not is_valid_nip("")
    assert not is_valid_nip("123")
    assert not is_valid_nip("12345678901")


def test_rejects_non_digits() -> None:
    assert not is_valid_nip("ABC1234567")


def test_rejects_mod_11_equals_10() -> None:
    # Construct a string whose first-9-digits mod 11 == 10 — invalid by spec.
    # Weights (6,5,7,2,3,4,5,6,7); pick 9 ones: 6+5+7+2+3+4+5+6+7 = 45; 45 % 11 = 1
    # Pick 9 twos: total = 90; 90 % 11 = 2. We need 10.
    # Try 5,5,5,5,5,5,5,5,5: 5*(6+5+7+2+3+4+5+6+7)=225; 225%11=5. Not 10.
    # Construct directly: total = 11k+10, with d_i in 0..9 and weighted.
    # 1,0,0,0,0,0,0,0,9 → 1*6 + 0+0+0+0+0+0+0 + 9*7 = 6+63 = 69 → 69%11=3. No.
    # 0,0,0,0,0,0,0,0,9 → 9*7=63 → 63%11=8. No.
    # 0,0,0,0,0,0,0,0,3 with weight 7 → 21 → 21%11=10. Yes!
    assert not is_valid_nip("0000000030")  # mod gives 10, must be rejected


def test_normalize_strips_separators() -> None:
    assert normalize_nip("PL 123-456-78-19") == "1234567819"

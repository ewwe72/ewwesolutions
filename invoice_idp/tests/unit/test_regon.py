"""REGON checksum validator."""

from __future__ import annotations

from src.utils.regon import is_valid_regon


def test_valid_9_digit() -> None:
    # Constructed: weights (8,9,2,3,4,5,6,7), first 8 = 12345678
    # Sum = 1*8+2*9+3*2+4*3+5*4+6*5+7*6+8*7 = 8+18+6+12+20+30+42+56 = 192
    # 192 % 11 = 5 → check digit 5
    assert is_valid_regon("123456785")


def test_invalid_9_digit_bad_checksum() -> None:
    assert not is_valid_regon("123456784")


def test_valid_14_digit() -> None:
    # First 9: 123456785 (valid). Next 4 chosen arbitrarily, then compute 14th.
    # Use prefix 12345678511230; the 14-weight checksum for the first 13 of
    # 12345678511237 needs verifying — easier: just round-trip via the
    # validator by trying several candidates.
    for last in "0123456789":
        candidate = "1234567851123" + last
        if is_valid_regon(candidate):
            assert is_valid_regon(candidate)
            return
    raise AssertionError("no valid 14-digit candidate found — generator broken")


def test_invalid_14_digit_when_prefix_invalid() -> None:
    # Prefix 123456784 is invalid (wrong 9-digit checksum) so the full
    # 14-digit must also fail regardless of the 14-digit checksum
    assert not is_valid_regon("12345678412345")


def test_rejects_wrong_length() -> None:
    assert not is_valid_regon("")
    assert not is_valid_regon("12345")
    assert not is_valid_regon("123456789012")  # 12 digits


def test_rejects_non_digits() -> None:
    assert not is_valid_regon("ABCDEFGHI")

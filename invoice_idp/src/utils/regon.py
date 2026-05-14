"""Polish REGON (statistical business number) checksum validation."""

from __future__ import annotations

REGON_9_WEIGHTS: tuple[int, ...] = (8, 9, 2, 3, 4, 5, 6, 7)
REGON_14_WEIGHTS: tuple[int, ...] = (2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8)


def normalize_regon(regon: str) -> str:
    return "".join(c for c in regon if c.isdigit())


def _checksum_matches(digits: str, weights: tuple[int, ...]) -> bool:
    total = sum(int(d) * w for d, w in zip(digits[:-1], weights))
    mod = total % 11
    check = 0 if mod == 10 else mod
    return check == int(digits[-1])


def is_valid_regon(regon: str) -> bool:
    """Validate 9-digit or 14-digit Polish REGON.

    For 14-digit REGON, both the 9-digit prefix checksum and the full
    14-digit checksum must verify.
    """
    digits = normalize_regon(regon)
    if not digits.isdigit():
        return False
    if len(digits) == 9:
        return _checksum_matches(digits, REGON_9_WEIGHTS)
    if len(digits) == 14:
        return (
            _checksum_matches(digits[:9], REGON_9_WEIGHTS)
            and _checksum_matches(digits, REGON_14_WEIGHTS)
        )
    return False

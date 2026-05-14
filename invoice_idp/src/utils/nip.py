"""Polish NIP (Tax Identification Number) checksum validation."""

from __future__ import annotations

NIP_WEIGHTS: tuple[int, ...] = (6, 5, 7, 2, 3, 4, 5, 6, 7)


def normalize_nip(nip: str) -> str:
    """Strip non-digit characters (handles 'PL', spaces, dashes)."""
    return "".join(c for c in nip if c.isdigit())


def is_valid_nip(nip: str) -> bool:
    """Validate Polish NIP: 10 digits, checksum mod-11 of first 9 == last digit.

    Per Polish tax law, a mod-11 result of 10 is not allowed and indicates
    an invalid NIP (such combinations are not issued).
    """
    digits = normalize_nip(nip)
    if len(digits) != 10:
        return False
    if not digits.isdigit():
        return False
    total = sum(int(d) * w for d, w in zip(digits[:9], NIP_WEIGHTS))
    check = total % 11
    if check == 10:
        return False
    return check == int(digits[9])

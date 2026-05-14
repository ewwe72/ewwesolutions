"""Argon2id password hashing.

Per spec §10: argon2id for password storage. Uses argon2-cffi defaults
which target ≥0.5s on modern hardware — adequate for V1. Tune
parameters once the prod VPS spec is known.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash with argon2id. Returns the encoded hash string."""
    return _hasher.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    """Verify a password against its hash. Returns False on mismatch."""
    try:
        _hasher.verify(encoded_hash, password)
        return True
    except (VerifyMismatchError, VerificationError):
        return False


def needs_rehash(encoded_hash: str) -> bool:
    """True if the hash was generated with weaker params than current defaults."""
    return _hasher.check_needs_rehash(encoded_hash)

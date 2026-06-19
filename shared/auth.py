"""Dashboard authentication — bcrypt-hashed, per-user passwords.

Only salted bcrypt **hashes** are stored, never plaintext. Hashes live in the
environment as ``AZRA_PASSWORD_HASH`` / ``BERRIN_PASSWORD_HASH`` (loaded from
``.env`` locally, or Streamlit secrets when hosted — both gitignored).

Generate a hash with:  python -m scripts.set_password <azra|berrin>
"""

from __future__ import annotations

import os

import bcrypt

from shared.config import USERS


def hash_password(password: str) -> str:
    """Return a salted bcrypt hash for a plaintext password."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _hash_for(slug: str) -> str | None:
    """The stored bcrypt hash for a user, or None if not configured."""
    return os.getenv(f"{slug.upper()}_PASSWORD_HASH")


def verify(slug: str, password: str) -> bool:
    """Check a plaintext password against the stored hash for `slug`."""
    stored = _hash_for(slug)
    if not stored:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
    except ValueError:
        return False  # malformed hash


def configured_users() -> list[str]:
    """Slugs that have a password hash set (i.e. login is active)."""
    return [slug for slug in USERS if _hash_for(slug)]


def authenticate(username: str, password: str) -> str | None:
    """Return the user slug if username + password are valid, else None.

    Username is the slug ("azra" / "berrin"), case-insensitive.
    """
    slug = (username or "").strip().lower()
    if slug in USERS and verify(slug, password):
        return slug
    return None

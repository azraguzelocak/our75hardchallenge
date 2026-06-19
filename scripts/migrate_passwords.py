"""One-off: convert plaintext AZRA_PASSWORD / BERRIN_PASSWORD in .env to
salted bcrypt hashes (AZRA_PASSWORD_HASH / BERRIN_PASSWORD_HASH), and remove
the plaintext lines.

Safe to run once. It verifies each hash matches the original password before
writing, and leaves every other line in .env untouched.

    python -m scripts.migrate_passwords
"""

from __future__ import annotations

import re
from pathlib import Path

import bcrypt

from shared.auth import hash_password

ENV = Path(__file__).resolve().parent.parent / ".env"
_PATTERN = re.compile(r"^\s*(AZRA|BERRIN)_PASSWORD\s*=\s*(.+?)\s*$")


def main() -> None:
    if not ENV.exists():
        print("No .env found — nothing to migrate.")
        return

    lines = ENV.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    migrated: list[str] = []

    for line in lines:
        stripped = line.strip()
        m = _PATTERN.match(line)
        # Only match real plaintext password lines (not *_PASSWORD_HASH, not comments).
        if m and not stripped.startswith("#") and "PASSWORD_HASH" not in line:
            slug = m.group(1)
            plaintext = m.group(2).strip().strip('"').strip("'")
            h = hash_password(plaintext)
            assert bcrypt.checkpw(plaintext.encode(), h.encode())  # sanity check
            out.append(f"{slug}_PASSWORD_HASH='{h}'")
            migrated.append(slug)
        else:
            out.append(line)

    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    if migrated:
        print("Migrated to hashed passwords:", ", ".join(sorted(migrated)))
        print("Plaintext password lines removed from .env.")
    else:
        print("No plaintext AZRA_PASSWORD / BERRIN_PASSWORD lines found.")


if __name__ == "__main__":
    main()

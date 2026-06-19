"""Generate a bcrypt password hash for a dashboard user.

Usage:
    python -m scripts.set_password azra

It asks for a password (hidden) and prints the line to add to your .env
(or to Streamlit secrets). Only the hash is stored — never the plaintext.
"""

from __future__ import annotations

import getpass
import sys

from shared.auth import hash_password
from shared.config import USERS


def main() -> None:
    slug = (sys.argv[1] if len(sys.argv) > 1 else input("user (azra/berrin): ")).strip().lower()
    if slug not in USERS:
        print(f"Unknown user '{slug}'. Choose one of: {', '.join(USERS)}")
        return

    pw = getpass.getpass("password: ")
    if not pw:
        print("Empty password — aborted.")
        return
    if pw != getpass.getpass("confirm:  "):
        print("Passwords don't match — aborted.")
        return

    h = hash_password(pw)
    print("\nAdd this line to your .env (or Streamlit secrets):\n")
    print(f"{slug.upper()}_PASSWORD_HASH='{h}'")


if __name__ == "__main__":
    main()

"""Seed / refresh the two users and their task sets in Supabase.

Run once after creating the database schema (and any time you change the task
sets in shared/config.py):

    python -m scripts.seed
"""

from __future__ import annotations

from shared.db import sync_users_and_tasks


def main() -> None:
    print("Syncing users and tasks into Supabase...")
    slug_to_id = sync_users_and_tasks()
    for slug, user_id in slug_to_id.items():
        print(f"  {slug}: {user_id}")
    print("Done.")


if __name__ == "__main__":
    main()

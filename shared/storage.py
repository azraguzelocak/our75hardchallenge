"""Supabase Storage helpers for meal + progress photos.

Photos are stored as private objects; we keep only the object path in the
database (never bytes, never a public URL). Uploads are best-effort: if the
bucket isn't set up yet, we log a warning and return None so logging still
works.
"""

from __future__ import annotations

import logging
import os

from shared.db import get_client

log = logging.getLogger("shared.storage")


def _bucket() -> str:
    return os.getenv("SUPABASE_PHOTO_BUCKET", "photos")


def upload_photo(image_bytes: bytes, dest_path: str,
                 content_type: str = "image/jpeg") -> str | None:
    """Upload bytes to the photo bucket. Returns the object path, or None on failure."""
    bucket = _bucket()
    try:
        get_client().storage.from_(bucket).upload(
            path=dest_path,
            file=image_bytes,
            file_options={"content-type": content_type, "upsert": "true"},
        )
        return dest_path
    except Exception as exc:  # noqa: BLE001 - best effort, never block logging
        log.warning("Photo upload failed (%s). Storing without photo_path.", exc)
        return None


def signed_url(object_path: str, expires_in: int = 3600) -> str | None:
    """Create a short-lived signed URL for a stored object (used by the dashboard)."""
    bucket = _bucket()
    try:
        result = get_client().storage.from_(bucket).create_signed_url(object_path, expires_in)
        return result.get("signedURL") or result.get("signedUrl")
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not sign url for %s (%s).", object_path, exc)
        return None

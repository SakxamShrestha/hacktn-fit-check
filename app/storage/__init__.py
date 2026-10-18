"""Object storage for donor uploads.

Everything here is best-effort by design: a photo that cannot be stored or
read back is reported as absent, never as an error, so a storage outage can
never block a donation.
"""

from app.storage.photos import MAX_PHOTO_BYTES, fetch_photo, discard_photo, store_photo

__all__ = [
    "MAX_PHOTO_BYTES",
    "fetch_photo",
    "store_photo",
]

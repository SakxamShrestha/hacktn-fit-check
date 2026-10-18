"""Donor device photographs, kept in a private OCI Object Storage bucket.

Written the way app/reference/device_registry.py is written: this module
reaches the network, and nothing here ever raises. Every failure path returns
None and logs the reason, so a donor posting an offer succeeds with no network,
with wrong credentials, with a deleted bucket, or through an OCI outage -- the
photo is simply absent. A storage failure must never cost us a donation.

The bucket is private (NoPublicAccess), so a stored key is read back through
fetch_photo rather than linked to directly.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.config import SETTINGS

logger = logging.getLogger(__name__)

# Deliberately larger than app.ai.extract.MAX_IMAGE_BYTES: that one bounds what
# we are willing to send to the vision model, this one bounds what we are
# willing to keep. They are different limits for different purposes.
MAX_PHOTO_BYTES = 8 * 1024 * 1024

IMAGE_PREFIX = "image/"
DEFAULT_CONTENT_TYPE = "image/jpeg"
DEFAULT_EXTENSION = "jpg"
CONTENT_TYPE_SEPARATOR = ";"

EXTENSION_BY_CONTENT_TYPE = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/heic": "heic",
    "image/heif": "heif",
    "image/tiff": "tiff",
    "image/bmp": "bmp",
}

# Built on first use, never at import time: importing this module must not
# touch the network or need credentials to exist.
_client: Any = None


def _object_storage_client() -> Any | None:
    """The shared Object Storage client, built lazily. None if it cannot be.

    Missing credentials are as ordinary as a missing network here, so this
    reports them the same way: a warning and None.
    """
    global _client
    if _client is not None:
        return _client
    try:
        import oci
        from oci.object_storage import ObjectStorageClient

        config = oci.config.from_file(SETTINGS.oci_config_path, SETTINGS.oci_profile)
        oci.config.validate_config(config)
        _client = ObjectStorageClient(config, timeout=SETTINGS.timeout)
    except Exception as exc:  # noqa: BLE001 - no client is a missing photo, not a crash
        logger.warning("Object Storage client unavailable (%s)", exc)
        return None
    return _client


def _media_type(content_type: str) -> str:
    """Normalise an uploaded content type; anything not image/* becomes JPEG."""
    cleaned = (content_type or "").split(CONTENT_TYPE_SEPARATOR)[0].strip().lower()
    if cleaned.startswith(IMAGE_PREFIX) and cleaned != IMAGE_PREFIX:
        return cleaned
    logger.debug("Coercing content type %r to %s", content_type, DEFAULT_CONTENT_TYPE)
    return DEFAULT_CONTENT_TYPE


def _extension(media_type: str) -> str:
    """A sane file extension for the key, so the bucket stays browsable."""
    return EXTENSION_BY_CONTENT_TYPE.get(media_type, DEFAULT_EXTENSION)


def _content_type_of(response: Any) -> str:
    """The content type the service reported, or JPEG when it reported none."""
    headers = getattr(response, "headers", None)
    get_header = getattr(headers, "get", None)
    if get_header is None:
        return DEFAULT_CONTENT_TYPE
    return _media_type(get_header("content-type", "") or "")


def store_photo(raw: bytes, content_type: str) -> str | None:
    """Upload one photo and return its object key, or None if it was not stored.

    Never raises. The caller treats None as "this offer has no photo" and
    carries on.
    """
    if not raw:
        logger.warning("Photo not stored: the upload was empty.")
        return None
    if len(raw) > MAX_PHOTO_BYTES:
        logger.warning(
            "Photo not stored: %d bytes is over the %d byte limit.",
            len(raw),
            MAX_PHOTO_BYTES,
        )
        return None

    client = _object_storage_client()
    if client is None:
        logger.warning("Photo not stored: no Object Storage client.")
        return None

    media_type = _media_type(content_type)
    key = f"{uuid.uuid4().hex}.{_extension(media_type)}"
    try:
        client.put_object(
            namespace_name=SETTINGS.object_storage_namespace,
            bucket_name=SETTINGS.object_storage_bucket,
            object_name=key,
            put_object_body=bytes(raw),
            content_type=media_type,
        )
    except Exception as exc:  # noqa: BLE001 - a photo is never worth a failed donation
        logger.warning("Photo %s not stored (%s)", key, exc)
        return None
    return key


def discard_photo(key: str) -> bool:
    """Delete a stored photograph. True when it is gone, False otherwise.

    Used when an offer fails to save after its photograph was already
    uploaded, so a failed donation does not leave an object in the bucket
    that nothing will ever reference. Like everything else here it never
    raises: a leaked object is a tidiness problem, not a reason to turn a
    failed save into a crash.
    """
    if not key.strip():
        return False

    client = _object_storage_client()
    if client is None:
        LOGGER.warning("cannot discard photo %s: no object storage client", key)
        return False

    try:
        client.delete_object(
            SETTINGS.object_storage_namespace, SETTINGS.object_storage_bucket, key
        )
    except Exception as exc:  # noqa: BLE001 - a leaked object must not raise
        LOGGER.warning("could not discard photo %s: %s", key, exc)
        return False
    return True


def fetch_photo(key: str) -> tuple[bytes, str] | None:
    """Read one photo back as (bytes, content type). None when it is not there.

    Never raises: a missing object, a dead link and a bad key are all one
    answer, which is that there is no photo to show.
    """
    object_name = (key or "").strip()
    if not object_name:
        logger.warning("Photo not fetched: no object key given.")
        return None

    client = _object_storage_client()
    if client is None:
        logger.warning("Photo %s not fetched: no Object Storage client.", object_name)
        return None

    try:
        response = client.get_object(
            namespace_name=SETTINGS.object_storage_namespace,
            bucket_name=SETTINGS.object_storage_bucket,
            object_name=object_name,
        )
        body = response.data.content
    except Exception as exc:  # noqa: BLE001 - a missing photo is not an error page
        logger.warning("Photo %s could not be read (%s)", object_name, exc)
        return None

    if not body:
        logger.warning("Photo %s came back empty.", object_name)
        return None
    return (bytes(body), _content_type_of(response))

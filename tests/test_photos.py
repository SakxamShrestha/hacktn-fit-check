"""Tests for donor photo storage.

No test here touches the network: every one of them swaps the client factory
for an in-memory fake, so a laptop with no OCI credentials runs them all.
"""

from __future__ import annotations

import importlib
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.config import SETTINGS
from app.storage import photos

PNG_BYTES = b"\x89PNG\r\n\x1a\n fake pixels"


class ObjectNotFound(Exception):
    """What the SDK raises for a missing object, in the shape we care about."""


class FakeObjectStorage:
    """An in-memory stand-in for oci.object_storage.ObjectStorageClient."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.calls: tuple[dict[str, str], ...] = ()

    def put_object(
        self,
        *,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
        put_object_body: bytes,
        content_type: str,
        **_: object,
    ) -> SimpleNamespace:
        self.calls = (
            *self.calls,
            {"namespace": namespace_name, "bucket": bucket_name, "key": object_name},
        )
        self.objects = {
            **self.objects,
            object_name: (bytes(put_object_body), content_type),
        }
        return SimpleNamespace(status=200)

    def get_object(
        self, *, namespace_name: str, bucket_name: str, object_name: str, **_: object
    ) -> SimpleNamespace:
        self.calls = (
            *self.calls,
            {"namespace": namespace_name, "bucket": bucket_name, "key": object_name},
        )
        stored = self.objects.get(object_name)
        if stored is None:
            raise ObjectNotFound(f"404 no such object {object_name}")
        body, content_type = stored
        return SimpleNamespace(
            data=SimpleNamespace(content=body),
            headers={"content-type": content_type},
        )


class BrokenObjectStorage:
    """Every call blows up, the way an outage or a deleted bucket does."""

    def put_object(self, **_: object) -> SimpleNamespace:
        raise RuntimeError("ServiceError: BucketNotFound")

    def get_object(self, **_: object) -> SimpleNamespace:
        raise RuntimeError("ServiceError: BucketNotFound")


@pytest.fixture(autouse=True)
def _clear_client_cache():
    """The lazily built client is module state, so no test may inherit it."""
    photos._client = None
    yield
    photos._client = None


@pytest.fixture
def fake_storage(monkeypatch) -> FakeObjectStorage:
    """Swap the client factory for a fake. Nothing leaves the process."""
    fake = FakeObjectStorage()
    monkeypatch.setattr(photos, "_object_storage_client", lambda: fake)
    return fake


def test_a_stored_photo_comes_back_byte_for_byte(fake_storage):
    # Arrange / Act
    key = photos.store_photo(PNG_BYTES, "image/png")
    fetched = photos.fetch_photo(key)

    # Assert
    assert key is not None
    assert fetched == (PNG_BYTES, "image/png")


def test_the_key_keeps_an_extension_matching_the_image_type(fake_storage):
    # Act
    key = photos.store_photo(PNG_BYTES, "image/png")

    # Assert
    assert key.endswith(".png")


def test_the_upload_goes_to_the_bucket_named_in_settings(fake_storage):
    # Act
    photos.store_photo(PNG_BYTES, "image/png")

    # Assert
    assert fake_storage.calls[0]["namespace"] == SETTINGS.object_storage_namespace
    assert fake_storage.calls[0]["bucket"] == SETTINGS.object_storage_bucket


def test_an_empty_upload_is_refused_and_says_why(fake_storage, caplog):
    # Act
    with caplog.at_level(logging.WARNING):
        key = photos.store_photo(b"", "image/png")

    # Assert
    assert key is None
    assert fake_storage.calls == ()
    assert caplog.records and caplog.records[0].levelno == logging.WARNING


def test_an_upload_over_the_size_limit_is_refused(fake_storage):
    # Arrange: one byte past the ceiling.
    oversized = b"\x00" * (photos.MAX_PHOTO_BYTES + 1)

    # Act
    key = photos.store_photo(oversized, "image/jpeg")

    # Assert
    assert key is None
    assert fake_storage.calls == ()


def test_an_upload_exactly_at_the_size_limit_is_accepted(fake_storage):
    # Arrange
    at_limit = b"\x00" * photos.MAX_PHOTO_BYTES

    # Act
    key = photos.store_photo(at_limit, "image/jpeg")

    # Assert
    assert key is not None


def test_a_non_image_content_type_is_coerced_to_jpeg(fake_storage):
    # Act: a browser that sent application/octet-stream must not poison the bucket.
    key = photos.store_photo(PNG_BYTES, "application/octet-stream")

    # Assert
    assert key.endswith(".jpg")
    assert fake_storage.objects[key] == (PNG_BYTES, "image/jpeg")


def test_a_content_type_with_parameters_keeps_its_media_type(fake_storage):
    # Act
    key = photos.store_photo(PNG_BYTES, "image/PNG; charset=binary")

    # Assert
    assert fake_storage.objects[key][1] == "image/png"


def test_a_client_that_raises_on_upload_returns_none_rather_than_propagating(
    monkeypatch,
):
    # Arrange
    monkeypatch.setattr(photos, "_object_storage_client", lambda: BrokenObjectStorage())

    # Act
    key = photos.store_photo(PNG_BYTES, "image/png")

    # Assert
    assert key is None


def test_a_client_that_raises_on_fetch_returns_none_rather_than_propagating(
    monkeypatch,
):
    # Arrange
    monkeypatch.setattr(photos, "_object_storage_client", lambda: BrokenObjectStorage())

    # Act
    found = photos.fetch_photo("whatever.png")

    # Assert
    assert found is None


def test_an_unbuildable_client_is_a_missing_photo_not_an_error(monkeypatch):
    # Arrange: no credentials, no network -- the factory gives up and returns None.
    monkeypatch.setattr(photos, "_object_storage_client", lambda: None)

    # Act / Assert
    assert photos.store_photo(PNG_BYTES, "image/png") is None
    assert photos.fetch_photo("whatever.png") is None


def test_fetching_an_unknown_key_returns_none(fake_storage):
    # Act
    found = photos.fetch_photo("0123456789abcdef.png")

    # Assert
    assert found is None


def test_fetching_without_a_key_returns_none(fake_storage):
    # Act / Assert
    assert photos.fetch_photo("") is None
    assert fake_storage.calls == ()


def test_the_built_client_is_cached_for_later_calls():
    # Arrange: pretend the first build already happened.
    fake = FakeObjectStorage()
    photos._client = fake

    # Act / Assert: a second caller reuses it instead of rebuilding.
    assert photos._object_storage_client() is fake


def test_importing_the_module_constructs_no_client():
    # Arrange / Act: a fresh import, with client construction made to fail loudly.
    with patch("oci.config.from_file", side_effect=AssertionError) as from_file:
        reimported = importlib.reload(photos)

    # Assert
    assert from_file.call_count == 0
    assert reimported._client is None

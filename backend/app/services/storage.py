"""Receipt object storage (AGENTS.md §3.5).

The service layer (:mod:`app.services.expenses`) talks to this module only —
it never touches a filesystem path or an SDK client directly. Two backends,
selected by ``STORAGE_BACKEND``:

* ``s3`` — an S3-compatible object store (the compose default: LocalStack;
  production: AWS S3). ``boto3`` is synchronous, so every SDK call runs in
  ``asyncio.to_thread`` to keep the event loop free.
* ``local`` — plain disk under ``UPLOAD_DIR`` (the pre-S3 behaviour; the
  default for bare dev and the test suite).

The object key is the ``attachments.filename`` value (``<uuid4-hex>.<ext>``) —
no schema change. ``ready()`` is called once from the app lifespan: for S3 it
creates the bucket (idempotent), for local it is a no-op (the upload dir is
created lazily on the first put, as before).
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Protocol

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.core.errors import NotFound


class ObjectStore(Protocol):
    """Byte storage for receipt files, addressed by object key."""

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store the bytes under ``key``, replacing any existing object."""

    async def get(self, key: str) -> bytes:
        """Return the bytes for ``key``; raise ``NotFound`` when absent."""

    async def delete(self, key: str) -> None:
        """Remove the object; a missing key is not an error (idempotent)."""

    async def ready(self) -> None:
        """Make the backend usable; called once at app start (idempotent)."""


class LocalStore:
    """On-disk backend rooted at ``settings.upload_dir`` (the v1 behaviour)."""

    def __init__(self) -> None:
        self._dir = Path(get_settings().upload_dir)

    async def ready(self) -> None:
        # The dir is created lazily on the first put, as before; a no-op here
        # keeps bare-dev boots (and tests) from touching the default path.
        return None

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        del content_type  # disk keeps no metadata; the DB row is authoritative
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / key).write_bytes(data)

    async def get(self, key: str) -> bytes:
        path = self._dir / key
        if not path.is_file():
            raise NotFound("The receipt file is missing on disk")
        return path.read_bytes()

    async def delete(self, key: str) -> None:
        # Already gone (e.g. volume wiped) is not an error — the row is gone anyway.
        with contextlib.suppress(OSError):
            (self._dir / key).unlink()


class S3Store:
    """S3-compatible backend (LocalStack locally, AWS S3 in production)."""

    def __init__(self) -> None:
        settings = get_settings()
        self._bucket = settings.s3_bucket
        kwargs: dict[str, str] = {"region_name": settings.s3_region}
        if settings.s3_endpoint_url:
            kwargs["endpoint_url"] = settings.s3_endpoint_url
        # Empty credentials fall through to boto3's own resolution chain
        # (AWS_* env vars, IAM / instance roles) for real-AWS deployments.
        if settings.s3_access_key_id:
            kwargs["aws_access_key_id"] = settings.s3_access_key_id
        if settings.s3_secret_access_key:
            kwargs["aws_secret_access_key"] = settings.s3_secret_access_key
        # Thread-safe; created once per process (see get_store below).
        self._client: BaseClient = boto3.client("s3", **kwargs)

    async def ready(self) -> None:
        await asyncio.to_thread(self._create_bucket)

    def _create_bucket(self) -> None:
        settings = get_settings()
        try:
            if settings.s3_region == "us-east-1":
                self._client.create_bucket(Bucket=self._bucket)
            else:
                self._client.create_bucket(
                    Bucket=self._bucket,
                    CreateBucketConfiguration={"LocationConstraint": settings.s3_region},
                )
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            # Already exists (possibly owned by us) — idempotent start.
            if code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                raise

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(self._put, key, data, content_type)

    def _put(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(
            Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
        )

    async def get(self, key: str) -> bytes:
        try:
            return await asyncio.to_thread(self._get, key)
        except ClientError as exc:
            if _is_missing(exc):
                raise NotFound("The receipt object is missing from the store") from exc
            raise

    def _get(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body: bytes = response["Body"].read()
        return body

    async def delete(self, key: str) -> None:
        # S3 DELETE is idempotent: no error when the key is already gone.
        await asyncio.to_thread(self._delete, key)

    def _delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)


def _is_missing(exc: ClientError) -> bool:
    code = str(exc.response.get("Error", {}).get("Code", ""))
    return code in {"NoSuchKey", "404"}


_store: ObjectStore | None = None


def get_store() -> ObjectStore:
    """The configured backend, cached for the process lifetime.

    Tests that flip ``STORAGE_BACKEND`` between apps call :func:`reset_store`
    alongside their ``get_settings.cache_clear()``.
    """
    global _store
    if _store is None:
        _store = S3Store() if get_settings().storage_backend == "s3" else LocalStore()
    return _store


def reset_store() -> None:
    """Drop the cached backend (test helper)."""
    global _store
    _store = None

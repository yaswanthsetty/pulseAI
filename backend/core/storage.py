"""Object storage abstraction (spec §10 ``content_ref`` / §25 Supabase storage).

Article bodies are stored out-of-line — Postgres keeps metadata plus a short
preview, and the full text lives in object storage referenced by ``content_ref``.

``LocalObjectStorage`` is the default for development (a directory on disk).
``S3ObjectStorage`` targets S3-compatible services (Supabase Storage, MinIO,
AWS S3) and is enabled with ``STORAGE_BACKEND=s3`` plus ``boto3`` installed
(``pip install pulseai[storage-s3]``).
"""

import logging
from pathlib import Path
from typing import Protocol

from backend.core.config import settings

logger = logging.getLogger(__name__)


class ObjectStorage(Protocol):
    """Minimal interface every backend must implement."""

    def put(self, key: str, data: bytes) -> str: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


def _normalize_key(key: str) -> str:
    """Object-storage keys are always "/"-separated; map Windows separators."""
    return key.replace("\\", "/")


def _validate_key(key: str) -> None:
    """Reject keys that could escape the storage root (path traversal).

    Backslashes are normalized to "/" first so the check behaves identically
    on every OS — on POSIX a "\\" is a literal filename character and would
    otherwise silently bypass traversal detection (GitHub Actions CI caught
    this: ``get("..\\escape.txt")`` raised FileNotFoundError, not ValueError).
    """
    normalized = _normalize_key(key)
    if not normalized or normalized.startswith("/") or ".." in Path(normalized).parts:
        raise ValueError(f"invalid storage key: {key!r}")


class LocalObjectStorage:
    """Filesystem-backed storage rooted at ``settings.storage_local_dir``."""

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or settings.storage_local_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        _validate_key(key)
        # Join with the normalized form so a key is resolved identically on
        # every platform (e.g. "articles\\a.txt" == "articles/a.txt").
        return self.root / _normalize_key(key)

    def put(self, key: str, data: bytes) -> str:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.exists():
            raise FileNotFoundError(f"object not found: {key}")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self._resolve(key)
        if path.exists():
            path.unlink()

    def exists(self, key: str) -> bool:
        return self._resolve(key).exists()


class S3ObjectStorage:
    """S3-compatible storage (boto3 loaded lazily so it is not a hard dep)."""

    def __init__(self, bucket: str, region: str | None = None) -> None:
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "boto3 is required for STORAGE_BACKEND=s3; "
                "install with `pip install pulseai[storage-s3]`"
            ) from exc
        self.bucket = bucket
        self.client = boto3.client("s3", region_name=region)

    def put(self, key: str, data: bytes) -> str:
        _validate_key(key)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return key

    def get(self, key: str) -> bytes:
        _validate_key(key)
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def delete(self, key: str) -> None:
        _validate_key(key)
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        _validate_key(key)
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    """Return the configured storage backend (cached singleton)."""
    global _storage
    if _storage is None:
        if settings.storage_backend == "s3":
            if not settings.storage_s3_bucket:
                raise RuntimeError("STORAGE_S3_BUCKET is required when STORAGE_BACKEND=s3")
            _storage = S3ObjectStorage(settings.storage_s3_bucket, settings.storage_s3_region)
        else:
            _storage = LocalObjectStorage()
        logger.info("object storage backend: %s", settings.storage_backend)
    return _storage


def reset_storage_cache() -> None:  # test helper
    global _storage
    _storage = None


# Re-export so callers can clean up their own namespace if needed.
__all__ = [
    "ObjectStorage",
    "LocalObjectStorage",
    "S3ObjectStorage",
    "get_storage",
    "reset_storage_cache",
]

"""Object storage providers (OAIS preservation store).

``minio`` in Docker / institutional deployments, ``filesystem`` for a single
edge machine with no object server. Both write content-addressed, checksummed
objects and both are stream-friendly. Keys are sanitised so a caller can never
escape the bucket or the storage root (path-traversal protection).
"""

from __future__ import annotations

import hashlib
import io
import os
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.base import ObjectStore, ProviderUnavailable, StoredObject

log = get_logger(__name__)

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")
_ARCHIVE_DATE = datetime.now(UTC).strftime("%Y/%m")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_key(key: str) -> str:
    """Reject traversal and normalise separators. Raises on hostile input."""
    if not key or key.strip() == "":
        raise ValueError("Object key must not be empty")
    normalised = key.replace("\\", "/")
    parts: list[str] = []
    for raw in normalised.split("/"):
        if raw in ("", "."):
            continue
        if ".." in raw:
            raise ValueError(f"Illegal object key segment: {raw!r}")
        seg = _SAFE_SEGMENT.sub("_", raw).strip("._-")
        if not seg:
            raise ValueError(f"Illegal object key segment: {raw!r}")
        parts.append(seg[:120])
    if not parts:
        raise ValueError(f"Illegal object key: {key!r}")
    return "/".join(parts)


def build_key(kind: str, identifier: str, filename: str | None = None) -> str:
    parts = [_ARCHIVE_DATE, kind, identifier]
    if filename:
        parts.append(safe_key(filename))
    return "/".join(p for p in parts if p)


class FilesystemObjectStore:
    """Content stored under ``local_storage_path`` preserving the key layout."""

    name = "filesystem"

    def __init__(self, root: Path | None = None, public_prefix: str = "/api/v1/files") -> None:
        self.root = Path(root or settings.storage_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.public_prefix = public_prefix

    def is_available(self) -> bool:
        return os.access(self.root, os.W_OK)

    def _path(self, key: str) -> Path:
        path = (self.root / safe_key(key)).resolve()
        root = self.root.resolve()
        if not str(path).startswith(str(root)):
            raise ValueError("Resolved object path escapes the storage root")
        return path

    def put(self, key: str, data: bytes, content_type: str | None = None) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        digest = sha256_bytes(data)
        if content_type:
            sidecar = path.with_suffix(path.suffix + ".meta")
            sidecar.write_text(content_type, encoding="utf-8")
        stat = path.stat()
        return StoredObject(
            key=key,
            size=stat.st_size,
            content_type=content_type or "application/octet-stream",
            sha256=digest,
            etag=digest[:32],
            last_modified=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            stream_url=f"{self.public_prefix}/{key}",
        )

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def open_stream(self, key: str) -> BinaryIO:
        return open(self._path(key), "rb")  # noqa: SIM115

    def exists(self, key: str) -> bool:
        try:
            return self._path(key).is_file()
        except ValueError:
            return False

    def delete(self, key: str) -> bool:
        try:
            path = self._path(key)
        except ValueError:
            return False
        if path.is_file():
            path.unlink()
            return True
        return False

    def stat(self, key: str) -> StoredObject | None:
        try:
            path = self._path(key)
        except ValueError:
            return None
        if not path.is_file():
            return None
        stat = path.stat()
        ctype = "application/octet-stream"
        sidecar = path.with_suffix(path.suffix + ".meta")
        if sidecar.is_file():
            ctype = sidecar.read_text(encoding="utf-8").strip() or ctype
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(block)
        return StoredObject(
            key=key,
            size=stat.st_size,
            content_type=ctype,
            sha256=digest.hexdigest(),
            etag=digest.hexdigest()[:32],
            last_modified=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            stream_url=f"{self.public_prefix}/{key}",
        )

    def url_for(self, key: str, expires_seconds: int = 900) -> str:
        return f"{self.public_prefix}/{safe_key(key)}"

    def usage(self) -> dict[str, Any]:
        total = 0
        count = 0
        for dirpath, _dirnames, filenames in os.walk(self.root):
            for name in filenames:
                if name.endswith(".meta") or name.endswith(".part"):
                    continue
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                    count += 1
                except OSError:  # pragma: no cover
                    continue
        return {"backend": self.name, "objects": count, "bytes": total, "root": str(self.root)}


class MinioObjectStore:
    name = "minio"

    def __init__(self) -> None:
        self._client: Any | None = None
        self._error: str | None = None
        self.bucket = settings.minio_bucket

    def _http_client(self) -> Any:
        """A deliberately impatient HTTP client.

        The default urllib3 policy retries five times, so a missing MinIO would
        add several seconds to every start-up and health check. The preservation
        store falls back to the filesystem anyway, so probing should be quick.
        """
        import urllib3
        from minio import Minio  # noqa: F401  (ensures minio is installed)

        return urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=1.0, read=5.0),
            retries=urllib3.Retry(total=1, connect=1, read=1, status=0, backoff_factor=0.1),
        )

    def _ensure(self) -> Any:
        if self._client is not None:
            return self._client
        if self._error:
            raise ProviderUnavailable(self._error)
        try:
            from minio import Minio

            client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
                http_client=self._http_client(),
            )
            if not client.bucket_exists(self.bucket):
                client.make_bucket(self.bucket)
            self._client = client
            return client
        except Exception as exc:  # noqa: BLE001
            self._error = f"MinIO unavailable at {settings.minio_endpoint}: {exc}"
            raise ProviderUnavailable(self._error) from exc

    def is_available(self) -> bool:
        try:
            self._ensure()
            return True
        except ProviderUnavailable:
            return False

    def put(self, key: str, data: bytes, content_type: str | None = None) -> StoredObject:
        client = self._ensure()
        key = safe_key(key)
        client.put_object(
            self.bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type or "application/octet-stream",
        )
        stat = client.stat_object(self.bucket, key)
        return StoredObject(
            key=key,
            size=int(stat.size or len(data)),
            content_type=content_type or "application/octet-stream",
            sha256=sha256_bytes(data),
            etag=(stat.etag or "").strip('"'),
            last_modified=stat.last_modified.isoformat() if stat.last_modified else None,
            stream_url=self.url_for(key),
        )

    def get(self, key: str) -> bytes:
        response = self._ensure().get_object(self.bucket, safe_key(key))
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def open_stream(self, key: str) -> BinaryIO:
        return io.BytesIO(self.get(key))

    def exists(self, key: str) -> bool:
        try:
            self._ensure().stat_object(self.bucket, safe_key(key))
            return True
        except Exception:  # noqa: BLE001
            return False

    def delete(self, key: str) -> bool:
        try:
            self._ensure().remove_object(self.bucket, safe_key(key))
            return True
        except Exception:  # noqa: BLE001
            return False

    def stat(self, key: str) -> StoredObject | None:
        try:
            st = self._ensure().stat_object(self.bucket, safe_key(key))
        except Exception:  # noqa: BLE001
            return None
        return StoredObject(
            key=key,
            size=int(st.size or 0),
            content_type=st.content_type or "application/octet-stream",
            sha256="",
            etag=(st.etag or "").strip('"'),
            last_modified=st.last_modified.isoformat() if st.last_modified else None,
            stream_url=self.url_for(key),
        )

    def url_for(self, key: str, expires_seconds: int = 900) -> str:
        from datetime import timedelta

        return self._ensure().presigned_get_object(
            self.bucket, safe_key(key), expires=timedelta(seconds=expires_seconds)
        )

    def usage(self) -> dict[str, Any]:
        try:
            client = self._ensure()
            total = 0
            count = 0
            for obj in client.list_objects(self.bucket, recursive=True):
                total += int(obj.size or 0)
                count += 1
            return {"backend": self.name, "objects": count, "bytes": total, "root": self.bucket}
        except ProviderUnavailable as exc:
            raise


@lru_cache(maxsize=1)
def get_object_store() -> ObjectStore:
    choice = settings.storage_backend
    if choice == "minio":
        return MinioObjectStore()
    if choice == "filesystem":
        return FilesystemObjectStore()
    minio = MinioObjectStore()
    if minio.is_available():
        return minio
    log.info("MinIO not reachable; using filesystem preservation store", root=settings.storage_root)
    return FilesystemObjectStore()


def storage_capability() -> dict[str, Any]:
    store = get_object_store()
    info: dict[str, Any] = {"available": True, "provider": store.name}
    usage = getattr(store, "usage", None)
    if callable(usage):
        try:
            info.update(usage())
        except Exception as exc:  # noqa: BLE001
            info["usage_error"] = str(exc)[:160]
    return info


def reset_store_cache() -> None:
    """Drop the memoised store so configuration changes take effect."""
    get_object_store.cache_clear()

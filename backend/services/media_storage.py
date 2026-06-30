"""Media storage abstraction for Sentinel managed upload pipeline.

Supports local disk storage with optional MongoDB metadata persistence.
Does not store raw image bytes in MongoDB.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Protocol
from uuid import uuid4

from models.media import MediaTelemetry, StorageMode, StoredMedia


class MediaStorage(Protocol):
    async def save(
        self,
        file_path: str,
        mime_type: str,
        extension: str,
        size_bytes: int,
        sha256: str,
        telemetry: Optional[MediaTelemetry],
        original_filename: str,
    ) -> StoredMedia: ...

    async def get(self, media_id: str) -> Optional[StoredMedia]: ...
    async def delete(self, media_id: str) -> bool: ...


class _InMemoryMediaStore:
    """Thread-safe in-memory metadata store with asyncio.Lock."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._meta: Dict[str, dict] = {}

    async def get(self, media_id: str) -> Optional[dict]:
        async with self._lock:
            return deepcopy(self._meta.get(media_id))

    async def set(self, media_id: str, doc: dict) -> None:
        async with self._lock:
            self._meta[media_id] = deepcopy(doc)

    async def delete(self, media_id: str) -> bool:
        async with self._lock:
            if media_id in self._meta:
                del self._meta[media_id]
                return True
            return False


# Resolve default media directory relative to this module
BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MEDIA_DIR = BACKEND_DIR / "data" / "media"


class LocalMediaStorage:
    """Stores media files on disk and metadata in MongoDB or memory fallback.

    Configurable via environment:
      SENTINEL_MEDIA_DIR  — base directory for media files (default: backend/data/media)
      SENTINEL_MEDIA_MAX_BYTES  — max upload size (default: 10 MiB)
    """

    def __init__(
        self,
        db: Any,
        mongo_reachable: bool,
        media_dir: Optional[str] = None,
        max_bytes: Optional[int] = None,
    ) -> None:
        self._db = db
        self._mongo_reachable = mongo_reachable
        self._mode = "mongo" if mongo_reachable else "memory"
        self._memory_meta = _InMemoryMediaStore()
        self._collection_name = "media"
        raw_dir = media_dir or os.environ.get("SENTINEL_MEDIA_DIR")
        if raw_dir:
            self._media_dir = Path(raw_dir)
        else:
            self._media_dir = DEFAULT_MEDIA_DIR
        self._max_bytes = max_bytes or int(os.environ.get("SENTINEL_MEDIA_MAX_BYTES", "10_485_760"))
        # Ensure directory exists
        self._media_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def media_dir(self) -> Path:
        return self._media_dir

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _collection(self):
        if self._mode == "mongo":
            return self._db[self._collection_name]
        return self._memory_meta

    def _generate_media_id(self) -> str:
        return f"media-{uuid4().hex}"

    def _generate_filename(self, media_id: str, extension: str) -> str:
        ext = extension.lstrip(".")
        return f"{media_id}.{ext}"

    def _safe_path(self, filename: str) -> Path:
        """Prevent path traversal by resolving within media_dir."""
        target = self._media_dir / filename
        try:
            target.resolve().relative_to(self._media_dir.resolve())
        except ValueError:
            raise ValueError("Invalid filename: path traversal detected")
        return target

    # ------------------------------------------------------------------
    # Storage operations
    # ------------------------------------------------------------------

    async def save(
        self,
        file_path: str,
        mime_type: str,
        extension: str,
        size_bytes: int,
        sha256: str,
        telemetry: Optional[MediaTelemetry],
        original_filename: str,
    ) -> StoredMedia:
        media_id = self._generate_media_id()
        filename = self._generate_filename(media_id, extension)
        dest = self._safe_path(filename)
        dest_created = False

        loop = asyncio.get_event_loop()

        # Move staged file to final location
        await loop.run_in_executor(None, shutil.move, file_path, str(dest))
        dest_created = True

        try:
            uri = f"/api/sentinel/media/{media_id}/file"
            now = datetime.now(timezone.utc)

            doc = {
                "media_id": media_id,
                "uri": uri,
                "file_path": str(dest),
                "mime_type": mime_type,
                "extension": extension.lstrip("."),
                "size_bytes": size_bytes,
                "sha256": sha256,
                "storage_mode": StorageMode.managed_upload.value,
                "original_filename": original_filename,
                "telemetry": telemetry.model_dump(by_alias=False) if telemetry else None,
                "created_at": now,
            }

            coll = self._collection()
            if self._mode == "mongo":
                await coll.insert_one(doc)
            else:
                await coll.set(media_id, doc)

            return StoredMedia(**deepcopy(doc))
        except Exception:
            # Rollback: delete final file if metadata persistence failed
            if dest_created and dest.exists():
                try:
                    await loop.run_in_executor(None, os.unlink, str(dest))
                except Exception:
                    pass
            raise

    async def get(self, media_id: str) -> Optional[StoredMedia]:
        coll = self._collection()
        if self._mode == "mongo":
            doc = await coll.find_one({"media_id": media_id}, {"_id": 0})
        else:
            doc = await coll.get(media_id)
        if doc is None:
            return None
        return StoredMedia(**deepcopy(doc))

    async def delete(self, media_id: str) -> bool:
        coll = self._collection()
        if self._mode == "mongo":
            doc = await coll.find_one_and_delete({"media_id": media_id}, {"_id": 0})
        else:
            doc = await coll.get(media_id)
            if doc:
                await coll.delete(media_id)

        if doc is None:
            return False

        # Remove file from disk
        try:
            path = Path(doc["file_path"])
            if path.exists():
                await asyncio.get_event_loop().run_in_executor(None, path.unlink)
        except Exception:
            pass

        return True

    # ------------------------------------------------------------------
    # File validation helpers
    # ------------------------------------------------------------------

    ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
    ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

    MIME_EXTENSION_MAP: Dict[str, set] = {
        "image/jpeg": {".jpg", ".jpeg"},
        "image/png": {".png"},
        "image/webp": {".webp"},
    }

    def validate_file_type(self, mime_type: str, extension: str, file_path: str) -> None:
        """Validate MIME type, extension, file signature, and MIME/extension consistency.

        Raises ValueError with descriptive message on mismatch.
        """
        ext = extension.lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            raise ValueError(f"Unsupported extension: {ext}")
        if mime_type not in self.ALLOWED_MIME_TYPES:
            raise ValueError(f"Unsupported MIME type: {mime_type}")

        # Verify extension matches MIME type
        allowed_exts = self.MIME_EXTENSION_MAP.get(mime_type, set())
        if ext not in allowed_exts:
            raise ValueError(f"Extension {ext} does not match MIME type {mime_type}")

        # Check file signature
        with open(file_path, "rb") as f:
            header = f.read(12)

        if mime_type == "image/jpeg":
            if not header.startswith(b"\xff\xd8\xff"):
                raise ValueError("File signature does not match JPEG")
        elif mime_type == "image/png":
            if not header.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("File signature does not match PNG")
        elif mime_type == "image/webp":
            if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WEBP":
                raise ValueError("File signature does not match WebP")

    # ------------------------------------------------------------------
    # Streaming hash/size calculator
    # ------------------------------------------------------------------

    async def stream_to_temp(self, file_iterator) -> tuple[str, int, str]:
        """Stream upload to a temporary file, return (temp_path, size_bytes, sha256).

        Enforces max_bytes limit while streaming.
        """
        fd, temp_path = tempfile.mkstemp(prefix="sentinel_media_", suffix=".tmp")
        hasher = hashlib.sha256()
        total = 0
        try:
            with os.fdopen(fd, "wb") as f:
                async for chunk in file_iterator:
                    total += len(chunk)
                    if total > self._max_bytes:
                        raise ValueError(f"File exceeds maximum size of {self._max_bytes} bytes")
                    f.write(chunk)
                    hasher.update(chunk)
            return temp_path, total, hasher.hexdigest()
        except Exception:
            # Clean up partial file on failure
            try:
                os.unlink(temp_path)
            except Exception:
                pass
            raise

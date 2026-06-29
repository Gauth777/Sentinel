"""Media service for Sentinel managed upload pipeline.

Validates uploads, normalizes metadata, generates safe IDs, and delegates
storage to a MediaStorage implementation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from models.media import MediaLocation, MediaTelemetry, TelemetrySource
from services.media_storage import LocalMediaStorage

logger = logging.getLogger(__name__)


class MediaServiceError(Exception):
    pass


class MediaService:
    """Orchestrates upload validation, metadata normalization, and storage."""

    def __init__(self, storage: LocalMediaStorage) -> None:
        self._storage = storage

    @property
    def mode(self) -> str:
        return self._storage._mode

    async def upload(
        self,
        file_iterator,
        mime_type: str,
        extension: str,
        original_filename: str,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        heading_degrees: Optional[float] = None,
        speed_kmh: Optional[float] = None,
        captured_at: Optional[str] = None,
        telemetry_source: Optional[str] = None,
    ) -> dict:
        """Validate, store, and return safe API metadata.

        Raises MediaServiceError with descriptive message on validation failure.
        """
        # Stream to temp file with size/hash enforcement
        try:
            temp_path, size_bytes, sha256 = await self._storage.stream_to_temp(file_iterator)
        except ValueError as e:
            raise MediaServiceError(str(e))

        # Validate file type/signature
        try:
            self._storage.validate_file_type(mime_type, extension, temp_path)
        except ValueError as e:
            # Clean up temp file
            try:
                import os
                os.unlink(temp_path)
            except Exception:
                pass
            raise MediaServiceError(str(e))

        # Reject empty files
        if size_bytes == 0:
            try:
                import os
                os.unlink(temp_path)
            except Exception:
                pass
            raise MediaServiceError("Empty file")

        # Build telemetry metadata
        telemetry = None
        if latitude is not None and longitude is not None:
            # Validate numeric telemetry fields explicitly before model creation
            if heading_degrees is not None and (heading_degrees < 0 or heading_degrees >= 360):
                try:
                    import os
                    os.unlink(temp_path)
                except Exception:
                    pass
                raise MediaServiceError("heading_degrees must be between 0 and 360")
            if speed_kmh is not None and speed_kmh < 0:
                try:
                    import os
                    os.unlink(temp_path)
                except Exception:
                    pass
                raise MediaServiceError("speed_kmh must be non-negative")

            try:
                location = MediaLocation(latitude=latitude, longitude=longitude)
            except Exception as e:
                # Clean up temp file
                try:
                    import os
                    os.unlink(temp_path)
                except Exception:
                    pass
                raise MediaServiceError(f"Invalid location: {e}")

            ts = TelemetrySource.unavailable
            if telemetry_source in ("demo", "live", "unavailable"):
                ts = TelemetrySource(telemetry_source)

            cap_dt = None
            if captured_at:
                try:
                    cap_dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
                except Exception:
                    cap_dt = datetime.now(timezone.utc)
            else:
                cap_dt = datetime.now(timezone.utc)

            telemetry = MediaTelemetry(
                location=location,
                heading_degrees=heading_degrees,
                speed_kmh=speed_kmh,
                captured_at=cap_dt,
                telemetry_source=ts,
            )

        # Delegate to storage
        try:
            stored = await self._storage.save(
                file_path=temp_path,
                mime_type=mime_type,
                extension=extension,
                size_bytes=size_bytes,
                sha256=sha256,
                telemetry=telemetry,
                original_filename=original_filename,
            )
        except Exception as e:
            # Clean up temp file if storage failed
            try:
                import os
                os.unlink(temp_path)
            except Exception:
                pass
            raise MediaServiceError(f"Storage failed: {e}")

        return stored.to_api_dict()

    async def get_metadata(self, media_id: str) -> Optional[dict]:
        stored = await self._storage.get(media_id)
        if stored is None:
            return None
        return stored.to_api_dict()

    async def get_file_path(self, media_id: str) -> Optional[str]:
        stored = await self._storage.get(media_id)
        if stored is None:
            return None
        return stored.file_path

    async def delete(self, media_id: str) -> bool:
        return await self._storage.delete(media_id)

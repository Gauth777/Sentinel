"""Media models for Sentinel managed upload pipeline.

Schema version: sentinel.media.v1
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _to_camel(snake: str) -> str:
    parts = snake.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _normalize_utc(dt: Optional[datetime | str]) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class TelemetrySource(str, Enum):
    demo = "demo"
    live = "live"
    unavailable = "unavailable"


class StorageMode(str, Enum):
    managed_upload = "managed_upload"


class MediaLocation(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class MediaTelemetry(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    location: Optional[MediaLocation] = None
    heading_degrees: Optional[float] = Field(default=None, ge=0, lt=360)
    speed_kmh: Optional[float] = Field(default=None, ge=0)
    captured_at: datetime
    telemetry_source: TelemetrySource = TelemetrySource.unavailable

    @field_validator("captured_at", mode="before")
    @classmethod
    def _capture_at_utc(cls, v):
        return _normalize_utc(v)


class StoredMedia(BaseModel):
    """Internal model representing a persisted media file with full metadata."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    media_id: str
    uri: str
    file_path: str
    mime_type: str
    extension: str
    size_bytes: int = Field(ge=0)
    sha256: str
    storage_mode: StorageMode = StorageMode.managed_upload
    original_filename: str
    telemetry: Optional[MediaTelemetry] = None
    created_at: datetime

    @field_validator("sha256")
    @classmethod
    def _sha256_hex(cls, v: str) -> str:
        v = v.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError("sha256 must be a 64-character hexadecimal value")
        return v

    def to_api_dict(self) -> dict:
        """Return safe client-facing fields only."""
        return self.model_dump(
            by_alias=True,
            exclude={"file_path", "original_filename"},
        )


class MediaUploadResponse(BaseModel):
    """Safe client-facing response after a successful upload."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    media_id: str
    uri: str
    mime_type: str
    size_bytes: int
    sha256: str
    storage_mode: StorageMode = StorageMode.managed_upload
    telemetry: Optional[MediaTelemetry] = None
    created_at: datetime

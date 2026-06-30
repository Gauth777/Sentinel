"""Media routes for Sentinel managed upload pipeline.

Endpoints:
  POST /api/sentinel/media
  GET  /api/sentinel/media/{media_id}
  GET  /api/sentinel/media/{media_id}/file
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.status import (
    HTTP_201_CREATED,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_413_CONTENT_TOO_LARGE,
    HTTP_415_UNSUPPORTED_MEDIA_TYPE,
)

from services.media_service import MediaService, MediaServiceError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sentinel/media")


# ------------------------------------------------------------------
# Dependency injection helpers
# ------------------------------------------------------------------

def _get_media_service(request: Request) -> MediaService:
    svc = getattr(request.app.state, "media_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Media service not initialized")
    return svc  # type: ignore[return-value]


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------

@router.post("", status_code=HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    latitude: Optional[float] = Form(default=None),
    longitude: Optional[float] = Form(default=None),
    heading_degrees: Optional[float] = Form(
        default=None,
        alias="headingDegrees",
    ),
    speed_kmh: Optional[float] = Form(
        default=None,
        alias="speedKmh",
    ),
    captured_at: Optional[str] = Form(
        default=None,
        alias="capturedAt",
    ),
    telemetry_source: Optional[str] = Form(
        default=None,
        alias="telemetrySource",
    ),
    request: Request = None,  # type: ignore
):
    svc = _get_media_service(request)

    if file.filename is None or file.filename.strip() == "":
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Missing filename")

    ext = Path(file.filename).suffix.lower()
    mime = file.content_type or "application/octet-stream"

    # Pre-validate MIME type before streaming
    allowed_mimes = {"image/jpeg", "image/png", "image/webp"}
    if mime not in allowed_mimes:
        raise HTTPException(status_code=HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=f"Unsupported MIME type: {mime}")

    # Build async iterator from UploadFile
    async def file_iterator():
        while True:
            chunk = await file.read(65536)
            if not chunk:
                break
            yield chunk

    try:
        result = await svc.upload(
            file_iterator=file_iterator(),
            mime_type=mime,
            extension=ext,
            original_filename=file.filename,
            latitude=latitude,
            longitude=longitude,
            heading_degrees=heading_degrees,
            speed_kmh=speed_kmh,
            captured_at=captured_at,
            telemetry_source=telemetry_source,
        )
    except MediaServiceError as e:
        msg = str(e).lower()
        if "exceeds maximum" in msg:
            raise HTTPException(status_code=HTTP_413_CONTENT_TOO_LARGE, detail=str(e))
        if "empty file" in msg:
            raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(e))
        if "unsupported" in msg or "signature" in msg or "does not match" in msg:
            raise HTTPException(status_code=HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(e))
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Unexpected upload error: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail="Upload processing failed")

    return result


@router.get("/{media_id}")
async def get_media_metadata(request: Request, media_id: str):
    svc = _get_media_service(request)
    meta = await svc.get_metadata(media_id)
    if meta is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Media not found")
    return meta


@router.get("/{media_id}/file")
async def get_media_file(request: Request, media_id: str):
    svc = _get_media_service(request)
    info = await svc.get_file_info(media_id)
    if info is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Media not found")

    # Verify the file exists on disk
    file_path = info["file_path"]
    if not Path(file_path).exists():
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Media file not found")

    # Security: ensure the resolved file remains inside configured storage
    storage_dir = Path(svc.get_storage_dir()).resolve()
    resolved_path = Path(file_path).resolve()
    try:
        resolved_path.relative_to(storage_dir)
    except ValueError:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Invalid file path")

    return FileResponse(
        path=file_path,
        media_type=info["mime_type"],
        filename=f"{media_id}.{info['extension']}",
    )

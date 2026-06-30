"""Comprehensive tests for Sentinel managed media upload pipeline.

Tests local storage, file validation, metadata safety, and route behaviour.
Does not require a real MongoDB or Neo4j server.
"""
import asyncio
import os
import sys
import tempfile
import hashlib
from pathlib import Path
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from models.media import TelemetrySource
from services.media_storage import LocalMediaStorage, DEFAULT_MEDIA_DIR
from services.media_service import MediaService, MediaServiceError
from routes.media import router as media_router
from routes.training_samples import router as training_samples_router


# --------------------------- Fixtures ---------------------------

@pytest.fixture
def temp_media_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def memory_storage(temp_media_dir):
    """LocalMediaStorage backed purely by in-memory metadata."""
    store = LocalMediaStorage(
        db=None,
        mongo_reachable=False,
        media_dir=temp_media_dir,
        max_bytes=10_485_760,
    )
    return store


@pytest.fixture
def media_service(memory_storage):
    return MediaService(memory_storage)


@pytest.fixture
def test_app(memory_storage):
    app = FastAPI()
    app.state.media_service = MediaService(memory_storage)
    app.include_router(media_router, prefix="/api")
    return app


@pytest.fixture
def client(test_app):
    with TestClient(test_app) as c:
        yield c


# --------------------------- Helpers ---------------------------

JPEG_SIG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
PNG_SIG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR"
WEBP_SIG = b"RIFF\x00\x00\x00\x00WEBPVP8 "


def make_jpeg(size: int = 1024) -> bytes:
    body = b"\x00" * (size - len(JPEG_SIG) - 2)
    return JPEG_SIG + body + b"\xff\xd9"


def make_png(size: int = 1024) -> bytes:
    body = b"\x00" * (size - len(PNG_SIG) - 4)
    crc = b"\x00\x00\x00\x00"
    return PNG_SIG + body + crc


def make_webp(size: int = 1024) -> bytes:
    body = b"\x00" * (size - len(WEBP_SIG))
    return WEBP_SIG + body


def upload(client, filename: str, content: bytes, mime: str, extra: dict = None):
    data = extra or {}
    files = {"file": (filename, BytesIO(content), mime)}
    if data:
        return client.post("/api/sentinel/media", data=data, files=files)
    return client.post("/api/sentinel/media", files=files)


# --------------------------- Upload tests ---------------------------

def test_jpeg_upload_succeeds(client):
    r = upload(client, "test.jpg", make_jpeg(), "image/jpeg")
    assert r.status_code == 201, f"Got {r.status_code}: {r.text}"
    d = r.json()
    assert d["mediaId"].startswith("media-")
    assert d["mimeType"] == "image/jpeg"
    assert d["sizeBytes"] > 0
    assert d["sha256"]
    assert d["storageMode"] == "managed_upload"
    assert "filePath" not in d
    assert "originalFilename" not in d


def test_png_upload_succeeds(client):
    r = upload(client, "test.png", make_png(), "image/png")
    assert r.status_code == 201
    d = r.json()
    assert d["mimeType"] == "image/png"
    assert d["sizeBytes"] > 0


def test_webp_upload_succeeds(client):
    r = upload(client, "test.webp", make_webp(), "image/webp")
    assert r.status_code == 201
    d = r.json()
    assert d["mimeType"] == "image/webp"
    assert d["sizeBytes"] > 0


def test_empty_file_returns_400(client):
    r = upload(client, "test.jpg", b"", "image/jpeg")
    assert r.status_code == 400


def test_unsupported_mime_rejected(client):
    r = upload(client, "test.gif", make_jpeg(), "image/gif")
    assert r.status_code == 415


def test_invalid_image_signature_rejected(client):
    fake = b"NOTANIMAGE" + b"\x00" * 100
    r = upload(client, "test.jpg", fake, "image/jpeg")
    assert r.status_code == 415


def test_mime_signature_mismatch_rejected(client):
    # JPEG body but PNG MIME
    r = upload(client, "test.png", make_jpeg(), "image/png")
    assert r.status_code == 415


def test_extension_mime_mismatch_rejected(client):
    # JPEG body with JPEG MIME but .png extension
    r = upload(client, "test.png", make_jpeg(), "image/jpeg")
    assert r.status_code == 415


def test_oversized_file_rejected(client, memory_storage):
    memory_storage._max_bytes = 500
    big = make_jpeg(1024)
    r = upload(client, "test.jpg", big, "image/jpeg")
    assert r.status_code == 413


def test_media_id_generated_server_side(client):
    r = upload(client, "test.jpg", make_jpeg(), "image/jpeg")
    d = r.json()
    assert d["mediaId"].startswith("media-")
    assert len(d["mediaId"]) > len("media-")


def test_uploaded_filename_cannot_cause_path_traversal(client, memory_storage):
    r = upload(client, "../../etc/passwd.jpg", make_jpeg(), "image/jpeg")
    assert r.status_code in (201, 415)
    if r.status_code == 201:
        d = r.json()
        stored = asyncio.run(memory_storage.get(d["mediaId"]))
        assert stored is not None
        assert Path(stored.file_path).resolve().is_relative_to(Path(memory_storage.media_dir).resolve())


def test_file_stored_under_configured_media_dir(client, memory_storage):
    r = upload(client, "test.jpg", make_jpeg(), "image/jpeg")
    d = r.json()
    stored = asyncio.run(memory_storage.get(d["mediaId"]))
    assert stored is not None
    assert Path(stored.file_path).resolve().is_relative_to(Path(memory_storage.media_dir).resolve())


def test_sha256_is_correct(client):
    content = make_jpeg()
    r = upload(client, "test.jpg", content, "image/jpeg")
    d = r.json()
    expected = hashlib.sha256(content).hexdigest()
    assert d["sha256"] == expected


def test_metadata_endpoint_returns_safe_fields(client):
    r = upload(client, "test.jpg", make_jpeg(), "image/jpeg")
    d = r.json()
    media_id = d["mediaId"]
    r2 = client.get(f"/api/sentinel/media/{media_id}")
    assert r2.status_code == 200
    meta = r2.json()
    assert "mediaId" in meta
    assert "mimeType" in meta
    assert "sizeBytes" in meta
    assert "sha256" in meta
    assert "storageMode" in meta
    assert "filePath" not in meta
    assert "originalFilename" not in meta


def test_file_endpoint_returns_correct_bytes(client):
    content = make_jpeg()
    r = upload(client, "test.jpg", content, "image/jpeg")
    d = r.json()
    media_id = d["mediaId"]
    r2 = client.get(f"/api/sentinel/media/{media_id}/file")
    assert r2.status_code == 200
    assert r2.content == content
    assert r2.headers["content-type"] == "image/jpeg"


def test_missing_media_returns_404(client):
    r = client.get("/api/sentinel/media/media-does-not-exist")
    assert r.status_code == 404

    r2 = client.get("/api/sentinel/media/media-does-not-exist/file")
    assert r2.status_code == 404


def test_location_validation_rejects_invalid_latitude(client):
    r = upload(
        client, "test.jpg", make_jpeg(), "image/jpeg",
        extra={"latitude": 91, "longitude": 80.1506}
    )
    assert r.status_code == 400


def test_location_validation_rejects_invalid_longitude(client):
    r = upload(
        client, "test.jpg", make_jpeg(), "image/jpeg",
        extra={"latitude": 12.9452, "longitude": 181}
    )
    assert r.status_code == 400


def test_negative_speed_rejected(client):
    r = upload(
        client, "test.jpg", make_jpeg(), "image/jpeg",
        extra={"headingDegrees": 8, "speedKmh": -1}
    )
    assert r.status_code == 400


def test_invalid_heading_rejected(client):
    r = upload(
        client, "test.jpg", make_jpeg(), "image/jpeg",
        extra={"headingDegrees": 360}
    )
    assert r.status_code == 400


def test_memory_fallback_works(client):
    r = upload(client, "test.jpg", make_jpeg(), "image/jpeg")
    assert r.status_code == 201
    d = r.json()
    assert d["mediaId"].startswith("media-")


def test_concurrent_uploads_create_distinct_media_ids(client):
    import concurrent.futures

    def do_upload():
        return upload(client, "test.jpg", make_jpeg(), "image/jpeg")

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(do_upload) for _ in range(4)]
        results = [f.result() for f in futures]

    ids = [r.json()["mediaId"] for r in results]
    assert len(set(ids)) == 4


def test_failed_upload_leaves_no_partial_file(client, memory_storage):
    media_dir = Path(memory_storage.media_dir)
    count_before = len(list(media_dir.glob("media-*")))

    memory_storage._max_bytes = 500
    big = make_jpeg(1024)
    r = upload(client, "test.jpg", big, "image/jpeg")
    assert r.status_code == 413

    count_after = len(list(media_dir.glob("media-*")))
    assert count_after == count_before


# --------------------------- Telemetry tests ---------------------------

def test_telemetry_persisted_with_camelcase_fields(client):
    """All frontend multipart fields must reach the backend and be persisted."""
    r = upload(
        client, "test.jpg", make_jpeg(), "image/jpeg",
        extra={
            "latitude": 12.9452,
            "longitude": 80.1506,
            "headingDegrees": 8,
            "speedKmh": 42,
            "capturedAt": "2026-06-29T10:00:00Z",
            "telemetrySource": "live",
        }
    )
    assert r.status_code == 201
    d = r.json()
    assert d["telemetry"]["location"]["latitude"] == 12.9452
    assert d["telemetry"]["location"]["longitude"] == 80.1506
    assert d["telemetry"]["headingDegrees"] == 8
    assert d["telemetry"]["speedKmh"] == 42
    assert d["telemetry"]["telemetrySource"] == "live"


def test_telemetry_persisted_without_gps(client):
    """Timestamp and telemetrySource must survive even when location is absent."""
    r = upload(
        client, "test.jpg", make_jpeg(), "image/jpeg",
        extra={
            "capturedAt": "2026-06-29T10:00:00Z",
            "telemetrySource": "unavailable",
        }
    )
    assert r.status_code == 201
    d = r.json()
    assert d["telemetry"] is not None
    assert d["telemetry"]["location"] is None
    assert d["telemetry"]["headingDegrees"] is None
    assert d["telemetry"]["speedKmh"] is None
    assert d["telemetry"]["telemetrySource"] == "unavailable"
    assert "capturedAt" in d["telemetry"]


def test_partial_location_rejected(client):
    r = upload(
        client, "test.jpg", make_jpeg(), "image/jpeg",
        extra={"latitude": 12.9452}
    )
    assert r.status_code == 400


def test_malformed_captured_at_rejected(client):
    r = upload(
        client, "test.jpg", make_jpeg(), "image/jpeg",
        extra={"capturedAt": "not-a-timestamp"}
    )
    assert r.status_code == 400


def test_invalid_telemetry_source_rejected(client):
    r = upload(
        client, "test.jpg", make_jpeg(), "image/jpeg",
        extra={"telemetrySource": "spacetime"}
    )
    assert r.status_code == 400


# --------------------------- Rollback test ---------------------------

@pytest.mark.anyio
async def test_metadata_failure_removes_final_file(temp_media_dir):
    """If metadata insertion fails, the final file must be rolled back."""
    store = LocalMediaStorage(
        db=None,
        mongo_reachable=False,
        media_dir=temp_media_dir,
        max_bytes=10_485_760,
    )
    svc = MediaService(store)

    # Patch metadata insertion to fail after the file move
    original_set = store._memory_meta.set
    async def failing_set(media_id, doc):
        raise RuntimeError("Simulated metadata failure")

    store._memory_meta.set = failing_set

    from io import BytesIO

    async def fake_iter():
        yield make_jpeg(512)

    media_dir = Path(temp_media_dir)
    count_before = len(list(media_dir.glob("media-*")))

    with pytest.raises(MediaServiceError):
        await svc.upload(
            file_iterator=fake_iter(),
            mime_type="image/jpeg",
            extension=".jpg",
            original_filename="test.jpg",
        )

    count_after = len(list(media_dir.glob("media-*")))
    assert count_after == count_before


# --------------------------- Default media path test ---------------------------

def test_default_media_dir_resolves_from_backend_path():
    """The default media directory must be stable and relative to backend source."""
    assert DEFAULT_MEDIA_DIR.is_absolute()
    assert DEFAULT_MEDIA_DIR.name == "media"
    assert "backend" in str(DEFAULT_MEDIA_DIR)


# --------------------------- File retrieval safety ---------------------------

def test_missing_physical_file_returns_404(client, memory_storage):
    """If metadata exists but the file is missing, return 404."""
    r = upload(client, "test.jpg", make_jpeg(), "image/jpeg")
    d = r.json()
    media_id = d["mediaId"]

    # Delete the file manually
    stored = asyncio.run(memory_storage.get(media_id))
    if stored:
        Path(stored.file_path).unlink()

    r2 = client.get(f"/api/sentinel/media/{media_id}/file")
    assert r2.status_code == 404


# --------------------------- Public DELETE route ---------------------------

def test_public_delete_route_absent(client):
    """The public DELETE endpoint must not exist."""
    r = client.delete("/api/sentinel/media/media-test")
    assert r.status_code == 405


# --------------------------- Router coexistence ---------------------------

def test_media_and_training_routers_coexist(temp_media_dir):
    """Media and training-sample routers must mount without path conflict."""
    from services.training_sample_service import TrainingSampleService, _InMemoryTrainingStore

    app = FastAPI()

    # Training sample service
    ts_store = _InMemoryTrainingStore()
    ts_svc = TrainingSampleService(ts_store, False)
    app.state.training_sample_service = ts_svc

    # Media service with temp directory
    ms_store = LocalMediaStorage(db=None, mongo_reachable=False, media_dir=temp_media_dir)
    ms_svc = MediaService(ms_store)
    app.state.media_service = ms_svc

    app.include_router(training_samples_router, prefix="/api")
    app.include_router(media_router, prefix="/api")

    test_client = TestClient(app)

    # Media upload works
    r1 = test_client.post(
        "/api/sentinel/media",
        files={"file": ("test.jpg", BytesIO(make_jpeg()), "image/jpeg")},
    )
    assert r1.status_code == 201

    # Training sample stats works
    r2 = test_client.get("/api/sentinel/training-samples/stats")
    assert r2.status_code == 200
    assert "total" in r2.json()

    # Media list works
    r3 = test_client.get("/api/sentinel/training-samples")
    assert r3.status_code == 200


# --------------------------- Service-level tests ---------------------------

def test_service_mode_property(memory_storage):
    svc = MediaService(memory_storage)
    assert svc.mode == "memory"


def test_service_upload_rejects_invalid_mime(memory_storage):
    with pytest.raises(ValueError):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
            f.write(b"NOTAJPEG")
            tmp = f.name
        try:
            memory_storage.validate_file_type("image/jpeg", ".jpg", tmp)
        finally:
            os.unlink(tmp)

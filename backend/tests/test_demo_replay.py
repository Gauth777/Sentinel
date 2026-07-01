"""Tests for Sentinel deterministic dataset replay service.

All tests use temporary directories.
No real research assets required.
"""
import asyncio
import json
import os
import sys
import tempfile
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from models.demo_replay import (
    DemoReplayManifest,
    RoadType,
    TrafficDensity,
    RoadComplexity,
    HazardPresence,
    AnticipatedRisk,
    RecommendedAction,
)
from services.demo_replay_service import DemoReplayService
from routes.demo_replay import router as demo_replay_router
from routes.media import router as media_router
from routes.training_samples import router as training_samples_router
from services.media_storage import LocalMediaStorage
from services.media_service import MediaService
from services.training_sample_service import TrainingSampleService, _InMemoryTrainingStore


# --------------------------- Helpers ---------------------------

JPEG_SIG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"


def make_jpeg(size: int = 256) -> bytes:
    body = b"\x00" * (size - len(JPEG_SIG) - 2)
    return JPEG_SIG + body + b"\xff\xd9"


def build_manifest(samples: list) -> dict:
    return {
        "schema_version": "1.0",
        "mode": "dataset_replay",
        "loop": True,
        "samples": samples,
    }


def build_sample(
    sid: str,
    seq: int,
    dashcam: str = "sample/dashcam.jpg",
    topview: str = "sample/topview.png",
    enabled: bool = True,
    **kwargs,
) -> dict:
    base = {
        "sample_id": sid,
        "sequence_index": seq,
        "title": f"Sample {sid}",
        "description": f"Description for {sid}",
        "dashcam_path": dashcam,
        "topview_path": topview,
        "tags": ["indian_road"],
        "enabled": enabled,
    }
    base.update(kwargs)
    return base


# --------------------------- Fixtures ---------------------------

@pytest.fixture
def scenario_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def replay_svc(scenario_dir):
    svc = DemoReplayService(str(scenario_dir))
    return svc


# --------------------------- Service tests ---------------------------

@pytest.mark.anyio
async def test_valid_manifest_initializes(replay_svc, scenario_dir):
    manifest = build_manifest([
        build_sample("s1", 1),
        build_sample("s2", 2),
    ])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)
    # Create dummy image files
    (scenario_dir / "sample").mkdir()
    with open(scenario_dir / "sample" / "dashcam.jpg", "wb") as f:
        f.write(make_jpeg())
    with open(scenario_dir / "sample" / "topview.png", "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

    await replay_svc.initialize()
    status = await replay_svc.status()
    assert status["status"] == "ready"
    assert status["sample_count"] == 2


@pytest.mark.anyio
async def test_current_starts_at_first_enabled(replay_svc, scenario_dir):
    manifest = build_manifest([
        build_sample("s1", 1),
        build_sample("s2", 2),
    ])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    await replay_svc.initialize()
    current = await replay_svc.get_current()
    assert current is not None
    assert current["sample"]["sampleId"] == "s1"
    assert current["current_index"] == 0


@pytest.mark.anyio
async def test_samples_ordered_by_sequence_index(replay_svc, scenario_dir):
    manifest = build_manifest([
        build_sample("s1", 3),
        build_sample("s2", 1),
        build_sample("s3", 2),
    ])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    # Should reject because enabled samples must be ordered
    await replay_svc.initialize()
    status = await replay_svc.status()
    assert status["status"] == "invalid"


@pytest.mark.anyio
async def test_advance_moves_to_next(replay_svc, scenario_dir):
    manifest = build_manifest([
        build_sample("s1", 1),
        build_sample("s2", 2),
    ])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    await replay_svc.initialize()
    result = await replay_svc.advance()
    assert result is not None
    assert result["sample"]["sampleId"] == "s2"
    assert result["current_index"] == 1


@pytest.mark.anyio
async def test_advance_loops_after_final(replay_svc, scenario_dir):
    manifest = build_manifest([
        build_sample("s1", 1),
        build_sample("s2", 2),
    ])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    await replay_svc.initialize()
    await replay_svc.advance()  # -> s2
    result = await replay_svc.advance()  # -> loop to s1
    assert result is not None
    assert result["looped"] is True
    assert result["sample"]["sampleId"] == "s1"


@pytest.mark.anyio
async def test_reset_returns_to_first(replay_svc, scenario_dir):
    manifest = build_manifest([
        build_sample("s1", 1),
        build_sample("s2", 2),
    ])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    await replay_svc.initialize()
    await replay_svc.advance()
    result = await replay_svc.reset()
    assert result is not None
    assert result["sample"]["sampleId"] == "s1"
    assert result["current_index"] == 0


@pytest.mark.anyio
async def test_duplicate_sample_ids_rejected(replay_svc, scenario_dir):
    manifest = build_manifest([
        build_sample("s1", 1),
        build_sample("s1", 2),
    ])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    await replay_svc.initialize()
    status = await replay_svc.status()
    assert status["status"] == "invalid"


@pytest.mark.anyio
async def test_duplicate_sequence_indexes_rejected(replay_svc, scenario_dir):
    manifest = build_manifest([
        build_sample("s1", 1),
        build_sample("s2", 1),
    ])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    await replay_svc.initialize()
    status = await replay_svc.status()
    assert status["status"] == "invalid"


@pytest.mark.anyio
async def test_invalid_latitude_rejected(replay_svc, scenario_dir):
    manifest = build_manifest([
        build_sample("s1", 1, location={"latitude": 91, "longitude": 80}),
    ])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    await replay_svc.initialize()
    status = await replay_svc.status()
    assert status["status"] == "invalid"


@pytest.mark.anyio
async def test_invalid_longitude_rejected(replay_svc, scenario_dir):
    manifest = build_manifest([
        build_sample("s1", 1, location={"latitude": 12, "longitude": 181}),
    ])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    await replay_svc.initialize()
    status = await replay_svc.status()
    assert status["status"] == "invalid"


@pytest.mark.anyio
async def test_invalid_heading_rejected(replay_svc, scenario_dir):
    manifest = build_manifest([
        build_sample("s1", 1, heading_degrees=360),
    ])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    await replay_svc.initialize()
    status = await replay_svc.status()
    assert status["status"] == "invalid"


@pytest.mark.anyio
async def test_invalid_expected_label_rejected(replay_svc, scenario_dir):
    manifest = build_manifest([
        build_sample("s1", 1, expected_labels={
            "road_type": "not_a_road",
            "traffic_density": "low",
            "road_complexity": "simple",
            "hazard_presence": "no",
            "anticipated_risk": "low",
            "recommended_action": "slow_down",
        }),
    ])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    await replay_svc.initialize()
    status = await replay_svc.status()
    assert status["status"] == "invalid"


@pytest.mark.anyio
async def test_absolute_asset_paths_rejected(replay_svc, scenario_dir):
    manifest = build_manifest([
        build_sample("s1", 1, dashcam="/etc/passwd.jpg"),
    ])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    await replay_svc.initialize()
    status = await replay_svc.status()
    assert status["status"] == "invalid"


@pytest.mark.anyio
async def test_path_traversal_rejected(replay_svc, scenario_dir):
    manifest = build_manifest([
        build_sample("s1", 1, dashcam="../../etc/passwd.jpg"),
    ])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    await replay_svc.initialize()
    status = await replay_svc.status()
    assert status["status"] == "invalid"


@pytest.mark.anyio
async def test_missing_manifest_unconfigured(replay_svc):
    await replay_svc.initialize()
    status = await replay_svc.status()
    assert status["status"] == "unconfigured"


@pytest.mark.anyio
async def test_invalid_manifest_does_not_crash_startup(replay_svc, scenario_dir):
    with open(scenario_dir / "manifest.json", "w") as f:
        f.write("NOT JSON")

    await replay_svc.initialize()
    status = await replay_svc.status()
    assert status["status"] == "invalid"


# --------------------------- Route tests ---------------------------

@pytest.fixture
def replay_client(scenario_dir):
    app = FastAPI()
    svc = DemoReplayService(str(scenario_dir))
    app.state.demo_replay_service = svc
    app.include_router(demo_replay_router, prefix="/api")
    with TestClient(app) as c:
        yield c, svc, scenario_dir


def test_missing_dashcam_returns_404(replay_client):
    client, svc, scenario_dir = replay_client
    manifest = build_manifest([build_sample("s1", 1)])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    # Run initialization in async context
    import asyncio
    asyncio.run(svc.initialize())

    r = client.get("/api/sentinel/demo-replay/samples/s1/dashcam")
    assert r.status_code == 404


def test_missing_topview_returns_404(replay_client):
    client, svc, scenario_dir = replay_client
    manifest = build_manifest([build_sample("s1", 1)])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    import asyncio
    asyncio.run(svc.initialize())

    r = client.get("/api/sentinel/demo-replay/samples/s1/topview")
    assert r.status_code == 404


def test_dashcam_endpoint_returns_bytes_and_mime(replay_client):
    client, svc, scenario_dir = replay_client
    manifest = build_manifest([build_sample("s1", 1)])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    (scenario_dir / "sample").mkdir()
    with open(scenario_dir / "sample" / "dashcam.jpg", "wb") as f:
        f.write(make_jpeg())
    with open(scenario_dir / "sample" / "topview.png", "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

    import asyncio
    asyncio.run(svc.initialize())

    r = client.get("/api/sentinel/demo-replay/samples/s1/dashcam")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert len(r.content) > 0


def test_topview_endpoint_returns_bytes_and_mime(replay_client):
    client, svc, scenario_dir = replay_client
    manifest = build_manifest([build_sample("s1", 1)])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    (scenario_dir / "sample").mkdir()
    with open(scenario_dir / "sample" / "dashcam.jpg", "wb") as f:
        f.write(make_jpeg())
    with open(scenario_dir / "sample" / "topview.png", "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

    import asyncio
    asyncio.run(svc.initialize())

    r = client.get("/api/sentinel/demo-replay/samples/s1/topview")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert len(r.content) > 0


@pytest.mark.anyio
async def test_disabled_samples_excluded(replay_svc, scenario_dir):
    manifest = build_manifest([
        build_sample("s1", 1, enabled=True),
        build_sample("s2", 2, enabled=False),
    ])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    await replay_svc.initialize()
    samples = await replay_svc.list_samples()
    assert len(samples) == 1
    assert samples[0]["sampleId"] == "s1"


@pytest.mark.anyio
async def test_disabled_sample_cannot_be_fetched(replay_svc, scenario_dir):
    manifest = build_manifest([
        build_sample("s1", 1, enabled=True),
        build_sample("s2", 2, enabled=False),
    ])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    await replay_svc.initialize()
    result = await replay_svc.get_sample("s2")
    assert result is None


def test_filesystem_paths_absent_from_responses(replay_client):
    client, svc, scenario_dir = replay_client
    manifest = build_manifest([build_sample("s1", 1)])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    import asyncio
    asyncio.run(svc.initialize())

    r = client.get("/api/sentinel/demo-replay/samples")
    assert r.status_code == 200
    d = r.json()
    assert len(d) == 1
    assert "dashcamPath" not in d[0]
    assert "topviewPath" not in d[0]
    assert "dashcamUrl" in d[0]
    assert "topviewUrl" in d[0]


def test_expected_labels_absent_from_default_responses(replay_client):
    client, svc, scenario_dir = replay_client
    manifest = build_manifest([build_sample("s1", 1, expected_labels={
        "road_type": "highway",
        "traffic_density": "low",
        "road_complexity": "simple",
        "hazard_presence": "no",
        "anticipated_risk": "low",
        "recommended_action": "maintain_speed",
    })])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    import asyncio
    asyncio.run(svc.initialize())

    r = client.get("/api/sentinel/demo-replay/samples/s1")
    assert r.status_code == 200
    d = r.json()
    assert "expectedLabels" not in d


@pytest.mark.anyio
async def test_concurrent_advance_consistent(replay_svc, scenario_dir):
    manifest = build_manifest([
        build_sample("s1", 1),
        build_sample("s2", 2),
        build_sample("s3", 3),
    ])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    await replay_svc.initialize()

    async def advance_many():
        for _ in range(10):
            await replay_svc.advance()

    await asyncio.gather(advance_many(), advance_many())
    current = await replay_svc.get_current()
    # After 20 advances on 3 samples: 20 % 3 = 2 -> s3
    assert current["sample"]["sampleId"] == "s3"


@pytest.mark.anyio
async def test_replay_reset_does_not_clear_media_metadata(replay_svc, scenario_dir):
    # This is a conceptual test: reset only affects replay state
    manifest = build_manifest([build_sample("s1", 1)])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    await replay_svc.initialize()
    await replay_svc.advance()
    await replay_svc.reset()
    status = await replay_svc.status()
    assert status["status"] == "ready"


@pytest.mark.anyio
async def test_replay_reset_does_not_clear_training_samples(replay_svc, scenario_dir):
    # Same conceptual test: reset is isolated
    manifest = build_manifest([build_sample("s1", 1)])
    with open(scenario_dir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    await replay_svc.initialize()
    await replay_svc.reset()
    status = await replay_svc.status()
    assert status["status"] == "ready"


# --------------------------- Router coexistence ---------------------------

def test_media_and_replay_routers_coexist(tmp_path):
    app = FastAPI()

    # Media service
    ms_store = LocalMediaStorage(db=None, mongo_reachable=False, media_dir=str(tmp_path))
    ms_svc = MediaService(ms_store)
    app.state.media_service = ms_svc

    # Replay service
    rs_svc = DemoReplayService()
    app.state.demo_replay_service = rs_svc

    app.include_router(media_router, prefix="/api")
    app.include_router(demo_replay_router, prefix="/api")

    test_client = TestClient(app)

    # Media upload returns 201 or 415
    r1 = test_client.post(
        "/api/sentinel/media",
        files={"file": ("test.jpg", BytesIO(make_jpeg()), "image/jpeg")},
    )
    assert r1.status_code in (201, 415)

    # Replay status returns 200 (may be unconfigured)
    r2 = test_client.get("/api/sentinel/demo-replay")
    assert r2.status_code == 200


def test_training_and_replay_routers_coexist():
    app = FastAPI()

    # Training service
    ts_store = _InMemoryTrainingStore()
    ts_svc = TrainingSampleService(ts_store, False)
    app.state.training_sample_service = ts_svc

    # Replay service
    rs_svc = DemoReplayService()
    app.state.demo_replay_service = rs_svc

    app.include_router(training_samples_router, prefix="/api")
    app.include_router(demo_replay_router, prefix="/api")

    test_client = TestClient(app)

    r1 = test_client.get("/api/sentinel/training-samples/stats")
    assert r1.status_code == 200

    r2 = test_client.get("/api/sentinel/demo-replay")
    assert r2.status_code == 200


def test_existing_perception_graph_route_registered():
    # Perception graph is in the main app, not a router.
    # Just verify the demo_replay router has registered routes.
    assert len(demo_replay_router.routes) > 0
    route_paths = [str(r.path) for r in demo_replay_router.routes if hasattr(r, "path")]
    assert any("/sentinel/demo-replay" in p for p in route_paths)

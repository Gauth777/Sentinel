"""Verification tests for replay corrections."""
from __future__ import annotations

import json
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.demo_replay_service import DemoReplayService
from services.perception_graph_service import PerceptionGraphService
from routes.demo_replay import router as demo_replay_router
from routes.demo_replay_evidence import router as demo_replay_evidence_router
from routes.demo_replay_graph_verify import router as demo_replay_graph_verify_router


def _make_five_sample_manifest() -> dict:
    """Return a hermetic manifest with 5 enabled samples."""
    return {
        "schemaVersion": "1.0",
        "mode": "dataset_replay",
        "loop": True,
        "samples": [
            {
                "sampleId": f"sample_{i:03d}",
                "sequenceIndex": i,
                "title": f"Sample {i}",
                "description": f"Description for sample {i}",
                "dashcamPath": f"sample_{i:03d}/dashcam.jpg",
                "topviewPath": f"sample_{i:03d}/topview.png",
                "location": {"latitude": 12.0 + i * 0.1, "longitude": 77.0 + i * 0.1},
                "headingDegrees": float(i * 10),
                "capturedAt": f"2026-06-29T10:{i:02d}:00Z",
                "tags": ["indian_road"],
                "expectedLabels": {
                    "roadType": "urban_arterial",
                    "trafficDensity": "low",
                    "roadComplexity": "simple",
                    "hazardPresence": "no",
                    "anticipatedRisk": "low",
                    "recommendedAction": "maintain_speed",
                },
                "cachedPredictionPath": f"sample_{i:03d}/cached_prediction.json",
                "enabled": True,
            }
            for i in range(1, 6)
        ],
    }


def _write_scenario(tmp_path, manifest: dict) -> None:
    """Write manifest and minimal image files to tmp_path."""
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for s in manifest["samples"]:
        sample_dir = tmp_path / s["sampleId"]
        sample_dir.mkdir(parents=True, exist_ok=True)
        # Minimal JPEG signature
        (sample_dir / "dashcam.jpg").write_bytes(
            b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100 + b"\xff\xd9"
        )
        # Minimal PNG signature
        (sample_dir / "topview.png").write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        )
        # Cached prediction with matching sample_id
        cached = {
            "sampleId": s["sampleId"],
            "model": "Qwen2.5-VL-7B-Instruct",
            "promptVersion": "v1",
            "generatedAt": s["capturedAt"],
            "prediction": {
                "roadType": "urban_arterial",
                "trafficDensity": "low",
                "roadComplexity": "simple",
                "hazardPresence": "no",
                "anticipatedRisk": "low",
                "recommendedAction": "maintain_speed",
            },
            "validated": True,
        }
        (sample_dir / "cached_prediction.json").write_text(json.dumps(cached), encoding="utf-8")


@pytest.fixture
def hermetic_app(tmp_path):
    """Return a FastAPI app with hermetic 5-sample replay service."""
    manifest = _make_five_sample_manifest()
    _write_scenario(tmp_path, manifest)

    replay_svc = DemoReplayService(scenario_dir=str(tmp_path))
    graph_svc = PerceptionGraphService()
    graph_svc._mode = "memory"

    app = FastAPI()
    app.state.demo_replay_service = replay_svc
    app.state.perception_graph_service = graph_svc

    app.include_router(demo_replay_router, prefix="/api")
    app.include_router(demo_replay_evidence_router, prefix="/api")
    app.include_router(demo_replay_graph_verify_router, prefix="/api")

    import anyio
    anyio.run(replay_svc.initialize)

    return app


@pytest.fixture
def client(hermetic_app):
    with TestClient(hermetic_app) as c:
        yield c


def test_sample_005_evidence(tmp_path):
    # Override sample_005 with specific evidence test data
    manifest = _make_five_sample_manifest()
    # Update sample_005 (index 4 in the list)
    manifest["samples"][4] = {
        "sampleId": "sample_005",
        "sequenceIndex": 5,
        "title": "Mathura Road Urban Arterial",
        "description": "Low traffic density on urban arterial segment in New Delhi",
        "dashcamPath": "sample_005/dashcam.jpg",
        "topviewPath": "sample_005/topview.png",
        "location": {
            "latitude": 28.594701,
            "longitude": 77.072801,
        },
        "headingDegrees": 5.0,
        "capturedAt": "2026-06-29T10:20:00Z",
        "tags": ["urban_arterial", "low_traffic", "indian_road"],
        "expectedLabels": {
            "roadType": "junction",
            "trafficDensity": "high",
            "roadComplexity": "complex",
            "hazardPresence": "yes",
            "anticipatedRisk": "high",
            "recommendedAction": "yield",
        },
        "cachedPredictionPath": "sample_005/cached_prediction.json",
        "enabled": True,
    }

    source_map_data = {
        "sample_005": "sample_041",
    }

    cached_pred_data = {
        "sampleId": "sample_005",
        "model": "Qwen2.5-VL-7B-Instruct",
        "promptVersion": "v1",
        "generatedAt": "2026-06-29T10:20:00Z",
        "prediction": {
            "roadType": "junction",
            "trafficDensity": "medium",
            "roadComplexity": "moderate",
            "hazardPresence": "yes",
            "anticipatedRisk": "medium",
            "recommendedAction": "slow_down",
        },
        "validated": True,
    }

    _write_scenario(tmp_path, manifest)
    (tmp_path / "source_map.example.json").write_text(
        json.dumps(source_map_data), encoding="utf-8"
    )
    (tmp_path / "sample_005" / "cached_prediction.json").write_text(
        json.dumps(cached_pred_data), encoding="utf-8"
    )

    replay_svc = DemoReplayService(scenario_dir=str(tmp_path))
    graph_svc = PerceptionGraphService()
    graph_svc._mode = "memory"

    app = FastAPI()
    app.state.demo_replay_service = replay_svc
    app.state.perception_graph_service = graph_svc

    app.include_router(demo_replay_router, prefix="/api")
    app.include_router(demo_replay_evidence_router, prefix="/api")
    app.include_router(demo_replay_graph_verify_router, prefix="/api")

    import anyio
    anyio.run(replay_svc.initialize)

    with TestClient(app) as test_client:
        r = test_client.get("/api/sentinel/demo-replay/samples/sample_005/evidence")
        assert r.status_code == 200
        data = r.json()

        # 1. Assert sample_005 maps to sample_041
        assert data["sourceSampleId"] == "sample_041"

        # 2. Assert evidence contains exact expected labels
        expected = data["expectedLabels"]
        assert expected["roadType"] == "junction"
        assert expected["trafficDensity"] == "high"
        assert expected["roadComplexity"] == "complex"
        assert expected["hazardPresence"] == "yes"
        assert expected["anticipatedRisk"] == "high"
        assert expected["recommendedAction"] == "yield"

        # 3. Assert actual prediction contains exact cached Qwen values
        actual = data["actualPrediction"]
        assert actual["roadType"] == "junction"
        assert actual["trafficDensity"] == "medium"
        assert actual["roadComplexity"] == "moderate"
        assert actual["hazardPresence"] == "yes"
        assert actual["anticipatedRisk"] == "medium"
        assert actual["recommendedAction"] == "slow_down"

        # 4. Assert correctFieldCount is 2 and totalFieldCount is 6
        assert data["correctFieldCount"] == 2
        assert data["totalFieldCount"] == 6


@pytest.mark.anyio
async def test_cached_prediction_sample_id_mismatch(hermetic_app, client, tmp_path):
    """A cached file for a different sample must be rejected."""
    # Write a cached prediction with wrong sample_id into sample_001
    bad_cached = {
        "sampleId": "sample_999",
        "model": "Qwen2.5-VL-7B-Instruct",
        "promptVersion": "v1",
        "generatedAt": "2026-06-29T10:00:00Z",
        "prediction": {
            "roadType": "highway",
            "trafficDensity": "low",
            "roadComplexity": "simple",
            "hazardPresence": "no",
            "anticipatedRisk": "low",
            "recommendedAction": "maintain_speed",
        },
        "validated": True,
    }
    (tmp_path / "sample_001" / "cached_prediction.json").write_text(
        json.dumps(bad_cached), encoding="utf-8"
    )

    # Re-initialize the service so it reads the new file
    svc = hermetic_app.state.demo_replay_service
    await svc.initialize()

    result = await svc.get_cached_prediction("sample_001")
    assert result is None


@pytest.mark.anyio
async def test_exact_observation_id_verify(hermetic_app, client):
    graph_svc = hermetic_app.state.perception_graph_service
    await graph_svc.record_observation(
        observation_id="exact-obs-1",
        vehicle_id="v-1",
        vehicle_label="V1",
        hazard_id="hzd-1",
        hazard_type="road_hazard",
        hazard_label="Pothole",
        road_segment_id="road-1",
        road_segment_name="R1",
        timestamp=100.0,
    )

    # Verify with correct IDs
    r = client.get("/api/sentinel/demo-replay/graph-verify?hazardId=hzd-1&observationId=exact-obs-1")
    assert r.status_code == 200
    data = r.json()
    assert data["exactHazardFound"] is True
    assert data["exactObservationFound"] is True
    assert data["exactSupportsRelationshipFound"] is True
    assert data["verified"] is True

    # 5. A different observation attached to the same hazard does NOT pass
    r2 = client.get("/api/sentinel/demo-replay/graph-verify?hazardId=hzd-1&observationId=different-obs")
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["exactHazardFound"] is True
    assert data2["exactObservationFound"] is False
    assert data2["exactSupportsRelationshipFound"] is False
    assert data2["verified"] is False


def test_sample_selection_by_id(client):
    # 6. Sample selection by ID
    r = client.post("/api/sentinel/demo-replay/samples/sample_005/select")
    assert r.status_code == 200
    data = r.json()
    assert data["sample"]["sampleId"] == "sample_005"
    assert data["currentIndex"] == 4
    assert data["sampleCount"] == 5

    # Current index must be updated
    r_curr = client.get("/api/sentinel/demo-replay/current")
    assert r_curr.status_code == 200
    assert r_curr.json()["sample"]["sampleId"] == "sample_005"

    # 7. Unknown selection returns 404
    r_err = client.post("/api/sentinel/demo-replay/samples/sample_999/select")
    assert r_err.status_code == 404


def test_existing_advance_reset_loop(client):
    # Reset
    r = client.post("/api/sentinel/demo-replay/reset")
    assert r.status_code == 200
    assert r.json()["currentIndex"] == 0
    assert r.json()["sample"]["sampleId"] == "sample_001"

    # Advance
    r = client.post("/api/sentinel/demo-replay/advance")
    assert r.status_code == 200
    assert r.json()["currentIndex"] == 1
    assert r.json()["sample"]["sampleId"] == "sample_002"


@pytest.mark.anyio
async def test_neo4j_persistence_conditions(hermetic_app, client, monkeypatch):
    graph_svc = hermetic_app.state.perception_graph_service

    async def mock_build_graph(*args, **kwargs):
        return {
            "mode": "neo4j",
            "nodes": [
                {"id": "hzd-1", "type": "Hazard"},
                {"id": "obs-1", "type": "Observation"},
            ],
            "edges": [
                {"type": "SUPPORTS", "source": "obs-1", "target": "hzd-1"},
            ],
        }

    monkeypatch.setattr(graph_svc, "build_graph", mock_build_graph)

    # 1. verified = True
    r = client.get("/api/sentinel/demo-replay/graph-verify?hazardId=hzd-1&observationId=obs-1")
    assert r.status_code == 200
    data = r.json()
    assert data["graphBackend"] == "neo4j"
    assert data["verified"] is True
    assert data["summary"] == "Persisted in Neo4j"

    # 2. verified = False (missing observation ID check)
    r2 = client.get("/api/sentinel/demo-replay/graph-verify?hazardId=hzd-1&observationId=obs-different")
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["graphBackend"] == "neo4j"
    assert data2["verified"] is False
    assert data2["summary"] == "Verification failed — missing exact IDs"


@pytest.mark.anyio
async def test_unknown_backend_failure_semantics(hermetic_app, client, monkeypatch):
    graph_svc = hermetic_app.state.perception_graph_service

    async def mock_build_graph_fail(*args, **kwargs):
        raise RuntimeError("Neo4j database down")

    monkeypatch.setattr(graph_svc, "build_graph", mock_build_graph_fail)

    r = client.get("/api/sentinel/demo-replay/graph-verify?hazardId=hzd-1&observationId=obs-1")
    assert r.status_code == 200
    data = r.json()
    assert data["graphBackend"] == "unknown"
    assert data["verified"] is False
    assert data["error"] == "Graph query failed"
    assert "memory" not in data["summary"].lower()
    assert "fallback" not in data["summary"].lower()


@pytest.mark.anyio
async def test_memory_fallback_labelled_memory(hermetic_app, client, monkeypatch):
    """A successful memory fallback must be labelled 'memory', not 'unknown'."""
    graph_svc = hermetic_app.state.perception_graph_service

    async def mock_build_graph(*args, **kwargs):
        return {
            "mode": "memory",
            "nodes": [
                {"id": "hzd-1", "type": "Hazard"},
                {"id": "obs-1", "type": "Observation"},
            ],
            "edges": [
                {"type": "SUPPORTS", "source": "obs-1", "target": "hzd-1"},
            ],
        }

    monkeypatch.setattr(graph_svc, "build_graph", mock_build_graph)

    r = client.get("/api/sentinel/demo-replay/graph-verify?hazardId=hzd-1&observationId=obs-1")
    assert r.status_code == 200
    data = r.json()
    assert data["graphBackend"] == "memory"
    assert data["verified"] is True
    assert data["summary"] == "Stored in in-memory fallback"


@pytest.mark.anyio
async def test_neo4j_env_does_not_affect_result(hermetic_app, client, monkeypatch):
    """Graph verification results must not change based on NEO4J_ENABLED env var."""
    graph_svc = hermetic_app.state.perception_graph_service

    async def mock_build_graph(*args, **kwargs):
        return {
            "mode": "memory",
            "nodes": [
                {"id": "hzd-1", "type": "Hazard"},
            ],
            "edges": [],
        }

    monkeypatch.setattr(graph_svc, "build_graph", mock_build_graph)

    # Even with NEO4J_ENABLED=True, if graph returns mode=memory, label should be memory
    monkeypatch.setenv("NEO4J_ENABLED", "1")
    # We need to reimport to check if NEO4J_ENABLED is used, but the route no longer uses it.
    # So the result should be memory regardless of the env var.
    r = client.get("/api/sentinel/demo-replay/graph-verify?hazardId=hzd-1&observationId=obs-1")
    assert r.status_code == 200
    data = r.json()
    assert data["graphBackend"] == "memory"

    # Same with env var unset
    monkeypatch.delenv("NEO4J_ENABLED", raising=False)
    r2 = client.get("/api/sentinel/demo-replay/graph-verify?hazardId=hzd-1&observationId=obs-1")
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["graphBackend"] == "memory"

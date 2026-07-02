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


@pytest.fixture
def test_app():
    app = FastAPI()
    # Real services with defaults
    replay_svc = DemoReplayService()
    graph_svc = PerceptionGraphService()
    graph_svc._mode = "memory"
    
    app.state.demo_replay_service = replay_svc
    app.state.perception_graph_service = graph_svc
    
    app.include_router(demo_replay_router, prefix="/api")
    app.include_router(demo_replay_evidence_router, prefix="/api")
    app.include_router(demo_replay_graph_verify_router, prefix="/api")
    
    import anyio
    anyio.run(replay_svc.initialize)
    
    return app


@pytest.fixture
def client(test_app):
    with TestClient(test_app) as c:
        yield c


def test_sample_005_evidence(tmp_path):
    # Setup mock scenario dir inside tmp_path
    manifest_data = {
        "schemaVersion": "1.0",
        "mode": "dataset_replay",
        "loop": True,
        "samples": [
            {
                "sampleId": "sample_005",
                "sequenceIndex": 1,
                "title": "Mathura Road Urban Arterial",
                "description": "Low traffic density on urban arterial segment in New Delhi",
                "dashcamPath": "sample_005/dashcam.jpg",
                "topviewPath": "sample_005/topview.png",
                "location": {
                    "latitude": 28.594701,
                    "longitude": 77.072801
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
                    "recommendedAction": "yield"
                },
                "cachedPredictionPath": "sample_005/cached_prediction.json",
                "enabled": True
            }
        ]
    }

    source_map_data = {
        "sample_005": "sample_041"
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
            "recommendedAction": "slow_down"
        },
        "validated": True
    }

    # Write files
    (tmp_path / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")
    (tmp_path / "source_map.example.json").write_text(json.dumps(source_map_data), encoding="utf-8")

    sample_dir = tmp_path / "sample_005"
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "cached_prediction.json").write_text(json.dumps(cached_pred_data), encoding="utf-8")

    # Initialize service and app
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
async def test_exact_observation_id_verify(test_app, client):
    graph_svc = test_app.state.perception_graph_service
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
async def test_neo4j_persistence_conditions(test_app, client, monkeypatch):
    import services.neo4j_service as neo4j_mod
    monkeypatch.setattr(neo4j_mod, "NEO4J_ENABLED", True)
    
    graph_svc = test_app.state.perception_graph_service
    
    async def mock_build_graph(*args, **kwargs):
        return {
            "mode": "neo4j",
            "nodes": [
                {"id": "hzd-1", "type": "Hazard"},
                {"id": "obs-1", "type": "Observation"}
            ],
            "edges": [
                {"type": "SUPPORTS", "source": "obs-1", "target": "hzd-1"}
            ]
        }
        
    monkeypatch.setattr(graph_svc, "build_graph", mock_build_graph)
    graph_svc._mode = "neo4j"
    
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
async def test_unknown_backend_failure_semantics(test_app, client, monkeypatch):
    graph_svc = test_app.state.perception_graph_service
    
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

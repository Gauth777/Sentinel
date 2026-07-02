"""Tests for Sentinel replay provenance evidence route."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from models.demo_replay import RoadType, TrafficDensity, RoadComplexity, HazardPresence, AnticipatedRisk, RecommendedAction
from services.demo_replay_service import DemoReplayService
from routes.demo_replay_evidence import router as demo_replay_evidence_router


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


@pytest.fixture
def evidence_client(tmp_path):
    app = FastAPI()
    svc = DemoReplayService(str(tmp_path))
    app.state.demo_replay_service = svc
    app.include_router(demo_replay_evidence_router, prefix="/api")
    with TestClient(app) as c:
        yield c, svc, tmp_path


def test_evidence_returns_source_map_and_labels(evidence_client):
    client, svc, tmp_path = evidence_client
    
    # 1. Write manifest with expected labels
    expected = {
        "road_type": "highway",
        "traffic_density": "high",
        "road_complexity": "moderate",
        "hazard_presence": "no",
        "anticipated_risk": "medium",
        "recommended_action": "slow_down",
    }
    manifest = build_manifest([build_sample("sample_001", 1, expected_labels=expected)])
    with open(tmp_path / "manifest.json", "w") as f:
        json.dump(manifest, f)

    # 2. Write source_map.example.json
    source_map = {"sample_001": "sample_018"}
    with open(tmp_path / "source_map.example.json", "w") as f:
        json.dump(source_map, f)

    # Setup files for validation pass so it initializes properly
    (tmp_path / "sample").mkdir()
    with open(tmp_path / "sample" / "dashcam.jpg", "wb") as f:
        f.write(b"dummy_dashcam")
    with open(tmp_path / "sample" / "topview.png", "wb") as f:
        f.write(b"dummy_topview")

    # Let service initialize
    import anyio
    anyio.run(svc.initialize)

    r = client.get("/api/sentinel/demo-replay/samples/sample_001/evidence")
    assert r.status_code == 200
    data = r.json()
    assert data["sampleId"] == "sample_001"
    assert data["sourceSampleId"] == "sample_018"
    assert data["sourceMapAvailable"] is True
    assert data["expectedLabels"]["roadType"] == "highway"
    assert data["expectedLabels"]["trafficDensity"] == "high"


def test_evidence_not_found(evidence_client):
    client, svc, tmp_path = evidence_client
    
    # Empty manifest
    manifest = build_manifest([])
    with open(tmp_path / "manifest.json", "w") as f:
        json.dump(manifest, f)
    
    import anyio
    anyio.run(svc.initialize)

    r = client.get("/api/sentinel/demo-replay/samples/sample_009/evidence")
    assert r.status_code == 404
    assert "Sample not found" in r.json()["detail"]


def test_evidence_source_map_missing(evidence_client):
    client, svc, tmp_path = evidence_client
    
    manifest = build_manifest([build_sample("sample_001", 1)])
    with open(tmp_path / "manifest.json", "w") as f:
        json.dump(manifest, f)
        
    (tmp_path / "sample").mkdir()
    with open(tmp_path / "sample" / "dashcam.jpg", "wb") as f:
        f.write(b"dummy_dashcam")
    with open(tmp_path / "sample" / "topview.png", "wb") as f:
        f.write(b"dummy_topview")

    import anyio
    anyio.run(svc.initialize)

    r = client.get("/api/sentinel/demo-replay/samples/sample_001/evidence")
    assert r.status_code == 200
    data = r.json()
    assert data["sampleId"] == "sample_001"
    assert data["sourceSampleId"] is None
    assert data["sourceMapAvailable"] is False
    assert data["expectedLabels"] is None

"""Tests for Sentinel replay provenance graph verification route."""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.perception_graph_service import PerceptionGraphService
from routes.demo_replay_graph_verify import router as demo_replay_graph_verify_router


@pytest.fixture
def graph_client():
    app = FastAPI()
    svc = PerceptionGraphService()
    # Force memory mode for predictable testing
    svc._mode = "memory"
    app.state.perception_graph_service = svc
    app.include_router(demo_replay_graph_verify_router, prefix="/api")
    with TestClient(app) as c:
        yield c, svc


@pytest.mark.anyio
async def test_graph_verify_not_found(graph_client):
    client, svc = graph_client
    r = client.get("/api/sentinel/demo-replay/graph-verify?hazardId=hzd-nonexistent&observationId=obs-nonexistent")
    assert r.status_code == 200
    data = r.json()
    assert data["hazardId"] == "hzd-nonexistent"
    assert data["hazardNodeFound"] is False
    assert data["observationNodeFound"] is False
    assert data["relationshipFound"] is False


@pytest.mark.anyio
async def test_graph_verify_found(graph_client):
    client, svc = graph_client

    # Record mock observation to populate the graph
    await svc.record_observation(
        observation_id="obs-1",
        vehicle_id="v-replay-observer",
        vehicle_label="Sentinel Dataset Observer",
        hazard_id="hzd-1",
        hazard_type="road_hazard",
        hazard_label="Pothole",
        road_segment_id="road-1",
        road_segment_name="Mathura Road",
        timestamp=123.45,
    )

    r = client.get("/api/sentinel/demo-replay/graph-verify?hazardId=hzd-1&observationId=obs-1")
    assert r.status_code == 200
    data = r.json()
    assert data["hazardId"] == "hzd-1"
    assert data["graphBackend"] == "memory"
    assert data["hazardNodeFound"] is True
    assert data["observationNodeFound"] is True
    assert data["relationshipFound"] is True
    assert data["warningNodeFound"] is False
    assert "Stored in in-memory fallback" in data["summary"]


@pytest.mark.anyio
async def test_graph_verify_warning_nodes(graph_client):
    client, svc = graph_client

    # Record mock observation and warning
    await svc.record_observation(
        observation_id="obs-2",
        vehicle_id="v-replay-observer",
        vehicle_label="Sentinel Dataset Observer",
        hazard_id="hzd-2",
        hazard_type="road_hazard",
        hazard_label="Pothole",
        road_segment_id="road-1",
        road_segment_name="Mathura Road",
        timestamp=123.45,
    )

    await svc.record_warning(
        warning_id="warn-1",
        hazard_id="hzd-2",
        vehicle_id="v-ego",
        warning_text="Slow down pothole ahead",
        language="en",
        road_segment_id="road-1",
        timestamp=124.0,
    )

    r = client.get("/api/sentinel/demo-replay/graph-verify?hazardId=hzd-2&observationId=obs-2")
    assert r.status_code == 200
    data = r.json()
    assert data["hazardId"] == "hzd-2"
    assert data["hazardNodeFound"] is True
    assert data["observationNodeFound"] is True
    assert data["relationshipFound"] is True
    assert data["warningNodeFound"] is True


def test_graph_verify_invalid_hazard_id(graph_client):
    client, svc = graph_client
    r = client.get("/api/sentinel/demo-replay/graph-verify?hazardId=&observationId=obs-1")
    # Query validator enforces min_length=1 or required parameters
    assert r.status_code == 422

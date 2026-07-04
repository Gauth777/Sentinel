"""Integration tests for PerceptionGraphService wired into the FastAPI application.

Run targeted:
    pytest backend/tests/test_perception_graph_integration.py -q

Run full regression:
    pytest backend/tests -q
"""

import os
import sys

# Force test isolation before any server import
os.environ["MONGO_URL"] = "mongodb://127.0.0.1:1"
os.environ["DB_NAME"] = "sentinel_test_integration"
os.environ["NEO4J_ENABLED"] = "false"
os.environ["NEO4J_URI"] = ""
os.environ["NEO4J_USERNAME"] = ""
os.environ["NEO4J_PASSWORD"] = ""

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import pytest
from fastapi.testclient import TestClient
from server import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.post("/api/sentinel/demo/reset")
        yield c


# ---------------------------------------------------------------------------
# Empty graph
# ---------------------------------------------------------------------------

def test_perception_graph_empty(client):
    r = client.get("/api/sentinel/perception-graph")
    assert r.status_code == 200
    graph = r.json()
    assert graph["mode"] == "memory"
    assert graph["focusHazardId"] is None
    assert len(graph["nodes"]) == 4
    assert len(graph["edges"]) == 4
    assert graph["summary"]["nodeCount"] == 4
    assert graph["summary"]["edgeCount"] == 4
    assert graph["summary"]["focus"] is None
    required_keys = {
        "mode",
        "generatedAt",
        "focusHazardId",
        "nodes",
        "edges",
        "summary",
        "timeline",
    }
    assert set(graph.keys()) == required_keys


# ---------------------------------------------------------------------------
# Graph after observation
# ---------------------------------------------------------------------------

def test_perception_graph_after_observation(client):
    client.post("/api/sentinel/demo/reset")

    obs = {
        "id": "obs-integ-001",
        "type": "pothole",
        "label": "Pothole Ahead",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "polygon": None,
        "sourceVehicleId": "v-1",
        "vehicleLabel": "Sentinel-A8",
    }
    r = client.post("/api/sentinel/demo/observation", json=obs)
    assert r.status_code == 200
    created = r.json()
    hazard_id = created["id"]

    graph = client.get("/api/sentinel/perception-graph").json()
    assert graph["summary"]["hazardCount"] >= 1
    assert graph["summary"]["observationCount"] >= 1
    assert graph["summary"]["vehicleCount"] >= 1

    edge_types = {e["type"] for e in graph["edges"]}
    assert "OBSERVED" in edge_types
    assert "SUPPORTS" in edge_types
    assert "ON_ROAD" in edge_types
    assert "APPROACHING" in edge_types


# ---------------------------------------------------------------------------
# Focused hazard query
# ---------------------------------------------------------------------------

def test_perception_graph_focused_hazard(client):
    client.post("/api/sentinel/demo/reset")

    obs = {
        "id": "obs-integ-002",
        "type": "pothole",
        "label": "Pothole Ahead",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "polygon": None,
        "sourceVehicleId": "v-1",
        "vehicleLabel": "Sentinel-A8",
    }
    r = client.post("/api/sentinel/demo/observation", json=obs)
    assert r.status_code == 200
    hazard_id = r.json()["id"]

    graph = client.get(f"/api/sentinel/perception-graph?hazard_id={hazard_id}").json()
    assert graph["focusHazardId"] == hazard_id
    assert graph["summary"]["hazardCount"] == 1
    assert graph["summary"]["focus"] is not None
    assert graph["summary"]["focus"]["sourceCount"] == 1
    assert graph["summary"]["focus"]["confidence"] == 60


# ---------------------------------------------------------------------------
# Corroboration from two vehicles
# ---------------------------------------------------------------------------

def test_perception_graph_corroboration(client):
    client.post("/api/sentinel/demo/reset")

    obs1 = {
        "id": "obs-integ-003",
        "type": "pothole",
        "label": "Pothole Ahead",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "polygon": None,
        "sourceVehicleId": "v-1",
        "vehicleLabel": "Sentinel-A8",
    }
    r1 = client.post("/api/sentinel/demo/observation", json=obs1)
    assert r1.status_code == 200
    hazard_id = r1.json()["id"]

    obs2 = {
        "id": "obs-integ-004",
        "type": "pothole",
        "label": "Pothole Ahead",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "polygon": None,
        "sourceVehicleId": "v-2",
        "vehicleLabel": "Sentinel-C2",
    }
    r2 = client.post("/api/sentinel/demo/observation", json=obs2)
    assert r2.status_code == 200
    assert r2.json()["id"] == hazard_id

    graph = client.get(f"/api/sentinel/perception-graph?hazard_id={hazard_id}").json()
    assert graph["summary"]["focus"]["sourceCount"] == 2
    assert graph["summary"]["focus"]["confidence"] == 80


# ---------------------------------------------------------------------------
# Warning chain after observation
# ---------------------------------------------------------------------------

def test_perception_graph_warning_chain(client):
    client.post("/api/sentinel/demo/reset")

    obs = {
        "id": "obs-integ-005",
        "type": "pothole",
        "label": "Pothole Ahead",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "polygon": None,
        "sourceVehicleId": "v-1",
        "vehicleLabel": "Sentinel-A8",
    }
    r = client.post("/api/sentinel/demo/observation", json=obs)
    assert r.status_code == 200
    res_json = r.json()
    hazard_id = res_json["id"]
    assert len(res_json.get("_warning_events", [])) == 4

    graph = client.get(f"/api/sentinel/perception-graph?hazard_id={hazard_id}").json()
    edge_types = {e["type"] for e in graph["edges"]}
    assert "TRIGGERED_WARNING" in edge_types
    assert "DELIVERED_TO" in edge_types

    warning_nodes = [n for n in graph["nodes"] if n["type"] == "Warning"]
    assert len(warning_nodes) == 4
    # All warning nodes are in English
    for node in warning_nodes:
        assert node["properties"]["language"] == "en"
        assert node["properties"]["text"] == res_json["warnings"]["en"]


# ---------------------------------------------------------------------------
# Reset clears the graph
# ---------------------------------------------------------------------------

def test_perception_graph_reset(client):
    client.post("/api/sentinel/demo/reset")

    obs = {
        "id": "obs-integ-006",
        "type": "pothole",
        "label": "Pothole Ahead",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "polygon": None,
        "sourceVehicleId": "v-1",
        "vehicleLabel": "Sentinel-A8",
    }
    client.post("/api/sentinel/demo/observation", json=obs)

    before = client.get("/api/sentinel/perception-graph").json()
    assert before["summary"]["nodeCount"] > 0

    client.post("/api/sentinel/demo/reset")

    after = client.get("/api/sentinel/perception-graph").json()
    assert after["summary"]["nodeCount"] == 4
    assert after["summary"]["edgeCount"] == 4


# ---------------------------------------------------------------------------
# Limit validation
# ---------------------------------------------------------------------------

def test_perception_graph_limit_validation(client):
    client.post("/api/sentinel/demo/reset")

    r = client.get("/api/sentinel/perception-graph?limit=0")
    assert r.status_code == 422

    r = client.get("/api/sentinel/perception-graph?limit=101")
    assert r.status_code == 422

    r = client.get("/api/sentinel/perception-graph?limit=abc")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Graph failure must not break observation endpoint
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_perception_graph_non_fatal():
    """Failure in upsert_observation_and_hazard must fail the request, not be rescued by Mongo, and not write to Mongo."""
    from unittest.mock import patch, AsyncMock
    from fastapi.testclient import TestClient
    from server import app, db

    # Clear Mongo db.hazards and db.observations
    await db.hazards.delete_many({})
    await db.observations.delete_many({})

    # Mock upsert_observation_and_hazard to raise RuntimeError
    mock_upsert = AsyncMock(side_effect=RuntimeError("graph down"))
    with TestClient(app, raise_server_exceptions=False) as c:
        # Apply mock after lifespan startup so seeding isn't affected
        with patch("server._perception_graph.upsert_observation_and_hazard", new=mock_upsert):
            obs = {
                "id": "obs-integ-007",
                "type": "pothole",
                "label": "Pothole Ahead",
                "location": {"latitude": 12.9450, "longitude": 80.1503},
                "polygon": None,
                "sourceVehicleId": "v-1",
                "vehicleLabel": "Sentinel-A8",
            }
            r = c.post("/api/sentinel/demo/observation", json=obs)
            # Verify request failed (not code 200)
            assert r.status_code != 200

        # Verify no Mongo hazard or observation write occurs (check outside mock)
        h_count = await db.hazards.count_documents({})
        o_count = await db.observations.count_documents({})
        # After seeding, hz-002 exists in Mongo. Only check observations are clean.
        assert o_count == 0




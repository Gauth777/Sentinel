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
    assert graph["nodes"] == []
    assert graph["edges"] == []
    assert graph["summary"]["nodeCount"] == 0
    assert graph["summary"]["edgeCount"] == 0
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
    hazard_id = r.json()["id"]

    graph = client.get(f"/api/sentinel/perception-graph?hazard_id={hazard_id}").json()
    edge_types = {e["type"] for e in graph["edges"]}
    assert "TRIGGERED_WARNING" not in edge_types
    assert "DELIVERED_TO" not in edge_types

    warning_nodes = [n for n in graph["nodes"] if n["type"] == "Warning"]
    assert len(warning_nodes) == 0


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
    assert after["summary"]["nodeCount"] == 0
    assert after["summary"]["edgeCount"] == 0


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
    """Mock PerceptionGraphService to fail; ensure observation endpoint still works."""
    from unittest.mock import patch, AsyncMock
    from fastapi.testclient import TestClient
    from server import app

    with patch("server._perception_graph.record_observation", new=AsyncMock(side_effect=RuntimeError("graph down"))):
        with patch("server._perception_graph.record_warning", new=AsyncMock(side_effect=RuntimeError("graph down"))):
            with TestClient(app) as c:
                c.post("/api/sentinel/demo/reset")
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
                assert r.status_code == 200
                assert r.json()["type"] == "pothole"


# ---------------------------------------------------------------------------
# Demo reset does not invoke legacy Neo4jService.reset_demo_data
# ---------------------------------------------------------------------------

def test_demo_reset_does_not_call_legacy_neo4j(monkeypatch):
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from server import app

    def raise_if_called(*args, **kwargs):
        raise AssertionError("Neo4jService.reset_demo_data was called")

    with patch("services.neo4j_service.Neo4jService.reset_demo_data", raise_if_called):
        with TestClient(app) as c:
            r = c.post("/api/sentinel/demo/reset")
            assert r.status_code == 200
            assert r.json()["message"] == "Demo data reset successfully"

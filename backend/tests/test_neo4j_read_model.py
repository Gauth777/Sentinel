import os
import sys

# Explicitly override Neo4j environment variables to disable Neo4j during unit tests
os.environ["NEO4J_ENABLED"] = "false"
os.environ["SENTINEL_NEO4J_STRICT"] = "false"
os.environ["DB_NAME"] = "test_sentinel_db"

import time
import asyncio
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from server import app, db, _perception_graph
from services.perception_graph_service import PerceptionGraphService, SCENARIO_ID

@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _clean_all():
    await _perception_graph.initialize()
    await _perception_graph.reset_demo_data()
    await db.hazards.delete_many({})
    await db.observations.delete_many({})
    await db.nearby_vehicles.delete_many({})
    await db.sentinel_meta.delete_many({})


def test_list_hazards_limit_validation():
    # Limit must be an integer, not bool
    with pytest.raises(ValueError, match="limit must be an integer"):
        asyncio.run(_perception_graph.list_hazards(limit=True))
    with pytest.raises(ValueError, match="limit must be an integer"):
        asyncio.run(_perception_graph.list_hazards(limit="50"))
    with pytest.raises(ValueError, match="limit must be an integer"):
        asyncio.run(_perception_graph.list_hazards(limit=0))
    with pytest.raises(ValueError, match="limit must be an integer"):
        asyncio.run(_perception_graph.list_hazards(limit=101))


@pytest.mark.anyio
async def test_list_hazards_memory_backend():
    await _clean_all()

    # Initially empty
    res = await _perception_graph.list_hazards()
    assert len(res) == 0

    # Seed one hazard
    now = time.time()
    await _perception_graph.upsert_observation_and_hazard(
        observation_id="obs-1",
        vehicle_id="v-1",
        vehicle_label="Sentinel-A8",
        hazard_id="hz-1",
        hazard_type="pothole",
        hazard_label="Pothole 1",
        latitude=12.94,
        longitude=80.15,
        road_segment_id="gst",
        road_segment_name="GST Road",
        timestamp=now - 10,
        hazard_fields={}
    )

    res = await _perception_graph.list_hazards()
    assert len(res) == 1
    assert res[0]["id"] == "hz-1"
    assert res[0]["segment_id"] == "gst"
    assert res[0]["sources"] == 1
    assert res[0]["source_vehicles"] == ["v-1"]
    assert res[0]["confidence"] == 60

    # Seed a second hazard
    await _perception_graph.upsert_observation_and_hazard(
        observation_id="obs-2",
        vehicle_id="v-2",
        vehicle_label="Sentinel-C2",
        hazard_id="hz-2",
        hazard_type="debris",
        hazard_label="Debris 1",
        latitude=12.95,
        longitude=80.16,
        road_segment_id="side",
        road_segment_name="Side Road",
        timestamp=now - 5,
        hazard_fields={}
    )

    # Ordering: updated_at desc (hz-2 then hz-1)
    res = await _perception_graph.list_hazards()
    assert len(res) == 2
    assert res[0]["id"] == "hz-2"
    assert res[1]["id"] == "hz-1"

    # Limit parameter works
    res_limited = await _perception_graph.list_hazards(limit=1)
    assert len(res_limited) == 1
    assert res_limited[0]["id"] == "hz-2"


@pytest.mark.anyio
async def test_baseline_graph_seed():
    await _clean_all()

    # Call seed helper
    from server import _seed_demo_graph_hazard, _seed_demo_graph_vehicles
    await _seed_demo_graph_vehicles()
    await _seed_demo_graph_hazard()

    # Verify exactly one Hazard and one Observation node exist
    hazards = await _perception_graph.list_hazards()
    assert len(hazards) == 1
    hz = hazards[0]
    assert hz["id"] == "hz-002"
    assert hz["type"] == "pothole"
    assert hz["segment_id"] == "gst"
    assert hz["source_vehicles"] == ["v-3"]

    # Verify idempotency
    await _seed_demo_graph_hazard()
    hazards = await _perception_graph.list_hazards()
    assert len(hazards) == 1


def test_get_hazards_endpoint():
    with TestClient(app) as client:
        # Trigger seed on reset
        client.post("/api/sentinel/demo/reset")

        response = client.get("/api/sentinel/hazards")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        hz_002 = [h for h in data if h["id"] == "hz-002"]
        assert len(hz_002) == 1
        assert hz_002[0]["type"] == "pothole"
        assert hz_002[0]["sources"] == 1


def test_get_world_model_endpoint():
    with TestClient(app) as client:
        # Seed vehicles to Mongo nearby_vehicles
        client.post("/api/sentinel/demo/reset")

        # 1. Demo Mode (no coordinates)
        response = client.get("/api/sentinel/world-model")
        assert response.status_code == 200
        data = response.json()
        assert data["scenarioId"] == "gst-northbound-blind-turn-v1"
        assert data["telemetrySource"] == "demo"
        assert len(data["hazards"]) >= 1
        assert data["hazards"][0]["id"] == "hz-002"

        # 2. Live Mode within radius
        response = client.get("/api/sentinel/world-model", params={
            "latitude": 12.9436,
            "longitude": 80.1502,
            "heading": 8.0,
            "radius_m": 500.0
        })
        assert response.status_code == 200
        data = response.json()
        assert data["telemetrySource"] == "live"
        assert len(data["hazards"]) >= 1

        # recalculation of distance is done
        assert data["hazards"][0]["distanceMeters"] > 0
        assert data["hazards"][0]["routeRelevance"] in ["low", "medium", "high", "none"]

        # 3. Live Mode outside radius
        response = client.get("/api/sentinel/world-model", params={
            "latitude": 12.0,
            "longitude": 80.0,
            "heading": 0.0,
            "radius_m": 10.0
        })
        assert response.status_code == 200
        data = response.json()
        assert len(data["hazards"]) == 0


def test_demo_reset_endpoint():
    with TestClient(app) as client:
        # Clean reset first
        r = client.post("/api/sentinel/demo/reset")
        assert r.status_code == 200

        # Post a dynamic observation
        obs_payload = {
            "id": "obs-dynamic-test",
            "type": "pothole",
            "label": "Dynamic Pothole",
            "location": {"latitude": 12.945, "longitude": 80.152},
            "polygon": None,
            "sourceVehicleId": "v-1",
            "vehicleLabel": "Sentinel-A8"
        }
        r_obs = client.post("/api/sentinel/demo/observation", json=obs_payload)
        assert r_obs.status_code == 200

        # Reset again
        r_reset = client.post("/api/sentinel/demo/reset")
        assert r_reset.status_code == 200

        # Verify only baseline state remains (hz-002 exists, but obs-dynamic-test is gone)
        r_hazards = client.get("/api/sentinel/hazards")
        assert r_hazards.status_code == 200
        data = r_hazards.json()
        assert len(data) == 1
        assert data[0]["id"] == "hz-002"


def test_graph_runtime_error_returns_503():
    with TestClient(app) as client:
        with patch.object(_perception_graph, "list_hazards", side_effect=RuntimeError("Graph down")):
            r = client.get("/api/sentinel/hazards")
            assert r.status_code == 503
            assert "Graph database error" in r.json()["detail"]

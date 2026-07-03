import os
import sys
import time
import asyncio
import inspect
import textwrap
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock

# Explicitly override Neo4j environment variables to disable Neo4j during unit tests
os.environ["NEO4J_ENABLED"] = "false"
os.environ["SENTINEL_NEO4J_STRICT"] = "false"
os.environ["DB_NAME"] = "test_sentinel_db"

import pytest
from fastapi.testclient import TestClient

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from server import app, db, _perception_graph, _seed_demo_graph_hazard, _seed_demo_graph_vehicles, ensure_seed
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


# ---------------------------------------------------------------------------
# list_hazards validation
# ---------------------------------------------------------------------------

def test_list_hazards_limit_validation():
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


# ---------------------------------------------------------------------------
# Baseline graph seed
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_baseline_graph_seed():
    await _clean_all()

    await _seed_demo_graph_vehicles()
    await _seed_demo_graph_hazard()

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


@pytest.mark.anyio
async def test_baseline_confidence_is_60():
    """Graph-derived confidence for 1 source vehicle must be 60, not overridden."""
    await _clean_all()
    await _seed_demo_graph_vehicles()
    await _seed_demo_graph_hazard()

    hazards = await _perception_graph.list_hazards()
    assert len(hazards) == 1
    assert hazards[0]["confidence"] == 60


@pytest.mark.anyio
async def test_baseline_seed_no_mongo_read():
    """_seed_demo_graph_hazard must not read from db.hazards."""
    await _clean_all()
    await _seed_demo_graph_vehicles()

    with patch.object(db.hazards, "find_one", side_effect=Exception("Mongo read forbidden")), \
         patch.object(db.hazards, "find", side_effect=Exception("Mongo read forbidden")):
        await _seed_demo_graph_hazard()

    hazards = await _perception_graph.list_hazards()
    assert len(hazards) == 1
    assert hazards[0]["id"] == "hz-002"


@pytest.mark.anyio
async def test_baseline_seed_no_private_access():
    """_seed_demo_graph_hazard must not access private graph backend members."""
    source = inspect.getsource(_seed_demo_graph_hazard)
    # Must not access private backend internals
    assert "_perception_graph._neo4j" not in source
    assert "_perception_graph._memory" not in source
    assert "_perception_graph._nodes" not in source
    assert "_perception_graph._mode" not in source
    assert "._run" not in source


@pytest.mark.anyio
async def test_baseline_seed_creates_no_warning():
    """Baseline seed must not create any Warning nodes."""
    await _clean_all()
    await _seed_demo_graph_vehicles()
    await _seed_demo_graph_hazard()

    # Check graph for Warning nodes
    graph = await _perception_graph.build_graph()
    warning_nodes = [n for n in graph["nodes"] if n.get("type") == "Warning"]
    assert len(warning_nodes) == 0


@pytest.mark.anyio
async def test_baseline_seed_idempotent():
    """Repeated seed produces identical results."""
    await _clean_all()
    await _seed_demo_graph_vehicles()
    await _seed_demo_graph_hazard()

    hazards1 = await _perception_graph.list_hazards()

    # Seed again
    await _seed_demo_graph_hazard()
    hazards2 = await _perception_graph.list_hazards()

    assert len(hazards1) == len(hazards2) == 1
    assert hazards1[0]["id"] == hazards2[0]["id"] == "hz-002"
    assert hazards1[0]["confidence"] == hazards2[0]["confidence"] == 60
    assert hazards1[0]["confirmed"] == hazards2[0]["confirmed"] == 0
    assert hazards1[0]["reportedIncorrect"] == hazards2[0]["reportedIncorrect"] == 0


# ---------------------------------------------------------------------------
# Graph-only GET route tests
# ---------------------------------------------------------------------------

def _make_db_hazards_forbidden():
    """Return a context manager that patches ALL db.hazards read methods to raise."""
    return patch.multiple(
        db.hazards,
        create=True,
        find=MagicMock(side_effect=Exception("Mongo hazards read forbidden")),
        find_one=AsyncMock(side_effect=Exception("Mongo hazards read forbidden")),
        find_one_and_update=AsyncMock(side_effect=Exception("Mongo hazards read forbidden")),
        aggregate=MagicMock(side_effect=Exception("Mongo hazards read forbidden")),
    )


def test_get_hazards_no_mongo_read():
    """GET /sentinel/hazards succeeds when all db.hazards read methods raise."""
    with TestClient(app) as client:
        client.post("/api/sentinel/demo/reset")
        with _make_db_hazards_forbidden():
            response = client.get("/api/sentinel/hazards")
            assert response.status_code == 200
            data = response.json()
            assert len(data) >= 1
            hz_002 = [h for h in data if h["id"] == "hz-002"]
            assert len(hz_002) == 1
            assert hz_002[0]["type"] == "pothole"
            assert hz_002[0]["sources"] == 1
            assert hz_002[0]["confidence"] == 60


def test_get_world_model_no_mongo_read():
    """GET /sentinel/world-model succeeds when all db.hazards read methods raise."""
    with TestClient(app) as client:
        client.post("/api/sentinel/demo/reset")
        with _make_db_hazards_forbidden():
            # Demo mode
            response = client.get("/api/sentinel/world-model")
            assert response.status_code == 200
            data = response.json()
            assert data["scenarioId"] == "gst-northbound-blind-turn-v1"
            assert len(data["hazards"]) >= 1
            assert data["hazards"][0]["id"] == "hz-002"

            # Live mode
            response = client.get("/api/sentinel/world-model", params={
                "latitude": 12.9436,
                "longitude": 80.1502,
                "heading": 8.0,
                "radius_m": 500.0
            })
            assert response.status_code == 200
            data = response.json()
            assert data["telemetrySource"] == "live"


def test_get_hazards_no_ensure_seed():
    """GET /sentinel/hazards must not call ensure_seed."""
    with TestClient(app) as client:
        client.post("/api/sentinel/demo/reset")
        with patch("server.ensure_seed", new_callable=AsyncMock, side_effect=Exception("ensure_seed called")) as mock_seed:
            response = client.get("/api/sentinel/hazards")
            assert response.status_code == 200
            mock_seed.assert_not_called()


def test_get_world_model_no_ensure_seed():
    """GET /sentinel/world-model must not call ensure_seed."""
    with TestClient(app) as client:
        client.post("/api/sentinel/demo/reset")
        with patch("server.ensure_seed", new_callable=AsyncMock, side_effect=Exception("ensure_seed called")) as mock_seed:
            response = client.get("/api/sentinel/world-model")
            assert response.status_code == 200
            mock_seed.assert_not_called()


def test_list_route_values_from_graph():
    """Confirm counter values come from graph properties, not Mongo."""
    with TestClient(app) as client:
        client.post("/api/sentinel/demo/reset")
        response = client.get("/api/sentinel/hazards")
        assert response.status_code == 200
        data = response.json()
        hz_002 = [h for h in data if h["id"] == "hz-002"][0]
        # Graph-derived values from SEED_HAZARDS constants
        assert hz_002["confirmed"] == 0
        assert hz_002["reportedIncorrect"] == 0
        assert hz_002["confidence"] == 60


# ---------------------------------------------------------------------------
# Live world-model resolved hazard filtering
# ---------------------------------------------------------------------------

def test_live_world_model_excludes_resolved():
    """Live world-model must exclude hazards with status != active."""
    with TestClient(app) as client:
        client.post("/api/sentinel/demo/reset")

        # Inject a resolved hazard into the graph
        import asyncio
        async def _inject_resolved():
            await _perception_graph.upsert_observation_and_hazard(
                observation_id="obs-resolved-1",
                vehicle_id="v-1",
                vehicle_label="Sentinel-A8",
                hazard_id="hz-resolved",
                hazard_type="debris",
                hazard_label="Cleared Debris",
                latitude=12.9440,
                longitude=80.1500,
                road_segment_id="gst",
                road_segment_name="GST Road Northbound",
                timestamp=time.time() - 10,
                hazard_fields={"status": "resolved", "distanceMeters": 50.0, "risk": "low"},
            )
        asyncio.run(_inject_resolved())

        # Live mode within radius — resolved hazard must be excluded
        response = client.get("/api/sentinel/world-model", params={
            "latitude": 12.9436,
            "longitude": 80.1502,
            "heading": 8.0,
            "radius_m": 2000.0
        })
        assert response.status_code == 200
        data = response.json()
        hazard_ids = [h["id"] for h in data["hazards"]]
        assert "hz-resolved" not in hazard_ids
        # Active hazard must still be present
        assert "hz-002" in hazard_ids


def test_demo_world_model_deterministic_ordering():
    """Demo world-model retains deterministic graph ordering."""
    with TestClient(app) as client:
        client.post("/api/sentinel/demo/reset")
        response = client.get("/api/sentinel/world-model")
        assert response.status_code == 200
        data = response.json()
        assert data["telemetrySource"] == "demo"
        assert len(data["hazards"]) >= 1
        assert data["hazards"][0]["id"] == "hz-002"


# ---------------------------------------------------------------------------
# Nearby vehicles still from Mongo
# ---------------------------------------------------------------------------

def test_nearby_vehicles_from_mongo():
    """Nearby vehicles must still come from db.nearby_vehicles."""
    with TestClient(app) as client:
        client.post("/api/sentinel/demo/reset")
        response = client.get("/api/sentinel/world-model")
        assert response.status_code == 200
        data = response.json()
        # Should have the seed vehicles
        assert len(data["nearbyVehicles"]) >= 1


# ---------------------------------------------------------------------------
# Reset behavior
# ---------------------------------------------------------------------------

def test_reset_clears_counters():
    """Reset must restore counters to deterministic seed values (0, 0)."""
    with TestClient(app) as client:
        client.post("/api/sentinel/demo/reset")

        # Inject a hazard with non-zero counters
        import asyncio
        async def _inject_countered():
            await _perception_graph.upsert_observation_and_hazard(
                observation_id="obs-countered",
                vehicle_id="v-2",
                vehicle_label="Sentinel-C2",
                hazard_id="hz-countered",
                hazard_type="debris",
                hazard_label="Counted Debris",
                latitude=12.946,
                longitude=80.151,
                road_segment_id="gst",
                road_segment_name="GST Road Northbound",
                timestamp=time.time() - 5,
                hazard_fields={"confirmed": 5, "reportedIncorrect": 3, "risk": "high"},
            )
        asyncio.run(_inject_countered())

        # Verify it exists
        response = client.get("/api/sentinel/hazards")
        data = response.json()
        assert any(h["id"] == "hz-countered" for h in data)

        # Reset
        r = client.post("/api/sentinel/demo/reset")
        assert r.status_code == 200

        # After reset, only baseline remains
        response = client.get("/api/sentinel/hazards")
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "hz-002"
        assert data[0]["confirmed"] == 0
        assert data[0]["reportedIncorrect"] == 0
        assert data[0]["confidence"] == 60


def test_reset_restores_one_baseline():
    """Reset produces exactly one hz-002 and one obs-seed-hz-002."""
    with TestClient(app) as client:
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
        client.post("/api/sentinel/demo/observation", json=obs_payload)

        # Reset
        r = client.post("/api/sentinel/demo/reset")
        assert r.status_code == 200

        # Verify only baseline remains
        response = client.get("/api/sentinel/hazards")
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "hz-002"


def test_reset_does_not_double_seed_graph():
    """Reset must seed graph vehicles/hazard exactly once, not twice."""
    original_seed_hazard = _seed_demo_graph_hazard

    call_count = {"hazard": 0, "vehicle": 0}
    original_seed_vehicles = _seed_demo_graph_vehicles

    async def counting_seed_hazard():
        call_count["hazard"] += 1
        return await original_seed_hazard()

    async def counting_seed_vehicles():
        call_count["vehicle"] += 1
        return await original_seed_vehicles()

    with TestClient(app) as client:
        with patch("server._seed_demo_graph_hazard", new=counting_seed_hazard), \
             patch("server._seed_demo_graph_vehicles", new=counting_seed_vehicles):
            call_count["hazard"] = 0
            call_count["vehicle"] = 0
            r = client.post("/api/sentinel/demo/reset")
            assert r.status_code == 200
            assert call_count["hazard"] == 1, f"Expected 1 graph hazard seed call, got {call_count['hazard']}"
            assert call_count["vehicle"] == 1, f"Expected 1 graph vehicle seed call, got {call_count['vehicle']}"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_graph_runtime_error_returns_503():
    with TestClient(app) as client:
        with patch.object(_perception_graph, "list_hazards", side_effect=RuntimeError("Graph down")):
            r = client.get("/api/sentinel/hazards")
            assert r.status_code == 503
            assert "Graph database error" in r.json()["detail"]


def test_world_model_graph_error_returns_503():
    with TestClient(app) as client:
        with patch.object(_perception_graph, "list_hazards", side_effect=RuntimeError("Graph down")):
            r = client.get("/api/sentinel/world-model")
            assert r.status_code == 503
            assert "Graph database error" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Memory vs Neo4j normalization equivalence
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_memory_list_hazards_normalization_schema():
    """Memory list_hazards returns all required normalized fields."""
    await _clean_all()
    await _seed_demo_graph_vehicles()
    await _seed_demo_graph_hazard()

    hazards = await _perception_graph.list_hazards()
    assert len(hazards) == 1
    hz = hazards[0]

    # Required fields from both Memory and Neo4j normalization
    required_keys = {
        "id", "type", "label", "location", "segment_id", "status",
        "created_at", "updated_at", "sources", "source_vehicles",
        "confidence", "confirmed", "reportedIncorrect", "distanceMeters",
        "direction", "recommendedAction", "risk", "visibilityState",
        "sourceType", "routeRelevance", "polygon",
    }
    missing = required_keys - set(hz.keys())
    assert not missing, f"Missing keys: {missing}"

    # Type checks
    assert isinstance(hz["location"], dict)
    assert "latitude" in hz["location"]
    assert "longitude" in hz["location"]
    assert isinstance(hz["sources"], int)
    assert isinstance(hz["confidence"], int)
    assert isinstance(hz["confirmed"], int)
    assert isinstance(hz["reportedIncorrect"], int)


# ---------------------------------------------------------------------------
# Live world-model outside radius
# ---------------------------------------------------------------------------

def test_live_world_model_outside_radius():
    """Live world-model outside radius returns no hazards."""
    with TestClient(app) as client:
        client.post("/api/sentinel/demo/reset")
        response = client.get("/api/sentinel/world-model", params={
            "latitude": 12.0,
            "longitude": 80.0,
            "heading": 0.0,
            "radius_m": 10.0
        })
        assert response.status_code == 200
        data = response.json()
        assert len(data["hazards"]) == 0

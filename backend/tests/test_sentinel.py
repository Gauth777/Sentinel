import os
import sys

# Ensure backend dir is on path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

os.environ["DB_NAME"] = "test_sentinel_db"

import pytest
from fastapi.testclient import TestClient
from server import app, db, MONGO_REACHABLE, mongo_url
from utils.mongo_mock import MOCK_SYNC_DB_STATE
from services.warning_service import WarningService
from services.neo4j_service import Neo4jService

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.post("/api/sentinel/demo/reset")
        yield c

@pytest.fixture(scope="module")
def sync_db():
    if MONGO_REACHABLE:
        from pymongo import MongoClient
        c = MongoClient(mongo_url)
        # Drop test DB at start to ensure clean test environment
        c.drop_database("test_sentinel_db")
        yield c["test_sentinel_db"]
        c.close()
    else:
        yield MOCK_SYNC_DB_STATE


# ===== Status =====
def test_status(client):
    r = client.get("/api/sentinel/status")
    assert r.status_code == 200
    d = r.json()
    assert "_id" not in d
    assert d["connected"] is True
    assert d["gps_locked"] is True
    assert d["network"] == "4G"
    assert d["speed_kmh"] == 42
    assert d["road_name"] == "GST Road Northbound"
    assert d["heading"] == "N"
    assert d["sentinel_vehicles_nearby"] == 4


# ===== Hazards =====
def test_hazards(client):
    client.post("/api/sentinel/demo/reset")

    r = client.get("/api/sentinel/hazards")
    assert r.status_code == 200

    arr = r.json()
    assert isinstance(arr, list)
    assert {h["id"] for h in arr} == {"hz-002"}

    hz = arr[0]
    assert "_id" not in hz
    assert hz["label"] == "Deep Pothole"
    assert hz["distanceMeters"] == 340
    assert hz["confidence"] == 76
    assert hz["sources"] == 1
    assert hz["risk"] == "medium"
    assert hz["recommendedAction"] == "Move left"
    assert hz["routeRelevance"] == "medium"
    assert hz["visibilityState"] == "hidden"
    assert hz["sourceType"] == "shared_vehicle"

    assert all(h["type"] != "stationary_vehicle" for h in arr)


# ===== Nearby Vehicles =====
def test_nearby_vehicles(client):
    r = client.get("/api/sentinel/nearby-vehicles")
    assert r.status_code == 200
    arr = r.json()
    assert len(arr) == 4
    for v in arr:
        assert "_id" not in v
        assert {"id", "label", "location", "heading_degrees"}.issubset(v.keys())
        assert "latitude" in v["location"] and "longitude" in v["location"]


# ===== World model =====
def test_world_model(client):
    r = client.get("/api/sentinel/world-model")
    assert r.status_code == 200
    wm = r.json()
    assert "_id" not in wm
    assert wm["scenarioId"] == "gst-northbound-blind-turn-v1"
    assert wm["telemetrySource"] in ("live", "cached", "demo")
    assert "ego" in wm and "location" in wm["ego"]
    assert "mapBounds" in wm and "southWest" in wm["mapBounds"]
    assert isinstance(wm["roads"], list) and len(wm["roads"]) >= 1
    assert isinstance(wm["buildings"], list) and len(wm["buildings"]) >= 1
    assert isinstance(wm["occupiedRegions"], list) and len(wm["occupiedRegions"]) >= 1
    assert isinstance(wm["nearbyVehicles"], list) and len(wm["nearbyVehicles"]) == 4
    assert isinstance(wm["hazards"], list) and len(wm["hazards"]) >= 1
    types = {o["sourceType"] for o in wm["occupiedRegions"]}
    assert "local_sensor" in types
    obj_types = {o["objectType"] for o in wm["occupiedRegions"]}
    assert "unknown" in obj_types


# ===== Confirm =====
def test_confirm_increments(client):
    client.post("/api/sentinel/demo/reset")
    r1 = client.post("/api/sentinel/hazards/hz-002/confirm")
    assert r1.status_code == 200
    d1 = r1.json()
    assert "_id" not in d1
    assert d1["id"] == "hz-002"
    assert d1["confirmed"] == 1
    r2 = client.post("/api/sentinel/hazards/hz-002/confirm")
    assert r2.status_code == 200
    assert r2.json()["confirmed"] == 1  # idempotent unique vote


# ===== Report incorrect =====
def test_report_increments(client):
    client.post("/api/sentinel/demo/reset")
    r1 = client.post("/api/sentinel/hazards/hz-002/report-incorrect")
    assert r1.status_code == 200
    d1 = r1.json()
    assert "_id" not in d1
    assert d1["reportedIncorrect"] == 1
    r2 = client.post("/api/sentinel/hazards/hz-002/report-incorrect")
    assert r2.status_code == 200
    assert r2.json()["reportedIncorrect"] == 1  # idempotent unique vote


# ===== 404 =====
def test_404_unknown(client):
    r = client.post("/api/sentinel/hazards/does-not-exist/confirm")
    assert r.status_code == 404


# ===== Demo seed migration =====
def test_old_schema_migration(client, sync_db):
    """Inject a legacy-shape hazard with the same id as a known demo doc, reset
    the seed-version sentinel, then call any endpoint and confirm that the
    record was upserted into the new geo schema."""
    # Insert legacy record (no `location`, normalised x/y).
    legacy_hz = {
        "id": "hz-002",
        "type": "pothole",
        "label": "OLD: pothole",
        "x": 0.5,
        "y": 0.2,
        "distance_m": 999,
        "confidence": 10,
        "sources": 1,
        "observed_seconds_ago": 99,
        "direction": "old",
        "recommended_action": "old",
        "risk": "low",
        "confirmed": 5,
        "reportedIncorrect": 2,
    }
    sync_db.hazards.replace_one({"id": "hz-002"}, legacy_hz, upsert=True)
    # Force re-migration by clearing the version sentinel.
    sync_db.sentinel_meta.delete_many({"id": "seed"})

    # Trigger ensure_seed via any endpoint.
    r = client.get("/api/sentinel/hazards")
    assert r.status_code == 200
    hz = next(h for h in r.json() if h["id"] == "hz-002")
    # Must now be the new schema.
    assert "location" in hz and "latitude" in hz["location"]
    assert hz["distanceMeters"] == 180
    assert hz["risk"] == "high"
    # Counters preserved.
    assert hz["confirmed"] >= 5
    assert hz["reportedIncorrect"] >= 2


def test_repeated_seeding_is_idempotent(client, sync_db):
    """Calling endpoints repeatedly must not produce duplicates."""
    before = sync_db.hazards.count_documents({"id": "hz-002"})
    assert before == 1
    for _ in range(5):
        client.get("/api/sentinel/world-model").raise_for_status()
    after = sync_db.hazards.count_documents({"id": "hz-002"})
    assert after == 1
    assert sync_db.nearby_vehicles.count_documents({"id": "v-1"}) == 1


def test_seed_meta_records_version(client, sync_db):
    client.get("/api/sentinel/status").raise_for_status()
    meta = sync_db.sentinel_meta.find_one({"id": "seed"})
    assert meta is not None
    assert isinstance(meta.get("version"), int)
    assert meta["version"] >= 2


# ===== Matching & Observation Processing =====
def test_new_observation_submission(client, sync_db):
    client.post("/api/sentinel/demo/reset")

    # Clean baseline: the shared stationary-vehicle Ghost must not exist yet.
    baseline = client.get("/api/sentinel/world-model")
    assert baseline.status_code == 200
    assert all(
        hazard["type"] != "stationary_vehicle"
        for hazard in baseline.json()["hazards"]
    )

    obs = {
        "id": "obs-demo-stationary-001",
        "type": "stationary_vehicle",
        "label": "Stationary Vehicle Ahead",
        "location": {
            "latitude": 12.9452,
            "longitude": 80.1506,
        },
        "polygon": [
            {"latitude": 12.9451, "longitude": 80.1505},
            {"latitude": 12.9451, "longitude": 80.1507},
            {"latitude": 12.9453, "longitude": 80.1507},
            {"latitude": 12.9453, "longitude": 80.1505},
        ],
        "sourceVehicleId": "v-1",
        "vehicleLabel": "Sentinel-A8",
    }

    # Observer creates a new shared hazard.
    r1 = client.post("/api/sentinel/demo/observation", json=obs)
    assert r1.status_code == 200

    created = r1.json()
    assert created["type"] == "stationary_vehicle"
    assert created["sources"] == 1
    assert created["confidence"] == 60
    assert created["sourceType"] == "shared_vehicle"
    assert created["visibilityState"] == "hidden"

    # Approaching vehicle receives it through the updated world model.
    after_submission = client.get("/api/sentinel/world-model")
    assert after_submission.status_code == 200

    stationary_hazards = [
        hazard
        for hazard in after_submission.json()["hazards"]
        if hazard["type"] == "stationary_vehicle"
    ]

    assert len(stationary_hazards) == 1
    assert stationary_hazards[0]["id"] == created["id"]

    # Same observation ID is idempotent.
    r2 = client.post("/api/sentinel/demo/observation", json=obs)
    assert r2.status_code == 200

    duplicate = r2.json()
    assert duplicate["id"] == created["id"]
    assert duplicate["sources"] == 1

    after_duplicate = client.get("/api/sentinel/world-model").json()
    stationary_after_duplicate = [
        hazard
        for hazard in after_duplicate["hazards"]
        if hazard["type"] == "stationary_vehicle"
    ]
    assert len(stationary_after_duplicate) == 1

    # A second independent vehicle should merge into the same hazard.
    second_observation = {
        **obs,
        "id": "obs-demo-stationary-002",
        "sourceVehicleId": "v-2",
        "vehicleLabel": "Sentinel-C2",
        "location": {
            "latitude": 12.94522,
            "longitude": 80.1505,
        },
    }

    r3 = client.post(
        "/api/sentinel/demo/observation",
        json=second_observation,
    )
    assert r3.status_code == 200

    corroborated = r3.json()
    assert corroborated["id"] == created["id"]
    assert corroborated["sources"] == 2
    assert corroborated["confidence"] in (79, 80)

    # Reset removes the dynamic hazard and restores the clean baseline.
    reset = client.post("/api/sentinel/demo/reset")
    assert reset.status_code == 200

    after_reset = client.get("/api/sentinel/world-model")
    assert after_reset.status_code == 200
    assert all(
        hazard["type"] != "stationary_vehicle"
        for hazard in after_reset.json()["hazards"]
    )

# ===== Warning translations =====
def test_warning_translation_generation():
    w = WarningService.generate_warning_texts("stationary_vehicle", 100, "Reduce speed")
    assert "Stationary vehicle approximately 100 metres ahead. Reduce speed." in w["en"]
    assert "लगभग 100 मीटर आगे एक रुका हुआ वाहन है। गति कम करें।" in w["hi"]
    assert "100 metre aage stationary vehicle hai. Speed kam karein." in w["hinglish"]

    w = WarningService.generate_warning_texts("pothole", 50, "Move left")
    assert "Pothole approximately 50 metres ahead. Move left." in w["en"]
    assert "लगभग 50 मीटर आगे एक गड्ढा है। बाईं ओर चलें।" in w["hi"]
    assert "50 metre aage pothole hai. Left move karein." in w["hinglish"]


# ===== Neo4j fallback operations =====
@pytest.mark.anyio
async def test_neo4j_service_operations(sync_db):
    # Test Neo4jService directly (calls async methods, which will fall back to MongoDB)
    await Neo4jService.record_vehicle("v-test-neo", "Test Neo Vehicle")
    await Neo4jService.record_road_segment("test-segment", "Test segment road")
    await Neo4jService.record_vehicle_approaching("v-test-neo", "test-segment")
    await Neo4jService.record_hazard("hz-test-neo", "test-segment", {"type": "pothole", "label": "Pothole"})
    
    relevant = await Neo4jService.get_relevant_hazards("v-test-neo")
    assert "hz-test-neo" in relevant

    # Link mock observation
    await Neo4jService.record_observation(
        "obs-neo-1", "v-test-neo", "hz-test-neo", {"type": "pothole", "label": "Pothole"}
    )
    provenance = await Neo4jService.get_hazard_provenance("hz-test-neo")
    assert len(provenance) > 0
    assert provenance[0]["vehicle_id"] == "v-test-neo"

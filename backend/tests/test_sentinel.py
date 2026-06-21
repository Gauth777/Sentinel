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
    r = client.get("/api/sentinel/hazards")
    assert r.status_code == 200
    arr = r.json()
    assert isinstance(arr, list) and len(arr) >= 2
    for item in arr:
        assert "_id" not in item
    hz = next((h for h in arr if h["id"] == "hz-001"), None)
    assert hz is not None
    assert hz["label"] == "Stationary Vehicle Ahead"
    assert hz["distanceMeters"] == 180
    assert hz["confidence"] == 91
    assert hz["sources"] == 2
    assert hz["risk"] == "high"
    assert hz["direction"] == "Northbound lane"
    assert hz["recommendedAction"] == "Reduce speed"
    assert hz["routeRelevance"] == "high"
    assert hz["visibilityState"] == "hidden"
    assert hz["sourceType"] == "shared_vehicle"
    loc = hz["location"]
    assert "latitude" in loc and "longitude" in loc
    assert 12.94 < loc["latitude"] < 12.95
    assert 80.14 < loc["longitude"] < 80.16


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
    r1 = client.post("/api/sentinel/hazards/hz-001/confirm")
    assert r1.status_code == 200
    d1 = r1.json()
    assert "_id" not in d1
    assert d1["id"] == "hz-001"
    assert d1["confirmed"] == 1
    r2 = client.post("/api/sentinel/hazards/hz-001/confirm")
    assert r2.status_code == 200
    assert r2.json()["confirmed"] == 1  # idempotent unique vote


# ===== Report incorrect =====
def test_report_increments(client):
    client.post("/api/sentinel/demo/reset")
    r1 = client.post("/api/sentinel/hazards/hz-001/report-incorrect")
    assert r1.status_code == 200
    d1 = r1.json()
    assert "_id" not in d1
    assert d1["reportedIncorrect"] == 1
    r2 = client.post("/api/sentinel/hazards/hz-001/report-incorrect")
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
        "id": "hz-001",
        "type": "stationary_vehicle",
        "label": "OLD: vehicle",
        "x": 0.5,
        "y": 0.2,
        "distance_m": 999,
        "confidence": 10,
        "sources": 1,
        "observed_seconds_ago": 99,
        "direction": "old",
        "recommended_action": "old",
        "risk": "low",
        "confirmed": 5,            # counters must be preserved
        "reportedIncorrect": 2,
    }
    sync_db.hazards.replace_one({"id": "hz-001"}, legacy_hz, upsert=True)
    # Force re-migration by clearing the version sentinel.
    sync_db.sentinel_meta.delete_many({"id": "seed"})

    # Trigger ensure_seed via any endpoint.
    r = client.get("/api/sentinel/hazards")
    assert r.status_code == 200
    hz = next(h for h in r.json() if h["id"] == "hz-001")
    # Must now be the new schema.
    assert "location" in hz and "latitude" in hz["location"]
    assert hz["distanceMeters"] == 180
    assert hz["risk"] == "high"
    # Counters preserved.
    assert hz["confirmed"] >= 5
    assert hz["reportedIncorrect"] >= 2


def test_repeated_seeding_is_idempotent(client, sync_db):
    """Calling endpoints repeatedly must not produce duplicates."""
    before = sync_db.hazards.count_documents({"id": "hz-001"})
    assert before == 1
    for _ in range(5):
        client.get("/api/sentinel/world-model").raise_for_status()
    after = sync_db.hazards.count_documents({"id": "hz-001"})
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
    # Reset demo data to ensure a clean slate (resets counters)
    client.post("/api/sentinel/demo/reset")
    # Post a new observation
    obs = {
        "id": "obs-test-new-123",
        "type": "stationary_vehicle",
        "label": "Test Stationary Vehicle",
        "location": {
            "latitude": 12.9452,
            "longitude": 80.1506
        },
        "polygon": [
            {"latitude": 12.9451, "longitude": 80.1505},
            {"latitude": 12.9453, "longitude": 80.1507}
        ],
        "sourceVehicleId": "v-1",
        "vehicleLabel": "Sentinel-A8"
    }
    r = client.post("/api/sentinel/demo/observation", json=obs)
    assert r.status_code == 200
    hazard = r.json()
    assert hazard["id"].startswith("hz-")
    assert hazard["type"] == "stationary_vehicle"
    assert hazard["confidence"] == 60  # 1 source
    assert hazard["sources"] == 1
    
    # Test idempotency (submit same observation ID)
    r2 = client.post("/api/sentinel/demo/observation", json=obs)
    assert r2.status_code == 200
    hazard2 = r2.json()
    assert hazard2["id"] == hazard["id"]
    
    # Submit matching observation from another vehicle (v-2) within radius (50m)
    obs_matching = {
        "id": "obs-test-new-456",
        "type": "stationary_vehicle",
        "label": "Test Stationary Vehicle 2",
        "location": {
            "latitude": 12.94522,
            "longitude": 80.1505
        },
        "sourceVehicleId": "v-2",
        "vehicleLabel": "Sentinel-C2"
    }
    r3 = client.post("/api/sentinel/demo/observation", json=obs_matching)
    assert r3.status_code == 200
    hazard3 = r3.json()
    assert hazard3["id"] == hazard["id"]  # matched!
    assert hazard3["sources"] == 2
    assert hazard3["confidence"] in (79, 80)
    
    # Check warnings are generated in English, Hindi, Hinglish
    assert "warnings" in hazard3
    assert "en" in hazard3["warnings"]
    assert "hi" in hazard3["warnings"]
    assert "hinglish" in hazard3["warnings"]


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

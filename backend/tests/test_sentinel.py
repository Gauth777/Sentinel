"""Sentinel backend API tests — phase 3.

Defaults to the LOCAL backend (`http://127.0.0.1:8001`). Set `SENTINEL_TEST_URL`
to point at a different deployment if you need to test the hosted preview.

Run from the repo root:
    cd /app/backend
    pytest tests/test_sentinel.py -v
"""
import os
import time
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("SENTINEL_TEST_URL", "http://127.0.0.1:8001").rstrip("/")
API = f"{BASE_URL}/api"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")


@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    sess.headers.update({"Content-Type": "application/json"})
    # Wait for backend to be ready (up to 10s).
    for _ in range(20):
        try:
            r = sess.get(f"{API}/sentinel/status", timeout=2)
            if r.status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(0.5)
    return sess


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


# ===== Status =====
def test_status(s):
    r = s.get(f"{API}/sentinel/status", timeout=15)
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


# ===== Hazards (new geo schema) =====
def test_hazards(s):
    r = s.get(f"{API}/sentinel/hazards", timeout=15)
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
def test_nearby_vehicles(s):
    r = s.get(f"{API}/sentinel/nearby-vehicles", timeout=15)
    assert r.status_code == 200
    arr = r.json()
    assert len(arr) == 4
    for v in arr:
        assert "_id" not in v
        assert {"id", "label", "location", "heading_degrees"}.issubset(v.keys())
        assert "latitude" in v["location"] and "longitude" in v["location"]


# ===== World model (with response_model validation) =====
def test_world_model(s):
    r = s.get(f"{API}/sentinel/world-model", timeout=15)
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
def test_confirm_increments(s):
    r1 = s.post(f"{API}/sentinel/hazards/hz-001/confirm", timeout=15)
    assert r1.status_code == 200
    d1 = r1.json()
    assert "_id" not in d1
    assert d1["id"] == "hz-001"
    before = d1["confirmed"]
    r2 = s.post(f"{API}/sentinel/hazards/hz-001/confirm", timeout=15)
    assert r2.status_code == 200
    assert r2.json()["confirmed"] == before + 1


# ===== Report incorrect =====
def test_report_increments(s):
    r1 = s.post(f"{API}/sentinel/hazards/hz-001/report-incorrect", timeout=15)
    assert r1.status_code == 200
    d1 = r1.json()
    assert "_id" not in d1
    before = d1["reportedIncorrect"]
    r2 = s.post(f"{API}/sentinel/hazards/hz-001/report-incorrect", timeout=15)
    assert r2.status_code == 200
    assert r2.json()["reportedIncorrect"] == before + 1


# ===== 404 =====
def test_404_unknown(s):
    r = s.post(f"{API}/sentinel/hazards/does-not-exist/confirm", timeout=15)
    assert r.status_code == 404


# ===== Demo seed migration =====
def test_old_schema_migration(s, mongo):
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
    mongo.hazards.replace_one({"id": "hz-001"}, legacy_hz, upsert=True)
    # Force re-migration by clearing the version sentinel.
    mongo.sentinel_meta.delete_many({"id": "seed"})

    # Trigger ensure_seed via any endpoint.
    r = s.get(f"{API}/sentinel/hazards", timeout=15)
    assert r.status_code == 200
    hz = next(h for h in r.json() if h["id"] == "hz-001")
    # Must now be the new schema.
    assert "location" in hz and "latitude" in hz["location"]
    assert hz["distanceMeters"] == 180
    assert hz["risk"] == "high"
    # Counters preserved.
    assert hz["confirmed"] >= 5
    assert hz["reportedIncorrect"] >= 2


def test_repeated_seeding_is_idempotent(s, mongo):
    """Calling endpoints repeatedly must not produce duplicates."""
    before = mongo.hazards.count_documents({"id": "hz-001"})
    assert before == 1
    for _ in range(5):
        s.get(f"{API}/sentinel/world-model", timeout=15).raise_for_status()
    after = mongo.hazards.count_documents({"id": "hz-001"})
    assert after == 1
    assert mongo.nearby_vehicles.count_documents({"id": "v-1"}) == 1


def test_seed_meta_records_version(s, mongo):
    s.get(f"{API}/sentinel/status", timeout=15).raise_for_status()
    meta = mongo.sentinel_meta.find_one({"id": "seed"})
    assert meta is not None
    assert isinstance(meta.get("version"), int)
    assert meta["version"] >= 2

"""Sentinel backend API tests — phase 2 (structured world model)."""
import os
import pytest
import requests

BASE_URL = os.environ.get(
    "EXPO_PUBLIC_BACKEND_URL", "https://road-guard-2.preview.emergentagent.com"
).rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    sess.headers.update({"Content-Type": "application/json"})
    return sess


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
    assert isinstance(arr, list) and len(arr) >= 1
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


# ===== World model =====
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
    # Ensure at least one Ghost (shared/hidden) and one local-sensor occupied region exist.
    types = {o["sourceType"] for o in wm["occupiedRegions"]}
    assert "local_sensor" in types
    # And one unknown object exists (semantic-free representation).
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

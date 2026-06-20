"""Sentinel backend API tests"""
import os
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://road-guard-2.preview.emergentagent.com").rstrip("/")
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


# ===== Hazards =====
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
    assert hz["distance_m"] == 180
    assert hz["confidence"] == 91
    assert hz["sources"] == 2
    assert hz["risk"] == "high"
    assert hz["direction"] == "Northbound lane"
    assert hz["recommended_action"] == "Reduce speed"


# ===== Nearby Vehicles =====
def test_nearby_vehicles(s):
    r = s.get(f"{API}/sentinel/nearby-vehicles", timeout=15)
    assert r.status_code == 200
    arr = r.json()
    assert len(arr) == 4
    for v in arr:
        assert "_id" not in v
        assert {"id", "x", "y", "heading_deg", "label"}.issubset(v.keys())


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
    before = d1["reported_incorrect"]
    r2 = s.post(f"{API}/sentinel/hazards/hz-001/report-incorrect", timeout=15)
    assert r2.status_code == 200
    assert r2.json()["reported_incorrect"] == before + 1


# ===== 404 =====
def test_confirm_404(s):
    r = s.post(f"{API}/sentinel/hazards/does-not-exist/confirm", timeout=15)
    assert r.status_code == 404


def test_report_404(s):
    r = s.post(f"{API}/sentinel/hazards/does-not-exist/report-incorrect", timeout=15)
    assert r.status_code == 404

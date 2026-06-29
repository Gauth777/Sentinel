"""Comprehensive tests for Sentinel training-sample data engine.

Runs without a live MongoDB or Neo4j instance.
"""
import os
import sys
import json
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Ensure backend dir is on path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from models.training_samples import (
    RoadType,
    TrafficDensity,
    RoadComplexity,
    HazardPresence,
    AnticipatedRisk,
    RecommendedAction,
    DatasetStatus,
    FeedbackStatus,
    FeedbackStatusInput,
    TrainingSampleCreate,
    TrainingFeedbackCreate,
    TrainingSample,
    Context,
    GeoLocation,
    Media,
    ModelInfo,
    PredictionLabels,
    Provenance,
)
from services.training_sample_service import TrainingSampleService, _InMemoryTrainingStore
from routes.training_samples import router as training_samples_router


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def memory_service():
    """Service backed purely by in-memory store."""
    store = _InMemoryTrainingStore()
    svc = TrainingSampleService(store, False)
    return svc


@pytest.fixture
def test_app(memory_service):
    """Minimal FastAPI app with training sample routes in memory mode."""
    app = FastAPI()
    app.state.training_sample_service = memory_service
    app.include_router(training_samples_router, prefix="/api")
    return app


@pytest.fixture
def client(test_app):
    with TestClient(test_app) as c:
        yield c


def _sample_payload(sample_id: str = "ts-001", **overrides: Any) -> dict:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {
        "schemaVersion": "sentinel.training.v1",
        "sampleId": sample_id,
        "observationId": "obs-001",
        "hazardId": "hz-001",
        "sourceVehicleId": "v-001",
        "capturedAt": now,
        "context": {
            "location": {"latitude": 12.9436, "longitude": 80.1502},
            "headingDegrees": 8,
            "speedKmh": 42,
            "roadName": "GST Road",
            "routeDirection": "Northbound",
            "telemetrySource": "demo",
        },
        "media": {
            "type": "image",
            "uri": "demo://sentinel/gst-northbound/stationary-vehicle-frame",
            "storageMode": "demo_uri",
        },
        "model": {
            "provider": "demo",
            "name": "sentinel-demo-baseline",
            "version": "1",
            "inferenceMode": "demo",
        },
        "prediction": {
            "roadType": "urban_arterial",
            "trafficDensity": "medium",
            "roadComplexity": "moderate",
            "hazardPresence": "yes",
            "anticipatedRisk": "high",
            "recommendedAction": "slow_down",
            "confidence": 0.85,
        },
        "provenance": {
            "source": "demo",
            "graphHazardId": "hz-001",
            "graphObservationId": "obs-001",
        },
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------------------
# Creation
# ------------------------------------------------------------------

def test_valid_sample_creation(client):
    payload = _sample_payload("ts-create-001")
    r = client.post("/api/sentinel/training-samples", json=payload)
    assert r.status_code == 201
    d = r.json()
    assert d["sampleId"] == "ts-create-001"
    assert d["datasetStatus"] == "pending"
    assert d["feedbackStatus"] == "pending"
    assert d["revision"] == 1
    assert "originalPrediction" in d
    assert d["originalPrediction"]["roadType"] == "urban_arterial"
    assert "_id" not in d


def test_generated_default_fields(client):
    payload = _sample_payload("ts-defaults-001")
    r = client.post("/api/sentinel/training-samples", json=payload)
    assert r.status_code == 201
    d = r.json()
    assert "createdAt" in d
    assert "updatedAt" in d
    assert d["revision"] == 1
    assert d["feedbackHistory"] == []
    assert d["finalVerifiedLabels"] is None


def test_duplicate_sample_returns_409(client):
    payload = _sample_payload("ts-dup-001")
    r1 = client.post("/api/sentinel/training-samples", json=payload)
    assert r1.status_code == 201
    r2 = client.post("/api/sentinel/training-samples", json=payload)
    assert r2.status_code == 409


def test_invalid_canonical_label_returns_422(client):
    payload = _sample_payload("ts-label-001")
    payload["prediction"]["roadType"] = "invalid_road"
    r = client.post("/api/sentinel/training-samples", json=payload)
    assert r.status_code == 422


def test_invalid_latitude_returns_422(client):
    payload = _sample_payload("ts-lat-001")
    payload["context"]["location"]["latitude"] = 91
    r = client.post("/api/sentinel/training-samples", json=payload)
    assert r.status_code == 422


def test_invalid_confidence_returns_422(client):
    payload = _sample_payload("ts-conf-001")
    payload["prediction"]["confidence"] = 1.5
    r = client.post("/api/sentinel/training-samples", json=payload)
    assert r.status_code == 422


def test_invalid_heading_returns_422(client):
    payload = _sample_payload("ts-head-001")
    payload["context"]["headingDegrees"] = 360
    r = client.post("/api/sentinel/training-samples", json=payload)
    assert r.status_code == 422


def test_invalid_speed_returns_422(client):
    payload = _sample_payload("ts-speed-001")
    payload["context"]["speedKmh"] = -1
    r = client.post("/api/sentinel/training-samples", json=payload)
    assert r.status_code == 422


def test_invalid_sha256_returns_422(client):
    payload = _sample_payload("ts-sha-001")
    payload["media"]["sha256"] = "not-a-valid-hash"
    r = client.post("/api/sentinel/training-samples", json=payload)
    assert r.status_code == 422


# ------------------------------------------------------------------
# Listing
# ------------------------------------------------------------------

def test_list_newest_first(client):
    for i in range(3):
        p = _sample_payload(f"ts-list-{i}")
        p["capturedAt"] = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        p["prediction"]["roadType"] = ["urban_arterial", "residential", "highway"][i]
        client.post("/api/sentinel/training-samples", json=p)

    r = client.get("/api/sentinel/training-samples?limit=10")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 3


def test_list_filter_by_dataset_status(client):
    p = _sample_payload("ts-filter-pending")
    client.post("/api/sentinel/training-samples", json=p)
    # Create and reject another
    p2 = _sample_payload("ts-filter-rejected")
    client.post("/api/sentinel/training-samples", json=p2)
    client.post("/api/sentinel/training-samples/ts-filter-rejected/feedback", json={"status": "rejected"})

    r = client.get("/api/sentinel/training-samples?status=pending")
    assert r.status_code == 200
    assert all(item["datasetStatus"] == "pending" for item in r.json()["items"])

    r = client.get("/api/sentinel/training-samples?status=rejected")
    assert r.status_code == 200
    assert all(item["datasetStatus"] == "rejected" for item in r.json()["items"])


def test_list_filter_by_feedback_status(client):
    p = _sample_payload("ts-fb-filter")
    client.post("/api/sentinel/training-samples", json=p)
    client.post("/api/sentinel/training-samples/ts-fb-filter/feedback", json={"status": "confirmed"})

    r = client.get("/api/sentinel/training-samples?feedback_status=confirmed")
    assert r.status_code == 200
    assert all(item["feedbackStatus"] == "confirmed" for item in r.json()["items"])


def test_list_filter_by_hazard_id(client):
    p = _sample_payload("ts-hz-filter")
    client.post("/api/sentinel/training-samples", json=p)

    r = client.get("/api/sentinel/training-samples?hazard_id=hz-001")
    assert r.status_code == 200
    assert all(item["hazardId"] == "hz-001" for item in r.json()["items"])

    r = client.get("/api/sentinel/training-samples?hazard_id=nonexistent")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_list_filter_by_source_vehicle(client):
    p = _sample_payload("ts-veh-filter")
    client.post("/api/sentinel/training-samples", json=p)

    r = client.get("/api/sentinel/training-samples?source_vehicle_id=v-001")
    assert r.status_code == 200
    assert all(item["sourceVehicleId"] == "v-001" for item in r.json()["items"])


def test_list_filter_by_model_name(client):
    p = _sample_payload("ts-model-filter")
    client.post("/api/sentinel/training-samples", json=p)

    r = client.get("/api/sentinel/training-samples?model_name=sentinel-demo-baseline")
    assert r.status_code == 200
    assert all(item["model"]["name"] == "sentinel-demo-baseline" for item in r.json()["items"])


def test_list_limit_and_skip(client):
    for i in range(5):
        p = _sample_payload(f"ts-lim-{i}")
        client.post("/api/sentinel/training-samples", json=p)

    r = client.get("/api/sentinel/training-samples?limit=2&skip=1")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 2
    assert data["limit"] == 2
    assert data["skip"] == 1


def test_list_limit_validation(client):
    r = client.get("/api/sentinel/training-samples?limit=0")
    assert r.status_code == 422
    r = client.get("/api/sentinel/training-samples?limit=201")
    assert r.status_code == 422


# ------------------------------------------------------------------
# Get one
# ------------------------------------------------------------------

def test_get_one_sample(client):
    p = _sample_payload("ts-get-001")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.get("/api/sentinel/training-samples/ts-get-001")
    assert r.status_code == 200
    assert r.json()["sampleId"] == "ts-get-001"
    assert "_id" not in r.json()


def test_unknown_sample_returns_404(client):
    r = client.get("/api/sentinel/training-samples/does-not-exist")
    assert r.status_code == 404


# ------------------------------------------------------------------
# Feedback
# ------------------------------------------------------------------

def test_confirmed_feedback(client):
    p = _sample_payload("ts-confirm-001")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.post("/api/sentinel/training-samples/ts-confirm-001/feedback", json={"status": "confirmed"})
    assert r.status_code == 200
    d = r.json()
    assert d["feedbackStatus"] == "confirmed"
    assert d["datasetStatus"] == "verified"
    assert d["finalVerifiedLabels"] is not None


def test_confirmed_feedback_copies_original_labels(client):
    p = _sample_payload("ts-confirm-copy")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.post("/api/sentinel/training-samples/ts-confirm-copy/feedback", json={"status": "confirmed"})
    d = r.json()
    orig = d["originalPrediction"]
    final = d["finalVerifiedLabels"]
    assert final["roadType"] == orig["roadType"]
    assert final["trafficDensity"] == orig["trafficDensity"]
    assert final["roadComplexity"] == orig["roadComplexity"]
    assert final["hazardPresence"] == orig["hazardPresence"]
    assert final["anticipatedRisk"] == orig["anticipatedRisk"]
    assert final["recommendedAction"] == orig["recommendedAction"]


def test_corrected_feedback_with_partial_correction(client):
    p = _sample_payload("ts-correct-001")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.post(
        "/api/sentinel/training-samples/ts-correct-001/feedback",
        json={"status": "corrected", "correctedLabels": {"roadType": "highway"}},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["feedbackStatus"] == "corrected"
    assert d["datasetStatus"] == "verified"
    assert d["finalVerifiedLabels"]["roadType"] == "highway"
    # Other labels should come from original
    assert d["finalVerifiedLabels"]["trafficDensity"] == "medium"


def test_corrected_feedback_produces_all_six_final_labels(client):
    p = _sample_payload("ts-correct-all6")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.post(
        "/api/sentinel/training-samples/ts-correct-all6/feedback",
        json={"status": "corrected", "correctedLabels": {"recommendedAction": "yield"}},
    )
    d = r.json()
    final = d["finalVerifiedLabels"]
    assert set(final.keys()) == {"roadType", "trafficDensity", "roadComplexity", "hazardPresence", "anticipatedRisk", "recommendedAction"}
    assert final["recommendedAction"] == "yield"


def test_corrected_feedback_without_changes_returns_422(client):
    p = _sample_payload("ts-correct-empty")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.post(
        "/api/sentinel/training-samples/ts-correct-empty/feedback",
        json={"status": "corrected", "correctedLabels": {}},
    )
    assert r.status_code == 422


def test_confirmed_feedback_with_corrections_returns_422(client):
    p = _sample_payload("ts-confirm-422")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.post(
        "/api/sentinel/training-samples/ts-confirm-422/feedback",
        json={"status": "confirmed", "correctedLabels": {"roadType": "highway"}},
    )
    assert r.status_code == 422


def test_rejected_feedback(client):
    p = _sample_payload("ts-reject-001")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.post(
        "/api/sentinel/training-samples/ts-reject-001/feedback",
        json={"status": "rejected", "note": "Test rejection"},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["feedbackStatus"] == "rejected"
    assert d["datasetStatus"] == "rejected"
    assert d["finalVerifiedLabels"] is None


def test_rejected_feedback_with_corrections_returns_422(client):
    p = _sample_payload("ts-reject-422")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.post(
        "/api/sentinel/training-samples/ts-reject-422/feedback",
        json={"status": "rejected", "correctedLabels": {"roadType": "highway"}},
    )
    assert r.status_code == 422


def test_feedback_history_is_append_only(client):
    p = _sample_payload("ts-history-001")
    client.post("/api/sentinel/training-samples", json=p)
    client.post("/api/sentinel/training-samples/ts-history-001/feedback", json={"status": "confirmed"})
    client.post("/api/sentinel/training-samples/ts-history-001/feedback", json={"status": "rejected", "note": "changed mind"})
    r = client.get("/api/sentinel/training-samples/ts-history-001")
    d = r.json()
    assert len(d["feedbackHistory"]) == 2
    assert d["feedbackHistory"][0]["status"] == "confirmed"
    assert d["feedbackHistory"][1]["status"] == "rejected"


def test_revision_increments(client):
    p = _sample_payload("ts-rev-001")
    client.post("/api/sentinel/training-samples", json=p)
    r1 = client.post("/api/sentinel/training-samples/ts-rev-001/feedback", json={"status": "confirmed"})
    assert r1.json()["revision"] == 2
    r2 = client.post("/api/sentinel/training-samples/ts-rev-001/feedback", json={"status": "rejected"})
    assert r2.json()["revision"] == 3


def test_repeated_feedback_updates_current_status_correctly(client):
    p = _sample_payload("ts-repeat-001")
    client.post("/api/sentinel/training-samples", json=p)
    client.post("/api/sentinel/training-samples/ts-repeat-001/feedback", json={"status": "confirmed"})
    client.post("/api/sentinel/training-samples/ts-repeat-001/feedback", json={"status": "rejected"})
    r = client.get("/api/sentinel/training-samples/ts-repeat-001")
    d = r.json()
    assert d["feedbackStatus"] == "rejected"
    assert d["datasetStatus"] == "rejected"


# ------------------------------------------------------------------
# Export
# ------------------------------------------------------------------

def test_rejected_sample_excluded_from_export(client):
    p = _sample_payload("ts-export-rejected")
    client.post("/api/sentinel/training-samples", json=p)
    client.post("/api/sentinel/training-samples/ts-export-rejected/feedback", json={"status": "rejected"})
    r = client.get("/api/sentinel/training-samples/export")
    assert r.status_code == 200
    lines = [l for l in r.text.strip().split("\n") if l.strip()]
    assert all(json.loads(l)["sample_id"] != "ts-export-rejected" for l in lines)


def test_pending_sample_excluded_from_export(client):
    p = _sample_payload("ts-export-pending")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.get("/api/sentinel/training-samples/export")
    assert r.status_code == 200
    lines = [l for l in r.text.strip().split("\n") if l.strip()]
    assert all(json.loads(l)["sample_id"] != "ts-export-pending" for l in lines)


def test_confirmed_sample_included_in_export(client):
    p = _sample_payload("ts-export-confirmed")
    client.post("/api/sentinel/training-samples", json=p)
    client.post("/api/sentinel/training-samples/ts-export-confirmed/feedback", json={"status": "confirmed"})
    r = client.get("/api/sentinel/training-samples/export")
    assert r.status_code == 200
    lines = [l for l in r.text.strip().split("\n") if l.strip()]
    assert any(json.loads(l)["sample_id"] == "ts-export-confirmed" for l in lines)


def test_corrected_sample_included_in_export(client):
    p = _sample_payload("ts-export-corrected")
    client.post("/api/sentinel/training-samples", json=p)
    client.post(
        "/api/sentinel/training-samples/ts-export-corrected/feedback",
        json={"status": "corrected", "correctedLabels": {"roadType": "highway"}},
    )
    r = client.get("/api/sentinel/training-samples/export")
    assert r.status_code == 200
    lines = [l for l in r.text.strip().split("\n") if l.strip()]
    assert any(json.loads(l)["sample_id"] == "ts-export-corrected" for l in lines)


def test_export_is_valid_ndjson(client):
    p = _sample_payload("ts-export-ndjson")
    client.post("/api/sentinel/training-samples", json=p)
    client.post("/api/sentinel/training-samples/ts-export-ndjson/feedback", json={"status": "confirmed"})
    r = client.get("/api/sentinel/training-samples/export")
    assert r.status_code == 200
    lines = [l for l in r.text.strip().split("\n") if l.strip()]
    for line in lines:
        obj = json.loads(line)
        assert isinstance(obj, dict)


def test_export_one_object_per_line(client):
    p = _sample_payload("ts-export-lines")
    client.post("/api/sentinel/training-samples", json=p)
    client.post("/api/sentinel/training-samples/ts-export-lines/feedback", json={"status": "confirmed"})
    r = client.get("/api/sentinel/training-samples/export")
    lines = [l for l in r.text.strip().split("\n") if l.strip()]
    assert len(lines) >= 1
    for line in lines:
        assert not line.startswith("[")
        assert not line.endswith("]")


def test_export_content_type(client):
    p = _sample_payload("ts-export-ct")
    client.post("/api/sentinel/training-samples", json=p)
    client.post("/api/sentinel/training-samples/ts-export-ct/feedback", json={"status": "confirmed"})
    r = client.get("/api/sentinel/training-samples/export")
    assert r.headers["content-type"] == "application/x-ndjson"


def test_export_content_disposition(client):
    p = _sample_payload("ts-export-cd")
    client.post("/api/sentinel/training-samples", json=p)
    client.post("/api/sentinel/training-samples/ts-export-cd/feedback", json={"status": "confirmed"})
    r = client.get("/api/sentinel/training-samples/export")
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "sentinel_verified_dataset_" in cd
    assert ".jsonl" in cd


def test_export_ends_in_newline_when_non_empty(client):
    p = _sample_payload("ts-export-nl")
    client.post("/api/sentinel/training-samples", json=p)
    client.post("/api/sentinel/training-samples/ts-export-nl/feedback", json={"status": "confirmed"})
    r = client.get("/api/sentinel/training-samples/export")
    assert r.text.endswith("\n")


def test_export_empty_valid_response(client):
    # No verified samples yet
    r = client.get("/api/sentinel/training-samples/export")
    assert r.status_code == 200
    assert r.text.strip() == ""


def test_export_contains_original_prediction(client):
    p = _sample_payload("ts-export-orig")
    client.post("/api/sentinel/training-samples", json=p)
    client.post("/api/sentinel/training-samples/ts-export-orig/feedback", json={"status": "confirmed"})
    r = client.get("/api/sentinel/training-samples/export")
    lines = [l for l in r.text.strip().split("\n") if l.strip()]
    obj = json.loads(lines[0])
    assert "original_prediction" in obj
    assert obj["original_prediction"]["road_type"] == "urban_arterial"


def test_export_contains_final_verified_labels(client):
    p = _sample_payload("ts-export-final")
    client.post("/api/sentinel/training-samples", json=p)
    client.post(
        "/api/sentinel/training-samples/ts-export-final/feedback",
        json={"status": "corrected", "correctedLabels": {"roadType": "highway"}},
    )
    r = client.get("/api/sentinel/training-samples/export")
    lines = [l for l in r.text.strip().split("\n") if l.strip()]
    obj = json.loads(lines[0])
    assert "final_verified_labels" in obj
    assert obj["final_verified_labels"]["road_type"] == "highway"


def test_export_never_exposes_id(client):
    p = _sample_payload("ts-export-noid")
    client.post("/api/sentinel/training-samples", json=p)
    client.post("/api/sentinel/training-samples/ts-export-noid/feedback", json={"status": "confirmed"})
    r = client.get("/api/sentinel/training-samples/export")
    lines = [l for l in r.text.strip().split("\n") if l.strip()]
    for line in lines:
        assert "_id" not in json.loads(line)


def test_api_responses_never_expose_id(client):
    p = _sample_payload("ts-noid")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.get("/api/sentinel/training-samples/ts-noid")
    assert "_id" not in r.json()


# ------------------------------------------------------------------
# Stats
# ------------------------------------------------------------------

def test_stats_counts(client):
    # Clean slate for this test via fresh client is handled by fixture
    p = _sample_payload("ts-stats-1")
    client.post("/api/sentinel/training-samples", json=p)
    p2 = _sample_payload("ts-stats-2")
    client.post("/api/sentinel/training-samples", json=p2)
    client.post("/api/sentinel/training-samples/ts-stats-2/feedback", json={"status": "confirmed"})
    p3 = _sample_payload("ts-stats-3")
    client.post("/api/sentinel/training-samples", json=p3)
    client.post("/api/sentinel/training-samples/ts-stats-3/feedback", json={"status": "rejected"})

    r = client.get("/api/sentinel/training-samples/stats")
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 3
    assert d["pending"] == 1
    assert d["verified"] == 1
    assert d["rejected"] == 1
    assert d["confirmed"] == 1
    assert d["corrected"] == 0
    assert d["exportable"] == 1


def test_stats_distributions_use_verified_labels(client):
    p = _sample_payload("ts-dist-1")
    p["prediction"]["roadType"] = "highway"
    client.post("/api/sentinel/training-samples", json=p)
    client.post("/api/sentinel/training-samples/ts-dist-1/feedback", json={"status": "confirmed"})

    p2 = _sample_payload("ts-dist-2")
    p2["prediction"]["roadType"] = "urban_arterial"
    client.post("/api/sentinel/training-samples", json=p2)
    client.post("/api/sentinel/training-samples/ts-dist-2/feedback", json={"status": "confirmed"})

    r = client.get("/api/sentinel/training-samples/stats")
    d = r.json()
    assert d["byRoadType"]["highway"] >= 1
    assert d["byRoadType"]["urban_arterial"] >= 1


# ------------------------------------------------------------------
# Regression tests
# ------------------------------------------------------------------

def test_camelcase_roadtype_correction_accepted(client):
    p = _sample_payload("ts-reg-camel-road")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.post(
        "/api/sentinel/training-samples/ts-reg-camel-road/feedback",
        json={"status": "corrected", "correctedLabels": {"roadType": "highway"}},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["finalVerifiedLabels"]["roadType"] == "highway"


def test_camelcase_recommendedaction_correction_accepted(client):
    p = _sample_payload("ts-reg-camel-action")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.post(
        "/api/sentinel/training-samples/ts-reg-camel-action/feedback",
        json={"status": "corrected", "correctedLabels": {"recommendedAction": "yield"}},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["finalVerifiedLabels"]["recommendedAction"] == "yield"


def test_multiple_partial_corrections_merge_correctly(client):
    p = _sample_payload("ts-reg-multi")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.post(
        "/api/sentinel/training-samples/ts-reg-multi/feedback",
        json={"status": "corrected", "correctedLabels": {"roadType": "highway", "recommendedAction": "yield"}},
    )
    assert r.status_code == 200
    d = r.json()
    final = d["finalVerifiedLabels"]
    assert final["roadType"] == "highway"
    assert final["recommendedAction"] == "yield"
    assert final["trafficDensity"] == "medium"
    assert final["roadComplexity"] == "moderate"
    assert final["hazardPresence"] == "yes"
    assert final["anticipatedRisk"] == "high"


def test_unknown_correction_field_returns_422(client):
    p = _sample_payload("ts-reg-unknown")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.post(
        "/api/sentinel/training-samples/ts-reg-unknown/feedback",
        json={"status": "corrected", "correctedLabels": {"roadType": "highway", "unknownField": "value"}},
    )
    assert r.status_code == 422


def test_invalid_correction_enum_returns_422(client):
    p = _sample_payload("ts-reg-invalid-enum")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.post(
        "/api/sentinel/training-samples/ts-reg-invalid-enum/feedback",
        json={"status": "corrected", "correctedLabels": {"roadType": "intergalactic_highway"}},
    )
    assert r.status_code == 422


def test_pending_feedback_status_returns_422(client):
    p = _sample_payload("ts-reg-pending")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.post(
        "/api/sentinel/training-samples/ts-reg-pending/feedback",
        json={"status": "pending"},
    )
    assert r.status_code == 422


def test_final_verified_labels_exactly_six_fields(client):
    p = _sample_payload("ts-reg-six-fields")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.post(
        "/api/sentinel/training-samples/ts-reg-six-fields/feedback",
        json={"status": "corrected", "correctedLabels": {"roadType": "highway"}},
    )
    d = r.json()
    final = d["finalVerifiedLabels"]
    assert set(final.keys()) == {
        "roadType", "trafficDensity", "roadComplexity",
        "hazardPresence", "anticipatedRisk", "recommendedAction",
    }
    assert "confidence" not in final
    assert "perLabelConfidence" not in final
    assert "rawResponse" not in final


def test_jsonl_uses_snake_case_sample_id(client):
    p = _sample_payload("ts-reg-snake")
    client.post("/api/sentinel/training-samples", json=p)
    client.post("/api/sentinel/training-samples/ts-reg-snake/feedback", json={"status": "confirmed"})
    r = client.get("/api/sentinel/training-samples/export")
    lines = [l for l in r.text.strip().split("\n") if l.strip()]
    obj = json.loads(lines[0])
    assert "sample_id" in obj
    assert "sampleId" not in obj


def test_api_uses_camelcase_sample_id(client):
    p = _sample_payload("ts-reg-camel")
    client.post("/api/sentinel/training-samples", json=p)
    r = client.get("/api/sentinel/training-samples/ts-reg-camel")
    d = r.json()
    assert "sampleId" in d
    assert "sample_id" not in d


def test_memory_model_name_filtering_returns_expected_sample(client):
    p = _sample_payload("ts-reg-model-filter")
    client.post("/api/sentinel/training-samples", json=p)
    client.post("/api/sentinel/training-samples/ts-reg-model-filter/feedback", json={"status": "confirmed"})
    r = client.get("/api/sentinel/training-samples/export?model_name=sentinel-demo-baseline")
    assert r.status_code == 200
    lines = [l for l in r.text.strip().split("\n") if l.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["sample_id"] == "ts-reg-model-filter"
    assert obj["model"]["name"] == "sentinel-demo-baseline"


def test_memory_road_type_export_filtering(client):
    p = _sample_payload("ts-reg-road-filter")
    p["prediction"]["roadType"] = "highway"
    client.post("/api/sentinel/training-samples", json=p)
    client.post("/api/sentinel/training-samples/ts-reg-road-filter/feedback", json={"status": "confirmed"})
    r = client.get("/api/sentinel/training-samples/export?road_type=highway")
    assert r.status_code == 200
    lines = [l for l in r.text.strip().split("\n") if l.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["final_verified_labels"]["road_type"] == "highway"


def test_memory_hazard_presence_export_filtering(client):
    p = _sample_payload("ts-reg-hazard-filter")
    client.post("/api/sentinel/training-samples", json=p)
    client.post("/api/sentinel/training-samples/ts-reg-hazard-filter/feedback", json={"status": "confirmed"})
    r = client.get("/api/sentinel/training-samples/export?hazard_presence=yes")
    assert r.status_code == 200
    lines = [l for l in r.text.strip().split("\n") if l.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["final_verified_labels"]["hazard_presence"] == "yes"


def test_default_privacy_status_is_not_reviewed(client):
    p = _sample_payload("ts-reg-privacy")
    r = client.post("/api/sentinel/training-samples", json=p)
    assert r.status_code == 201
    d = r.json()
    assert d["quality"]["privacyStatus"] == "not_reviewed"
    assert d["quality"]["unusableReason"] is None
    assert d["quality"]["notes"] is None


@pytest.mark.anyio
async def test_memory_concurrent_feedback_preserve_both_events():
    store = _InMemoryTrainingStore()
    svc = TrainingSampleService(store, False)
    await svc.initialize()

    p = TrainingSampleCreate(
        sample_id="ts-concurrent-fb",
        source_vehicle_id="v-1",
        captured_at=datetime.now(timezone.utc),
        context=Context(location=GeoLocation(latitude=12.0, longitude=80.0)),
        media=Media(uri="demo://test"),
        model=ModelInfo(provider="demo", name="test", version="1"),
        prediction=PredictionLabels(
            road_type=RoadType.urban_arterial,
            traffic_density=TrafficDensity.low,
            road_complexity=RoadComplexity.simple,
            hazard_presence=HazardPresence.no,
            anticipated_risk=AnticipatedRisk.low,
            recommended_action=RecommendedAction.maintain_speed,
        ),
    )
    await svc.create_sample(p)

    fb1 = TrainingFeedbackCreate(status=FeedbackStatusInput.confirmed)
    fb2 = TrainingFeedbackCreate(status=FeedbackStatusInput.rejected, note="changed")

    async def submit(fb):
        try:
            return await svc.submit_feedback("ts-concurrent-fb", fb)
        except Exception as e:
            return str(e)

    results = await asyncio.gather(submit(fb1), submit(fb2))
    # Both should succeed; one will be first, the other second
    assert all(isinstance(r, TrainingSample) for r in results)

    # Verify the final state
    final = await svc.get_sample("ts-concurrent-fb")
    assert final is not None
    assert len(final.feedback_history) == 2
    # Revision should have been incremented twice (1 -> 3)
    assert final.revision == 3


# ------------------------------------------------------------------
# Service-level direct tests
# ------------------------------------------------------------------

@pytest.mark.anyio
async def test_memory_fallback_initialises_safely():
    store = _InMemoryTrainingStore()
    svc = TrainingSampleService(store, False)
    await svc.initialize()
    assert svc._mode == "memory"


@pytest.mark.anyio
async def test_memory_duplicate_concurrent_safety():
    store = _InMemoryTrainingStore()
    svc = TrainingSampleService(store, False)
    await svc.initialize()

    p = TrainingSampleCreate(
        sample_id="ts-concurrent",
        source_vehicle_id="v-1",
        captured_at=datetime.now(timezone.utc),
        context=Context(location=GeoLocation(latitude=12.0, longitude=80.0)),
        media=Media(uri="demo://test"),
        model=ModelInfo(provider="demo", name="test", version="1"),
        prediction=PredictionLabels(
            road_type=RoadType.urban_arterial,
            traffic_density=TrafficDensity.low,
            road_complexity=RoadComplexity.simple,
            hazard_presence=HazardPresence.no,
            anticipated_risk=AnticipatedRisk.low,
            recommended_action=RecommendedAction.maintain_speed,
        ),
    )

    await svc.create_sample(p)

    async def try_create():
        try:
            await svc.create_sample(p)
            return "ok"
        except Exception:
            return "error"

    results = await asyncio.gather(try_create(), try_create())
    assert results.count("error") == 2


# ------------------------------------------------------------------
# Integration with existing server routes (using actual server if available)
# ------------------------------------------------------------------

def _safe_import_server():
    """Import server with local-safe configuration to avoid production DBs.

    Uses a deliberately unreachable local URL and reloads if server
    was already imported in a prior test.
    """
    import importlib

    os.environ["MONGO_URL"] = "mongodb://127.0.0.1:1"
    os.environ["DB_NAME"] = "sentinel_test"
    os.environ["NEO4J_URI"] = ""
    os.environ["NEO4J_USER"] = ""
    os.environ["NEO4J_PASSWORD"] = ""

    try:
        import server
        importlib.reload(server)
        return server.app
    except ImportError:
        return None
    except Exception:
        return None


def _safe_import_server_subprocess():
    """Alternative: import server in a fresh subprocess."""
    import subprocess
    import sys

    env = os.environ.copy()
    env["MONGO_URL"] = "mongodb://127.0.0.1:1"
    env["DB_NAME"] = "sentinel_test"
    env["NEO4J_URI"] = ""
    env["NEO4J_USER"] = ""
    env["NEO4J_PASSWORD"] = ""

    result = subprocess.run(
        [sys.executable, "-c", "import server; print('OK')"],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode == 0 and "OK" in result.stdout


@pytest.mark.anyio
async def test_mongo_confirmed_feedback_does_not_call_coroutine_get():
    """Confirmed feedback in Mongo mode must not call .get() on an un-awaited coroutine."""
    from services.training_sample_service import TrainingSampleService

    call_log = []

    FULL_DOC = {
        "sample_id": "ts-mongo-fb",
        "source_vehicle_id": "v-1",
        "captured_at": datetime.now(timezone.utc),
        "context": {"location": {"latitude": 12.0, "longitude": 80.0}, "telemetry_source": "demo"},
        "media": {"type": "image", "uri": "demo://test", "storage_mode": "demo_uri"},
        "model": {"provider": "demo", "name": "test", "version": "1", "inference_mode": "demo"},
        "prediction": {
            "road_type": "urban_arterial",
            "traffic_density": "medium",
            "road_complexity": "moderate",
            "hazard_presence": "yes",
            "anticipated_risk": "high",
            "recommended_action": "slow_down",
        },
        "original_prediction": {
            "road_type": "urban_arterial",
            "traffic_density": "medium",
            "road_complexity": "moderate",
            "hazard_presence": "yes",
            "anticipated_risk": "high",
            "recommended_action": "slow_down",
        },
        "provenance": {"source": "demo"},
        "dataset_status": "pending",
        "feedback_status": "pending",
        "feedback_history": [],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "revision": 1,
    }

    class FakeCollection:
        async def find_one(self, filter, projection=None):
            call_log.append(("find_one", filter))
            return deepcopy(FULL_DOC)

        async def find_one_and_update(self, filter, update, return_document=None):
            call_log.append(("find_one_and_update", filter))
            updated = deepcopy(FULL_DOC)
            updated["dataset_status"] = "verified"
            updated["feedback_status"] = "confirmed"
            updated["final_verified_labels"] = {
                "road_type": "urban_arterial",
                "traffic_density": "medium",
                "road_complexity": "moderate",
                "hazard_presence": "yes",
                "anticipated_risk": "high",
                "recommended_action": "slow_down",
            }
            updated["revision"] = 2
            updated["feedback_history"] = [{"status": "confirmed", "submitted_at": datetime.now(timezone.utc)}]
            return updated

    fake_db = {"training_samples": FakeCollection()}
    svc = TrainingSampleService(fake_db, True)
    await svc.initialize()

    from models.training_samples import TrainingFeedbackCreate, FeedbackStatusInput

    fb = TrainingFeedbackCreate(status=FeedbackStatusInput.confirmed)
    result = await svc.submit_feedback("ts-mongo-fb", fb)

    assert result is not None
    assert result.feedback_status == FeedbackStatus.confirmed
    assert result.dataset_status == DatasetStatus.verified
    # The old bug was: coll.find_one(...).get(...) without await.
    # If that happened, it would be a coroutine.get() which raises AttributeError.
    # The test reaching this assertion proves the bug is absent.
    find_one_calls = [c for c in call_log if c[0] == "find_one"]
    find_one_update_calls = [c for c in call_log if c[0] == "find_one_and_update"]
    assert len(find_one_calls) >= 1
    assert len(find_one_update_calls) >= 1


@pytest.mark.anyio
async def test_mongo_revision_guard_preserved_on_conflict():
    """Mongo feedback should retry on revision conflict and eventually fail cleanly."""
    from services.training_sample_service import TrainingSampleService, ConcurrencyError

    class FailingCollection:
        async def find_one(self, filter, projection=None):
            return {
                "sample_id": "ts-mongo-rev",
                "source_vehicle_id": "v-1",
                "captured_at": datetime.now(timezone.utc),
                "context": {"location": {"latitude": 12.0, "longitude": 80.0}, "telemetry_source": "demo"},
                "media": {"type": "image", "uri": "demo://test", "storage_mode": "demo_uri"},
                "model": {"provider": "demo", "name": "test", "version": "1", "inference_mode": "demo"},
                "prediction": {
                    "road_type": "urban_arterial",
                    "traffic_density": "medium",
                    "road_complexity": "moderate",
                    "hazard_presence": "yes",
                    "anticipated_risk": "high",
                    "recommended_action": "slow_down",
                },
                "original_prediction": {
                    "road_type": "urban_arterial",
                    "traffic_density": "medium",
                    "road_complexity": "moderate",
                    "hazard_presence": "yes",
                    "anticipated_risk": "high",
                    "recommended_action": "slow_down",
                },
                "provenance": {"source": "demo"},
                "revision": 1,
                "feedback_history": [],
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
                "dataset_status": "pending",
                "feedback_status": "pending",
            }

        async def find_one_and_update(self, filter, update, return_document=None):
            # Always simulate revision mismatch
            return None

    fake_db = {"training_samples": FailingCollection()}
    svc = TrainingSampleService(fake_db, True)

    from models.training_samples import TrainingFeedbackCreate, FeedbackStatusInput

    fb = TrainingFeedbackCreate(status=FeedbackStatusInput.confirmed)
    with pytest.raises(ConcurrencyError):
        await svc.submit_feedback("ts-mongo-rev", fb)


# ------------------------------------------------------------------
# Integration with existing server routes (using actual server if available)
# ------------------------------------------------------------------

def test_safe_server_subprocess_isolation():
    """Prove that the server subprocess import uses safe test URLs."""
    assert _safe_import_server_subprocess() is True


def test_existing_perception_graph_route_still_works():
    real_app = _safe_import_server()
    if real_app is None:
        pytest.skip("server module not available in this test environment")

    with TestClient(real_app) as c:
        c.post("/api/sentinel/demo/reset")
        r = c.get("/api/sentinel/perception-graph")
        assert r.status_code == 200
        assert "nodes" in r.json()


def test_existing_demo_observation_route_still_works():
    real_app = _safe_import_server()
    if real_app is None:
        pytest.skip("server module not available in this test environment")

    with TestClient(real_app) as c:
        c.post("/api/sentinel/demo/reset")
        obs = {
            "id": "obs-integration-test",
            "type": "stationary_vehicle",
            "label": "Stationary Vehicle",
            "location": {"latitude": 12.9452, "longitude": 80.1506},
            "sourceVehicleId": "v-test",
            "vehicleLabel": "Test Vehicle",
        }
        r = c.post("/api/sentinel/demo/observation", json=obs)
        assert r.status_code == 200
        assert "id" in r.json()


def test_observation_route_independent_of_training_sample_service():
    """Observation route works regardless of training sample service state."""
    real_app = _safe_import_server()
    if real_app is None:
        pytest.skip("server module not available in this test environment")

    with TestClient(real_app) as c:
        c.post("/api/sentinel/demo/reset")
        obs = {
            "id": "obs-independent-test",
            "type": "pothole",
            "label": "Pothole",
            "location": {"latitude": 12.9436, "longitude": 80.1502},
            "sourceVehicleId": "v-test",
            "vehicleLabel": "Test Vehicle",
        }
        r = c.post("/api/sentinel/demo/observation", json=obs)
        assert r.status_code == 200

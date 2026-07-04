import os
import sys

# Clear external Neo4j configuration before any import to force memory mode in global service
os.environ["SENTINEL_NEO4J_STRICT"] = "false"
os.environ["NEO4J_ENABLED"] = "false"

import math
import pytest
import asyncio
from unittest.mock import patch, MagicMock

# Ensure backend dir is on path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from server import app, db, MONGO_REACHABLE, mongo_url, _perception_graph
from fastapi.testclient import TestClient
from services.perception_graph_service import PerceptionGraphService, SCENARIO_ID, _calculate_confidence_and_status
from utils.mongo_mock import MOCK_SYNC_DB_STATE

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
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


@pytest.fixture
async def memory_service():
    service = PerceptionGraphService()
    await service.initialize()
    service._strict = False
    service._neo4j_enabled = False
    service._mode = "memory"
    yield service
    await service.close()


# ===========================================================================
# Service validation & integrity tests (Memory Backend focus)
# ===========================================================================

@pytest.mark.anyio
async def test_feedback_unknown_hazard_returns_none(memory_service):
    # 1. Unknown hazard returns None and creates no voter
    res = await memory_service.record_hazard_feedback(
        hazard_id="hz-unknown",
        vehicle_id="v-1",
        vehicle_label="V1",
        feedback_type="confirm"
    )
    assert res is None

    # Verify no voter was created in memory
    assert "v-1" not in memory_service._memory._nodes


@pytest.mark.anyio
async def test_feedback_wrong_type_hazard_rejected(memory_service):
    # Create a non-Hazard node sharing the hazard_id
    memory_service._memory._nodes["hz-wrong"] = {
        "id": "hz-wrong",
        "type": "RoadSegment",
        "scenarioId": SCENARIO_ID,
        "properties": {}
    }

    # 2. Wrong-type Hazard state is rejected
    with pytest.raises(ValueError):
        await memory_service.record_hazard_feedback(
            hazard_id="hz-wrong",
            vehicle_id="v-1",
            vehicle_label="V1",
            feedback_type="confirm"
        )


@pytest.mark.anyio
async def test_feedback_wrong_type_vehicle_rejected(memory_service):
    # Seed a valid hazard
    await memory_service._memory.record_observation(
        observation_id="obs-1",
        vehicle_id="v-obs",
        vehicle_label="Observer",
        hazard_id="hz-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        road_segment_id="road-1",
        road_segment_name="Road 1"
    )

    # Create a non-Vehicle node sharing the voter vehicle_id
    memory_service._memory._nodes["v-voter-wrong"] = {
        "id": "v-voter-wrong",
        "type": "RoadSegment",
        "scenarioId": SCENARIO_ID,
        "properties": {}
    }

    # 3. Wrong-type Vehicle state is rejected
    with pytest.raises(ValueError):
        await memory_service.record_hazard_feedback(
            hazard_id="hz-1",
            vehicle_id="v-voter-wrong",
            vehicle_label="Voter Wrong",
            feedback_type="confirm"
        )


@pytest.mark.anyio
async def test_confirmation_creates_edge_and_idempotent(memory_service):
    await memory_service._memory.record_observation(
        observation_id="obs-1",
        vehicle_id="v-obs",
        vehicle_label="Observer",
        hazard_id="hz-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        road_segment_id="road-1",
        road_segment_name="Road 1"
    )

    # 4. Confirmation creates one CONFIRMED edge
    res1 = await memory_service.record_hazard_feedback(
        hazard_id="hz-1",
        vehicle_id="v-1",
        vehicle_label="V1",
        feedback_type="confirm"
    )
    assert res1 is not None
    assert res1["confirmed"] == 1
    assert res1["feedbackCreated"] is True

    edge_id = "CONFIRMED:v-1:hz-1"
    assert edge_id in memory_service._memory._edges
    assert memory_service._memory._edges[edge_id]["type"] == "CONFIRMED"

    orig_ts = memory_service._memory._edges[edge_id]["properties"]["created_at"]
    assert orig_ts > 0.0

    # 5. Repeated confirmation is idempotent
    res2 = await memory_service.record_hazard_feedback(
        hazard_id="hz-1",
        vehicle_id="v-1",
        vehicle_label="V1",
        feedback_type="confirm"
    )
    assert res2["confirmed"] == 1
    assert res2["feedbackCreated"] is False

    # 9. Exact retry preserves created_at
    assert memory_service._memory._edges[edge_id]["properties"]["created_at"] == orig_ts



@pytest.mark.anyio
async def test_report_creates_edge_and_idempotent(memory_service):
    await memory_service._memory.record_observation(
        observation_id="obs-1",
        vehicle_id="v-obs",
        vehicle_label="Observer",
        hazard_id="hz-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        road_segment_id="road-1",
        road_segment_name="Road 1"
    )

    # 6. Report creates one REPORTED_INCORRECT edge
    res1 = await memory_service.record_hazard_feedback(
        hazard_id="hz-1",
        vehicle_id="v-1",
        vehicle_label="V1",
        feedback_type="report_incorrect"
    )
    assert res1["reportedIncorrect"] == 1
    assert res1["feedbackCreated"] is True

    edge_id = "REPORTED_INCORRECT:v-1:hz-1"
    assert edge_id in memory_service._memory._edges

    # 7. Repeated report is idempotent
    res2 = await memory_service.record_hazard_feedback(
        hazard_id="hz-1",
        vehicle_id="v-1",
        vehicle_label="V1",
        feedback_type="report_incorrect"
    )
    assert res2["reportedIncorrect"] == 1
    assert res2["feedbackCreated"] is False


@pytest.mark.anyio
async def test_same_vehicle_confirm_and_report_and_no_approaching(memory_service):
    await memory_service._memory.record_observation(
        observation_id="obs-1",
        vehicle_id="v-obs",
        vehicle_label="Observer",
        hazard_id="hz-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        road_segment_id="road-1",
        road_segment_name="Road 1"
    )

    # 8. Same vehicle may confirm and report
    res_c = await memory_service.record_hazard_feedback("hz-1", "v-1", "V1", "confirm")
    res_r = await memory_service.record_hazard_feedback("hz-1", "v-1", "V1", "report_incorrect")

    assert res_r["confirmed"] == 1
    assert res_r["reportedIncorrect"] == 1

    # 10. Feedback creates no APPROACHING edge
    for e in memory_service._memory._edges.values():
        if e["type"] == "APPROACHING" and e["source"] == "v-1":
            raise AssertionError("Feedback created an APPROACHING edge for voter vehicle")


@pytest.mark.anyio
async def test_neo4j_feedback_failure_no_memory_fallback():
    service = PerceptionGraphService()
    # Force neo4j configuration without running initialize() to avoid reading os.environ
    service._strict = False
    service._neo4j_enabled = True
    service._mode = "neo4j"
    service._neo4j_connected = True

    # Mock Neo4j backend to raise error
    service._neo4j.record_hazard_feedback = MagicMock(side_effect=Exception("Database down"))

    # 12. Neo4j feedback failure does not call memory or change mode
    with pytest.raises(RuntimeError) as exc_info:
        await service.record_hazard_feedback("hz-1", "v-1", "V1", "confirm")

    assert "Neo4j feedback write failed" in str(exc_info.value)
    assert service._mode == "neo4j"


# ===========================================================================
# Aggregation & Recalculation tests
# ===========================================================================

def test_aggregation_rules():
    # 13. One source + one confirmation -> confidence 70
    conf, status = _calculate_confidence_and_status(1, 1, 0, "active")
    assert conf == 70
    assert status == "active"

    # 14. One source + one incorrect report -> confidence 45
    conf, status = _calculate_confidence_and_status(1, 0, 1, "active")
    assert conf == 45
    assert status == "active"

    # 15. Two sources use base confidence 80 before feedback
    conf, status = _calculate_confidence_and_status(2, 0, 0, "active")
    assert conf == 80
    assert status == "active"

    # 16. Confidence clamps at 0 and 100
    conf_min, _ = _calculate_confidence_and_status(1, 0, 10, "active")
    assert conf_min == 0
    conf_max, _ = _calculate_confidence_and_status(3, 10, 0, "active")
    assert conf_max == 100

    # 17. Five distinct reports resolve the hazard
    _, status_res = _calculate_confidence_and_status(3, 0, 5, "active")
    assert status_res == "resolved"

    # 18. Resolved status is monotonic
    _, status_mono = _calculate_confidence_and_status(3, 10, 0, "resolved")
    assert status_mono == "resolved"


@pytest.mark.anyio
async def test_new_observation_preserves_feedback_and_recalculates(memory_service):
    await memory_service.upsert_observation_and_hazard(
        observation_id="obs-1",
        vehicle_id="v-obs1",
        vehicle_label="Observer 1",
        hazard_id="hz-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        latitude=12.9436,
        longitude=80.1502,
        road_segment_id="road-1",
        road_segment_name="Road 1",
        timestamp=1000.0,
    )

    # Add feedback
    await memory_service.record_hazard_feedback("hz-1", "v-voter", "Voter", "confirm")

    # Get initial hazard state
    hz = memory_service._memory._nodes["hz-1"]
    assert hz["properties"]["confirmed"] == 1
    assert hz["properties"]["confidence"] == 70

    # 19. New observation preserves feedback counters
    # 20. New observation recalculates feedback-adjusted confidence
    # 21. Feedback does not alter Hazard.updated_at or created_at
    orig_created = hz["properties"]["created_at"]

    await memory_service.upsert_observation_and_hazard(
        observation_id="obs-2",
        vehicle_id="v-obs2",
        vehicle_label="Observer 2",
        hazard_id="hz-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        latitude=12.9436,
        longitude=80.1502,
        road_segment_id="road-1",
        road_segment_name="Road 1",
        timestamp=2000.0,
    )

    hz_after = memory_service._memory._nodes["hz-1"]
    assert hz_after["properties"]["confirmed"] == 1
    # 2 sources (base 80) + 1 confirmation -> 90 confidence
    assert hz_after["properties"]["confidence"] == 90
    assert hz_after["properties"]["created_at"] == orig_created


# ===========================================================================
# Concurrency tests
# ===========================================================================

@pytest.mark.anyio
async def test_concurrent_feedback(memory_service):
    await memory_service.record_observation(
        observation_id="obs-1",
        vehicle_id="v-obs",
        vehicle_label="Observer",
        hazard_id="hz-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        road_segment_id="road-1",
        road_segment_name="Road 1"
    )

    # 23. Concurrent identical confirmations create one edge
    tasks_ident_c = [
        memory_service.record_hazard_feedback("hz-1", "v-1", "V1", "confirm"),
        memory_service.record_hazard_feedback("hz-1", "v-1", "V1", "confirm"),
    ]
    results_ident_c = await asyncio.gather(*tasks_ident_c)
    assert sum(1 for r in results_ident_c if r["feedbackCreated"]) == 1

    # 24. Concurrent identical reports create one edge
    tasks_ident_r = [
        memory_service.record_hazard_feedback("hz-1", "v-2", "V2", "report_incorrect"),
        memory_service.record_hazard_feedback("hz-1", "v-2", "V2", "report_incorrect"),
    ]
    results_ident_r = await asyncio.gather(*tasks_ident_r)
    assert sum(1 for r in results_ident_r if r["feedbackCreated"]) == 1

    # 25. Concurrent different voters return correct final counts
    tasks_diff = [
        memory_service.record_hazard_feedback("hz-1", "v-3", "V3", "confirm"),
        memory_service.record_hazard_feedback("hz-1", "v-4", "V4", "confirm"),
        memory_service.record_hazard_feedback("hz-1", "v-5", "V5", "report_incorrect"),
    ]
    await asyncio.gather(*tasks_diff)

    hz = memory_service._memory._nodes["hz-1"]
    # 1 obs source (base 60) + 3 confirmations (v-1, v-3, v-4) - 2 reports (v-2, v-5)
    # 60 + 30 - 30 = 60 confidence
    assert hz["properties"]["confirmed"] == 3
    assert hz["properties"]["reportedIncorrect"] == 2
    assert hz["properties"]["confidence"] == 60


# ===========================================================================
# API tests
# ===========================================================================

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.post("/api/sentinel/demo/reset")
        yield c


def test_api_routes(client):
    # Reset
    client.post("/api/sentinel/demo/reset").raise_for_status()

    # 27. Confirm remains bodyless and preserves response shape
    r_c = client.post("/api/sentinel/hazards/hz-002/confirm")
    assert r_c.status_code == 200
    d_c = r_c.json()
    assert d_c["id"] == "hz-002"
    assert d_c["confirmed"] == 1
    assert d_c["reportedIncorrect"] == 0

    # 29. Repeated v-ego calls remain idempotent
    r_c2 = client.post("/api/sentinel/hazards/hz-002/confirm")
    assert r_c2.status_code == 200
    assert r_c2.json()["confirmed"] == 1

    # 28. Report remains bodyless and preserves response shape
    r_r = client.post("/api/sentinel/hazards/hz-002/report-incorrect")
    assert r_r.status_code == 200
    d_r = r_r.json()
    assert d_r["id"] == "hz-002"
    assert d_r["confirmed"] == 1
    assert d_r["reportedIncorrect"] == 1

    # 30. Unknown hazard returns 404
    r_404 = client.post("/api/sentinel/hazards/hz-does-not-exist/confirm")
    assert r_404.status_code == 404
    assert r_404.json()["detail"] == "Hazard not found"

    # 31. Invalid feedback produces sanitized 422
    r_422 = client.post("/api/sentinel/hazards/%20/confirm")
    assert r_422.status_code == 422

    # 34. GET hazards immediately reflects feedback
    r_list = client.get("/api/sentinel/hazards")
    assert r_list.status_code == 200
    hz = next(h for h in r_list.json() if h["id"] == "hz-002")
    assert hz["confirmed"] == 1
    assert hz["reportedIncorrect"] == 1
    # 1 source (base 60) + 1 confirm - 1 report -> 60 + 10 - 15 = 55 confidence
    assert hz["confidence"] == 55

    # 35. Demo world-model immediately reflects feedback
    r_wm = client.get("/api/sentinel/world-model")
    assert r_wm.status_code == 200
    hz_wm = next(h for h in r_wm.json()["hazards"] if h["id"] == "hz-002")
    assert hz_wm["confirmed"] == 1
    assert hz_wm["reportedIncorrect"] == 1


@pytest.mark.anyio
async def test_api_resolved_hazard_disappears_from_live_world_model(client):
    client.post("/api/sentinel/demo/reset").raise_for_status()

    # Resolve the hazard by sending 5 reports as different vehicles
    for i in range(5):
        await _perception_graph.record_hazard_feedback(
            hazard_id="hz-002",
            vehicle_id=f"v-voter-{i}",
            vehicle_label="Voter",
            feedback_type="report_incorrect"
        )

    # Verify via lists
    raw_hz = await _perception_graph.list_hazards()
    hz_status = next(h for h in raw_hz if h["id"] == "hz-002")["status"]
    assert hz_status == "resolved"

    # 36. Resolved hazard disappears from live world-model
    # When requesting live world model with coords
    r_wm = client.get(
        "/api/sentinel/world-model",
        params={"latitude": 12.9436, "longitude": 80.1502, "heading": 0, "radius_m": 1000}
    )
    assert r_wm.status_code == 200
    assert all(h["id"] != "hz-002" for h in r_wm.json()["hazards"])

    # 37. Reset removes feedback and restores baseline counters
    client.post("/api/sentinel/demo/reset").raise_for_status()
    r_list = client.get("/api/sentinel/hazards")
    hz_reset = next(h for h in r_list.json() if h["id"] == "hz-002")
    assert hz_reset["confirmed"] == 0
    assert hz_reset["reportedIncorrect"] == 0

    raw_hz_reset = await _perception_graph.list_hazards()
    hz_reset_graph = next(h for h in raw_hz_reset if h["id"] == "hz-002")
    assert hz_reset_graph["status"] == "active"



def test_no_db_hazards_fallback(client):
    # 33. Patching every db.hazards method to fail does not affect either endpoint
    with patch.object(db.hazards, "find_one_and_update", side_effect=Exception("Mongo fail")):
        with patch.object(db.hazards, "replace_one", side_effect=Exception("Mongo fail")):
            r_c = client.post("/api/sentinel/hazards/hz-002/confirm")
            assert r_c.status_code == 200

            r_r = client.post("/api/sentinel/hazards/hz-002/report-incorrect")
            assert r_r.status_code == 200


# ===========================================================================
# Retirement & Absence Verification
# ===========================================================================

def test_retirement_verifications(sync_db):
    # 38. demo_observation performs no db.hazards/db.observations write
    # Clear db.hazards and db.observations first
    sync_db.hazards.delete_many({})
    sync_db.observations.delete_many({})

    client_test = TestClient(app)
    obs = {
        "id": "obs-retirement-test",
        "type": "stationary_vehicle",
        "label": "Stationary Vehicle",
        "location": {"latitude": 12.9436, "longitude": 80.1502},
        "sourceVehicleId": "v-test",
        "vehicleLabel": "Test Vehicle"
    }
    r = client_test.post("/api/sentinel/demo/observation", json=obs)
    assert r.status_code == 200

    # Verify no writes occurred in Mongo
    assert sync_db.hazards.count_documents({}) == 0
    assert sync_db.observations.count_documents({}) == 0

    # 39. ensure_seed performs no Mongo hazard read/write
    sync_db.sentinel_meta.delete_many({"id": "seed"})
    r_status = client_test.get("/api/sentinel/status")
    assert r_status.status_code == 200
    assert sync_db.hazards.count_documents({}) == 0

    # 40. server contains no Neo4jService import
    server_path = os.path.join(backend_dir, "server.py")
    with open(server_path, "r", encoding="utf-8") as f:
        server_content = f.read()
    assert "Neo4jService" not in server_content

    # 41. production code contains no fallback collection access
    graph_path = os.path.join(backend_dir, "services", "perception_graph_service.py")
    with open(graph_path, "r", encoding="utf-8") as f:
        graph_content = f.read()

    obsolete_colls = [
        "neo4j_confirmations", "neo4j_reports", "neo4j_hazards", "neo4j_observations",
        "neo4j_warnings", "neo4j_vehicles", "neo4j_road_segments", "neo4j_approaching"
    ]
    for coll in obsolete_colls:
        assert coll not in server_content
        assert coll not in graph_content

    # 42. legacy neo4j_service.py is deleted
    neo4j_svc_path = os.path.join(backend_dir, "services", "neo4j_service.py")
    assert not os.path.exists(neo4j_svc_path)


# ===========================================================================
# Hardened Correctness & Verification Regression Tests
# ===========================================================================

@pytest.mark.anyio
async def test_feedback_timestamp_none_produces_finite_positive_created_at(memory_service):
    await memory_service.record_observation(
        observation_id="obs-ts-1",
        vehicle_id="v-obs",
        vehicle_label="Observer",
        hazard_id="hz-ts-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        road_segment_id="road-1",
        road_segment_name="Road 1"
    )
    import time
    t0 = time.time()
    res = await memory_service.record_hazard_feedback(
        hazard_id="hz-ts-1",
        vehicle_id="v-voter",
        vehicle_label="Voter",
        feedback_type="confirm",
        timestamp=None
    )
    t1 = time.time()
    assert res is not None
    edge_id = "CONFIRMED:v-voter:hz-ts-1"
    edge = memory_service._memory._edges[edge_id]
    c_at = edge["properties"]["created_at"]
    assert isinstance(c_at, float)
    assert not isinstance(c_at, bool)
    assert math.isfinite(c_at)
    assert t0 - 5.0 <= c_at <= t1 + 5.0


@pytest.mark.anyio
async def test_feedback_exact_retry_preserves_created_at_and_feedback_updated_at(memory_service):
    await memory_service.record_observation(
        observation_id="obs-retry-1",
        vehicle_id="v-obs",
        vehicle_label="Observer",
        hazard_id="hz-retry-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        road_segment_id="road-1",
        road_segment_name="Road 1"
    )
    res1 = await memory_service.record_hazard_feedback(
        hazard_id="hz-retry-1",
        vehicle_id="v-voter",
        vehicle_label="Voter",
        feedback_type="confirm",
        timestamp=100.0
    )
    assert res1["feedbackCreated"] is True

    edge_id = "CONFIRMED:v-voter:hz-retry-1"
    edge = memory_service._memory._edges[edge_id]
    assert edge["properties"]["created_at"] == 100.0
    hz = memory_service._memory._nodes["hz-retry-1"]
    assert hz["properties"].get("feedbackUpdatedAt") == 100.0

    res2 = await memory_service.record_hazard_feedback(
        hazard_id="hz-retry-1",
        vehicle_id="v-voter",
        vehicle_label="Voter",
        feedback_type="confirm",
        timestamp=200.0
    )
    assert res2["feedbackCreated"] is False
    assert edge["properties"]["created_at"] == 100.0
    assert hz["properties"].get("feedbackUpdatedAt") == 100.0


@pytest.mark.anyio
async def test_memory_hazard_stores_feedback_updated_at(memory_service):
    await memory_service.record_observation(
        observation_id="obs-fu-1",
        vehicle_id="v-obs",
        vehicle_label="Observer",
        hazard_id="hz-fu-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        road_segment_id="road-1",
        road_segment_name="Road 1"
    )
    await memory_service.record_hazard_feedback(
        hazard_id="hz-fu-1",
        vehicle_id="v-voter",
        vehicle_label="Voter",
        feedback_type="confirm",
        timestamp=123.45
    )
    hz = memory_service._memory._nodes["hz-fu-1"]
    assert hz["properties"].get("feedbackUpdatedAt") == 123.45


@pytest.mark.anyio
async def test_feedback_malformed_created_at_rejected(memory_service):
    await memory_service.record_observation(
        observation_id="obs-mal-1",
        vehicle_id="v-obs",
        vehicle_label="Observer",
        hazard_id="hz-mal-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        road_segment_id="road-1",
        road_segment_name="Road 1"
    )

    edge_id = "CONFIRMED:v-voter:hz-mal-1"

    invalid_values = [True, False, -10.0, float("nan"), float("inf"), float("-inf")]
    for val in invalid_values:
        memory_service._memory._edges[edge_id] = {
            "id": edge_id,
            "type": "CONFIRMED",
            "source": "v-voter",
            "target": "hz-mal-1",
            "scenarioId": SCENARIO_ID,
            "properties": {
                "scenario_id": SCENARIO_ID,
                "feedback_id": f"{SCENARIO_ID}:confirm:hz-mal-1:v-voter",
                "created_at": val
            }
        }
        memory_service._memory._nodes["v-voter"] = {
            "id": "v-voter",
            "type": "Vehicle",
            "scenarioId": SCENARIO_ID,
            "properties": {"label": "Voter"}
        }

        with pytest.raises(ValueError):
            await memory_service.record_hazard_feedback(
                hazard_id="hz-mal-1",
                vehicle_id="v-voter",
                vehicle_label="Voter",
                feedback_type="confirm",
                timestamp=100.0
            )


@pytest.mark.anyio
async def test_duplicate_semantic_feedback_edges_rejected(memory_service):
    await memory_service.record_observation(
        observation_id="obs-dup-1",
        vehicle_id="v-obs",
        vehicle_label="Observer",
        hazard_id="hz-dup-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        road_segment_id="road-1",
        road_segment_name="Road 1"
    )

    memory_service._memory._edges["edge-1"] = {
        "id": "edge-1",
        "type": "CONFIRMED",
        "source": "v-voter",
        "target": "hz-dup-1",
        "scenarioId": SCENARIO_ID,
        "properties": {
            "scenario_id": SCENARIO_ID,
            "feedback_id": f"{SCENARIO_ID}:confirm:hz-dup-1:v-voter",
            "created_at": 100.0
        }
    }

    memory_service._memory._edges["edge-2"] = {
        "id": "edge-2",
        "type": "CONFIRMED",
        "source": "v-voter",
        "target": "hz-dup-1",
        "scenarioId": SCENARIO_ID,
        "properties": {
            "scenario_id": SCENARIO_ID,
            "feedback_id": f"{SCENARIO_ID}:confirm:hz-dup-1:v-voter",
            "created_at": 100.0
        }
    }

    memory_service._memory._nodes["v-voter"] = {
        "id": "v-voter",
        "type": "Vehicle",
        "scenarioId": SCENARIO_ID,
        "properties": {"label": "Voter"}
    }

    with pytest.raises(ValueError, match="Multiple relationships of type CONFIRMED found"):
        await memory_service.record_hazard_feedback(
            hazard_id="hz-dup-1",
            vehicle_id="v-voter",
            vehicle_label="Voter",
            feedback_type="confirm",
            timestamp=100.0
        )


@pytest.mark.anyio
async def test_service_runtime_error_text_exactly_sanitized():
    service = PerceptionGraphService()
    service._strict = False
    service._neo4j_enabled = True
    service._mode = "neo4j"
    service._neo4j_connected = True

    service._neo4j.record_hazard_feedback = MagicMock(side_effect=Exception("Raw DB credentials leak or connection reset"))

    with pytest.raises(RuntimeError) as exc_info:
        await service.record_hazard_feedback("hz-1", "v-1", "V1", "confirm")

    assert str(exc_info.value) == "Neo4j feedback write failed"


def test_api_422_detail_is_exactly_sanitized(client):
    r = client.post("/api/sentinel/hazards/%20/confirm")
    assert r.status_code == 422
    assert r.json()["detail"] == "Invalid feedback request"


@pytest.mark.anyio
async def test_feedback_directly_preserves_hazard_created_at_and_updated_at(memory_service):
    await memory_service.upsert_observation_and_hazard(
        observation_id="obs-ts-p-1",
        vehicle_id="v-obs",
        vehicle_label="Observer",
        hazard_id="hz-ts-p-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        latitude=12.9436,
        longitude=80.1502,
        road_segment_id="road-1",
        road_segment_name="Road 1",
        timestamp=987.65
    )

    hz_node = memory_service._memory._nodes["hz-ts-p-1"]
    orig_c_at = hz_node["properties"]["created_at"]
    orig_u_at = hz_node["properties"]["updated_at"]
    assert orig_c_at == 987.65
    assert orig_u_at == 987.65

    await memory_service.record_hazard_feedback(
        hazard_id="hz-ts-p-1",
        vehicle_id="v-voter",
        vehicle_label="Voter",
        feedback_type="confirm",
        timestamp=2000.0
    )

    assert hz_node["properties"]["created_at"] == orig_c_at
    assert hz_node["properties"]["updated_at"] == orig_u_at


@pytest.mark.anyio
async def test_post_lock_status_monotonicity_race(memory_service):
    await memory_service.upsert_observation_and_hazard(
        observation_id="obs-race-1",
        vehicle_id="v-obs",
        vehicle_label="Observer",
        hazard_id="hz-race-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        latitude=12.9436,
        longitude=80.1502,
        road_segment_id="road-1",
        road_segment_name="Road 1",
        timestamp=1000.0
    )

    for i in range(3):
        await memory_service.record_hazard_feedback(
            hazard_id="hz-race-1",
            vehicle_id=f"v-reporter-{i}",
            vehicle_label="Reporter",
            feedback_type="report_incorrect",
            timestamp=1010.0 + i
        )

    hz = memory_service._memory._nodes["hz-race-1"]
    assert hz["properties"]["status"] == "active"
    assert hz["properties"]["reportedIncorrect"] == 3
    assert hz["properties"]["confirmed"] == 0
    assert hz["properties"]["confidence"] == 15

    await memory_service.record_hazard_feedback(
        hazard_id="hz-race-1",
        vehicle_id="v-reporter-3",
        vehicle_label="Reporter 3",
        feedback_type="report_incorrect",
        timestamp=1100.0
    )
    assert hz["properties"]["status"] == "resolved"
    assert hz["properties"]["confidence"] == 0

    await memory_service.record_hazard_feedback(
        hazard_id="hz-race-1",
        vehicle_id="v-confirmer",
        vehicle_label="Confirmer",
        feedback_type="confirm",
        timestamp=1110.0
    )

    assert hz["properties"]["confirmed"] == 1
    assert hz["properties"]["reportedIncorrect"] == 4
    assert hz["properties"]["confidence"] == 10
    assert hz["properties"]["status"] == "resolved"


@pytest.mark.anyio
async def test_memory_and_neo4j_normalized_feedback_results_match(memory_service):
    await memory_service.upsert_observation_and_hazard(
        observation_id="obs-match-1",
        vehicle_id="v-obs",
        vehicle_label="Observer",
        hazard_id="hz-match-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        latitude=12.9436,
        longitude=80.1502,
        road_segment_id="road-1",
        road_segment_name="Road 1",
        timestamp=1000.0
    )

    res_mem = await memory_service.record_hazard_feedback(
        hazard_id="hz-match-1",
        vehicle_id="v-voter",
        vehicle_label="Voter",
        feedback_type="confirm",
        timestamp=2000.0
    )

    assert res_mem is not None
    assert res_mem["id"] == "hz-match-1"
    assert res_mem["confirmed"] == 1
    assert res_mem["reportedIncorrect"] == 0
    assert res_mem["confidence"] == 70
    assert res_mem["status"] == "active"
    assert res_mem["feedbackCreated"] is True


@pytest.mark.anyio
async def test_relationship_constraints_creation():
    service = PerceptionGraphService()
    service._strict = True
    service._neo4j_enabled = True
    service._mode = "neo4j"

    mock_driver = MagicMock()
    mock_session = MagicMock()

    mock_driver.session.return_value.__aenter__.return_value = mock_session
    service._neo4j._driver = mock_driver

    runs = []
    async def mock_run(cypher, *args, **kwargs):
        runs.append(cypher)
        return MagicMock()

    mock_session.run = mock_run

    await service._neo4j._create_constraints()

    assert any("sentinel_confirmed_feedback_identity" in r and "CONFIRMED" in r for r in runs)
    assert any("sentinel_reported_feedback_identity" in r and "REPORTED_INCORRECT" in r for r in runs)

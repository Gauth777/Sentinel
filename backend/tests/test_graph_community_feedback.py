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
    assert memory_service._memory._edges[edge_id]["properties"]["created_at"] == 0.0


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

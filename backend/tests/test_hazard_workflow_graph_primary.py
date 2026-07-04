import asyncio
import hashlib
import os
import sys
import pytest
from unittest.mock import MagicMock, AsyncMock

# Ensure backend is on sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from workflows.hazard_workflow import LocalWorkflowRunner
from services.perception_graph_service import PerceptionGraphService

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture
async def graph_service():
    gs = PerceptionGraphService()
    await gs.initialize()
    yield gs
    await gs.close()

# 1. New observation creates a graph-backed hazard.
@pytest.mark.anyio
async def test_1_new_observation_creates_hazard(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs = {
        "id": "obs-1",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-1",
        "vehicleLabel": "Vehicle 1"
    }
    res = await runner.process_observation(obs)
    assert res is not None
    assert "id" in res
    assert res["type"] == "pothole"
    assert res["status"] == "active"
    
    # Check that it exists in the graph service
    graph_hz = await graph_service.get_observation_hazard("obs-1")
    assert graph_hz is not None
    assert graph_hz["id"] == res["id"]

# 2. Complete Vehicle → Observation → Hazard → RoadSegment graph exists.
@pytest.mark.anyio
async def test_2_complete_graph_relationships(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs = {
        "id": "obs-2",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-2",
        "vehicleLabel": "Vehicle 2"
    }
    res = await runner.process_observation(obs)
    
    graph = await graph_service.build_graph(hazard_id=res["id"])
    node_types = [n["type"] for n in graph["nodes"]]
    assert "Vehicle" in node_types
    assert "Observation" in node_types
    assert "Hazard" in node_types
    assert "RoadSegment" in node_types
    
    edge_types = [e["type"] for e in graph["edges"]]
    assert "OBSERVED" in edge_types
    assert "SUPPORTS" in edge_types
    assert "ON_ROAD" in edge_types
    assert "APPROACHING" in edge_types

# 3. Returned hazard ID is deterministic from observation ID.
@pytest.mark.anyio
async def test_3_deterministic_hazard_id(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs = {
        "id": "obs-3",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-3",
        "vehicleLabel": "Vehicle 3"
    }
    res = await runner.process_observation(obs)
    
    hash_val = hashlib.sha256(b"obs-3").hexdigest()
    expected_id = f"hz-{hash_val[:12]}"
    assert res["id"] == expected_id

# 4. Same observation returns the same hazard without another upsert.
@pytest.mark.anyio
async def test_4_duplicate_observation_no_upsert(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs = {
        "id": "obs-4",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-4",
        "vehicleLabel": "Vehicle 4"
    }
    res1 = await runner.process_observation(obs)
    
    # Now patch graph_service.upsert_observation_and_hazard to raise an assertion error
    original_upsert = graph_service.upsert_observation_and_hazard
    async def mock_upsert(*args, **kwargs):
        pytest.fail("upsert_observation_and_hazard should not be called for duplicate observation")
    
    graph_service.upsert_observation_and_hazard = mock_upsert
    try:
        res2 = await runner.process_observation(obs)
        assert res1["id"] == res2["id"]
    finally:
        graph_service.upsert_observation_and_hazard = original_upsert

# 5. Duplicate observation does not change updated_at.
@pytest.mark.anyio
async def test_5_duplicate_observation_no_updated_at_change(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs = {
        "id": "obs-5",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-5",
        "vehicleLabel": "Vehicle 5"
    }
    res1 = await runner.process_observation(obs)
    
    await asyncio.sleep(0.01)
    
    res2 = await runner.process_observation(obs)
    assert res1["updated_at"] == res2["updated_at"]

# 6. Duplicate observation does not increase source count.
@pytest.mark.anyio
async def test_6_duplicate_observation_no_source_count_increase(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs = {
        "id": "obs-6",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-6",
        "vehicleLabel": "Vehicle 6"
    }
    res1 = await runner.process_observation(obs)
    assert res1["sources"] == 1
    
    res2 = await runner.process_observation(obs)
    assert res2["sources"] == 1

# 7. Duplicate observation returns warnings and deterministic warning event ID.
@pytest.mark.anyio
async def test_7_duplicate_observation_returns_warning_event(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs = {
        "id": "obs-7",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-7",
        "vehicleLabel": "Vehicle 7"
    }
    res1 = await runner.process_observation(obs)
    expected_warn_id = f"warn-{res1['id']}-obs-7-v-7-en"
    assert res1["_warning_events"] == [expected_warn_id]

    res2 = await runner.process_observation(obs)
    assert res2["_warning_events"] == [expected_warn_id]

# 8. Second distinct vehicle within matching radius reuses the hazard.
@pytest.mark.anyio
async def test_8_second_vehicle_reuses_hazard(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs1 = {
        "id": "obs-8-1",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-8-1",
        "vehicleLabel": "Vehicle 8-1"
    }
    obs2 = {
        "id": "obs-8-2",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9451, "longitude": 80.1504},
        "sourceVehicleId": "v-8-2",
        "vehicleLabel": "Vehicle 8-2"
    }
    res1 = await runner.process_observation(obs1)
    res2 = await runner.process_observation(obs2)
    assert res1["id"] == res2["id"]

# 9. Two distinct source vehicles produce: sources=2, confidence=80, sorted unique source_vehicles.
@pytest.mark.anyio
async def test_9_two_source_vehicles_stats(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs1 = {
        "id": "obs-9-1",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-9-b",
        "vehicleLabel": "Vehicle 9-B"
    }
    obs2 = {
        "id": "obs-9-2",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-9-a",
        "vehicleLabel": "Vehicle 9-A"
    }
    await runner.process_observation(obs1)
    res2 = await runner.process_observation(obs2)
    assert res2["sources"] == 2
    assert res2["confidence"] == 80
    assert res2["source_vehicles"] == ["v-9-a", "v-9-b"]

# 10. Third distinct source produces confidence=100.
@pytest.mark.anyio
async def test_10_three_source_vehicles_stats(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs1 = {
        "id": "obs-10-1",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-10-1",
        "vehicleLabel": "Vehicle 1"
    }
    obs2 = {
        "id": "obs-10-2",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-10-2",
        "vehicleLabel": "Vehicle 2"
    }
    obs3 = {
        "id": "obs-10-3",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-10-3",
        "vehicleLabel": "Vehicle 3"
    }
    await runner.process_observation(obs1)
    await runner.process_observation(obs2)
    res3 = await runner.process_observation(obs3)
    assert res3["sources"] == 3
    assert res3["confidence"] == 100

# 11. Far-away observation creates a separate hazard.
@pytest.mark.anyio
async def test_11_far_away_creates_separate(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs1 = {
        "id": "obs-11-1",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-11-1",
        "vehicleLabel": "Vehicle 1"
    }
    obs2 = {
        "id": "obs-11-2",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9470, "longitude": 80.1510},
        "sourceVehicleId": "v-11-2",
        "vehicleLabel": "Vehicle 2"
    }
    res1 = await runner.process_observation(obs1)
    res2 = await runner.process_observation(obs2)
    assert res1["id"] != res2["id"]

# 12. Different hazard type creates a separate hazard.
@pytest.mark.anyio
async def test_12_different_type_creates_separate(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs1 = {
        "id": "obs-12-1",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-12-1",
        "vehicleLabel": "Vehicle 1"
    }
    obs2 = {
        "id": "obs-12-2",
        "type": "stationary_vehicle",
        "label": "Stationary Vehicle",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-12-2",
        "vehicleLabel": "Vehicle 2"
    }
    res1 = await runner.process_observation(obs1)
    res2 = await runner.process_observation(obs2)
    assert res1["id"] != res2["id"]

# 13. Observation on a different resolved road creates a separate hazard.
@pytest.mark.anyio
async def test_13_different_road_creates_separate(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs1 = {
        "id": "obs-13-1",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9436, "longitude": 80.1502}, # GST
        "sourceVehicleId": "v-13-1",
        "vehicleLabel": "Vehicle 1"
    }
    obs2 = {
        "id": "obs-13-2",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9429, "longitude": 80.1574}, # Service Rd
        "sourceVehicleId": "v-13-2",
        "vehicleLabel": "Vehicle 2"
    }
    res1 = await runner.process_observation(obs1)
    res2 = await runner.process_observation(obs2)
    assert res1["id"] != res2["id"]

# 14. Replay provenance is persisted.
@pytest.mark.anyio
async def test_14_replay_provenance_persisted(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs = {
        "id": "obs-14",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-14",
        "vehicleLabel": "Vehicle 14",
        "_replay_meta": {
            "recommendedAction": "Move left",
            "risk": "medium",
            "model": "TestModel",
            "inferenceMode": "cached_qwen",
            "sampleId": "s14",
            "lastInferenceId": "inf14",
            "confidence": 0.95
        }
    }
    res = await runner.process_observation(obs)
    
    graph_hz = await graph_service.get_observation_hazard("obs-14")
    assert graph_hz["model"] == "TestModel"
    assert graph_hz["inferenceMode"] == "cached_qwen"
    assert graph_hz["sampleId"] == "s14"
    assert graph_hz["lastInferenceId"] == "inf14"
    assert graph_hz["replayConfidence"] == 0.95

# 15. Replay action is applied.
@pytest.mark.anyio
async def test_15_replay_action_applied(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs = {
        "id": "obs-15",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-15",
        "vehicleLabel": "Vehicle 15",
        "_replay_meta": {
            "recommendedAction": "Yield to traffic",
            "risk": "medium",
            "model": "TestModel",
            "inferenceMode": "cached_qwen",
            "sampleId": "s15",
            "lastInferenceId": "inf15",
            "confidence": 0.95
        }
    }
    res = await runner.process_observation(obs)
    assert res["recommendedAction"] == "Yield to traffic"

# 16. Replay risk may increase.
@pytest.mark.anyio
async def test_16_replay_risk_may_increase(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs1 = {
        "id": "obs-16-1",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-16-1",
        "vehicleLabel": "Vehicle 1"
    }
    res1 = await runner.process_observation(obs1)
    assert res1["risk"] == "medium"
    
    obs2 = {
        "id": "obs-16-2",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-16-2",
        "vehicleLabel": "Vehicle 2",
        "_replay_meta": {
            "recommendedAction": "Move left",
            "risk": "high",
            "model": "TestModel",
            "inferenceMode": "cached_qwen",
            "sampleId": "s16",
            "lastInferenceId": "inf16",
            "confidence": 0.95
        }
    }
    res2 = await runner.process_observation(obs2)
    assert res2["id"] == res1["id"]
    assert res2["risk"] == "high"

# 17. Replay risk cannot decrease an existing hazard.
@pytest.mark.anyio
async def test_17_replay_risk_cannot_decrease(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs1 = {
        "id": "obs-17-1",
        "type": "stationary_vehicle",
        "label": "Stationary Vehicle",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-17-1",
        "vehicleLabel": "Vehicle 1"
    }
    res1 = await runner.process_observation(obs1)
    assert res1["risk"] == "high"
    
    obs2 = {
        "id": "obs-17-2",
        "type": "stationary_vehicle",
        "label": "Stationary Vehicle",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-17-2",
        "vehicleLabel": "Vehicle 2",
        "_replay_meta": {
            "recommendedAction": "Reduce speed",
            "risk": "low",
            "model": "TestModel",
            "inferenceMode": "cached_qwen",
            "sampleId": "s17",
            "lastInferenceId": "inf17",
            "confidence": 0.95
        }
    }
    res2 = await runner.process_observation(obs2)
    assert res2["id"] == res1["id"]
    assert res2["risk"] == "high"

# 18. Warning text is still returned.
@pytest.mark.anyio
async def test_18_warning_text_returned(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs = {
        "id": "obs-18",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-18",
        "vehicleLabel": "Vehicle 18"
    }
    res = await runner.process_observation(obs)
    assert "warnings" in res
    assert "en" in res["warnings"]
    assert "hi" in res["warnings"]

# 19. Warning node is created with correct relationships in Stage B2A.
@pytest.mark.anyio
async def test_19_warning_node_created_with_relationships(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs = {
        "id": "obs-19",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-19",
        "vehicleLabel": "Vehicle 19"
    }
    res = await runner.process_observation(obs)

    # Assert response elements
    assert "warnings" in res
    assert "en" in res["warnings"]
    assert len(res.get("_warning_events", [])) == 1
    warn_id = res["_warning_events"][0]

    graph = await graph_service.build_graph(hazard_id=res["id"])
    node_types = [n["type"] for n in graph["nodes"]]
    assert "Warning" in node_types

    # Find warning node properties
    warn_node = next(n for n in graph["nodes"] if n["type"] == "Warning")
    assert warn_node["id"] == warn_id
    assert warn_node["properties"]["language"] == "en"
    assert warn_node["properties"]["text"] == res["warnings"]["en"]

    # Assert relationship existence
    edge_types = [e["type"] for e in graph["edges"]]
    assert "TRIGGERED_WARNING" in edge_types
    assert "DELIVERED_TO" in edge_types

# 20. Invalid observation data fails before mutation.
@pytest.mark.anyio
async def test_20_invalid_observation_validation(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    
    obs_invalid_lat = {
        "id": "obs-20-1",
        "type": "pothole",
        "location": {"latitude": 150.0, "longitude": 80.1503},
        "sourceVehicleId": "v-20"
    }
    with pytest.raises(ValueError, match="latitude"):
        await runner.process_observation(obs_invalid_lat)
        
    graph = await graph_service.build_graph()
    assert graph["summary"]["nodeCount"] == 0

# 21. Graph operational failure is not swallowed.
@pytest.mark.anyio
async def test_21_graph_failure_propagates(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs = {
        "id": "obs-21",
        "type": "pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-21"
    }
    
    async def mock_upsert(*args, **kwargs):
        raise RuntimeError("Graph database disconnected")
        
    original_upsert = graph_service.upsert_observation_and_hazard
    graph_service.upsert_observation_and_hazard = mock_upsert
    try:
        with pytest.raises(RuntimeError, match="Graph database disconnected"):
            await runner.process_observation(obs)
    finally:
        graph_service.upsert_observation_and_hazard = original_upsert

# 22. Concurrent calls for the exact same observation produce one hazard and one source.
@pytest.mark.anyio
async def test_22_concurrent_calls_idempotent(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs = {
        "id": "obs-22",
        "type": "pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-22"
    }
    
    results = await asyncio.gather(
        runner.process_observation(obs),
        runner.process_observation(obs),
        runner.process_observation(obs),
        return_exceptions=True
    )
    
    for res in results:
        assert not isinstance(res, Exception)
        assert res["sources"] == 1
        
    graph = await graph_service.build_graph()
    assert graph["summary"]["hazardCount"] == 1
    assert graph["summary"]["observationCount"] == 1

# 23. Workflow source contains no prohibited statements.
def test_23_workflow_source_clean():
    workflow_path = os.path.join(backend_dir, "workflows", "hazard_workflow.py")
    with open(workflow_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    assert "db.hazards" not in content
    assert "db.observations" not in content
    assert "Neo4jService" not in content
    assert ".record_observation" not in content

# 24. Demo observation route injects the global graph service.
def test_24_demo_route_dependency_injection():
    server_path = os.path.join(backend_dir, "server.py")
    with open(server_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    assert "LocalWorkflowRunner(" in content
    assert "graph_service=" in content
    assert "ego_location=" in content

# 25. Existing replay activation contracts continue to pass.
@pytest.mark.anyio
async def test_25_replay_activation_contracts():
    from services.replay_activation_service import activate_inference
    from models.vision_inference import InferenceResult, StructuredRoadPrediction, RuntimeHazardPrediction, InferenceMode
    
    pred = StructuredRoadPrediction(
        road_type="urban_arterial",
        traffic_density="high",
        road_complexity="complex",
        hazard_presence="yes",
        anticipated_risk="high",
        recommended_action="slow_down"
    )
    runtime_hz = RuntimeHazardPrediction(
        hazard_type="crossing_vehicle",
        hazard_description="Vehicle crossing from side",
        confidence=0.82
    )
    result = InferenceResult(
        inference_id="inf-test-25",
        sample_id="sample-25",
        model="Qwen2.5-VL-7B-Instruct",
        prompt_version="v1",
        inference_mode=InferenceMode.cached_qwen,
        prediction=pred,
        runtime_hazard=runtime_hz,
        latency_ms=0
    )
    
    location = {"latitude": 12.9450, "longitude": 80.1503}
    
    activation = await activate_inference(result, location)
    assert activation.activated is True
    assert activation.warning_text_generated is True
    assert activation.warning_event_created is True


# ---------------------------------------------------------------------------
# Stage B1 review blockers regression tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_replay_metadata_validation(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    
    # None is allowed
    obs_none = {
        "id": "obs-meta-1",
        "type": "pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "_replay_meta": None
    }
    await runner.process_observation(obs_none)  # Should succeed

    # Non-dict raises ValueError
    obs_invalid_type = {
        "id": "obs-meta-2",
        "type": "pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "_replay_meta": "not-a-dict"
    }
    with pytest.raises(ValueError, match="_replay_meta must be a dictionary or None"):
        await runner.process_observation(obs_invalid_type)

    # Empty string in string fields raises ValueError
    obs_empty_str = {
        "id": "obs-meta-3",
        "type": "pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "_replay_meta": {"model": ""}
    }
    with pytest.raises(ValueError, match="Replay metadata model must be a non-empty string"):
        await runner.process_observation(obs_empty_str)

    # Invalid risk raises ValueError
    obs_invalid_risk = {
        "id": "obs-meta-4",
        "type": "pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "_replay_meta": {"risk": "extreme"}
    }
    with pytest.raises(ValueError, match="Replay metadata risk must be 'low', 'medium', or 'high'"):
        await runner.process_observation(obs_invalid_risk)

    # Boolean confidence raises ValueError
    obs_bool_conf = {
        "id": "obs-meta-5",
        "type": "pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "_replay_meta": {"confidence": True}
    }
    with pytest.raises(ValueError, match="Replay metadata confidence must be a finite number"):
        await runner.process_observation(obs_bool_conf)

    # Non-finite float confidence raises ValueError
    obs_inf_conf = {
        "id": "obs-meta-6",
        "type": "pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "_replay_meta": {"confidence": float("inf")}
    }
    with pytest.raises(ValueError, match="Replay metadata confidence must be a finite number"):
        await runner.process_observation(obs_inf_conf)

    # Duplicate observation does not bypass this validation
    obs_dup = {
        "id": "obs-meta-7",
        "type": "pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
    }
    await runner.process_observation(obs_dup)
    
    # Process duplicate but pass invalid metadata
    obs_dup_invalid = dict(obs_dup)
    obs_dup_invalid["_replay_meta"] = {"model": ""}
    with pytest.raises(ValueError, match="Replay metadata model must be a non-empty string"):
        await runner.process_observation(obs_dup_invalid)


@pytest.mark.anyio
async def test_preserve_matched_hazard_label(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    
    # Process first observation with custom label
    obs1 = {
        "id": "obs-label-1",
        "type": "pothole",
        "label": "Deep Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
    }
    res1 = await runner.process_observation(obs1)
    assert res1["label"] == "Deep Pothole"

    # Process second nearby observation with a different label
    obs2 = {
        "id": "obs-label-2",
        "type": "pothole",
        "label": "Minor Pothole",
        "location": {"latitude": 12.9451, "longitude": 80.1504},
    }
    res2 = await runner.process_observation(obs2)
    
    # The hazard ID is the same (matched)
    assert res1["id"] == res2["id"]
    
    # The label should still be "Deep Pothole", NOT overwritten by "Minor Pothole"
    assert res2["label"] == "Deep Pothole"





# ---------------------------------------------------------------------------
# Stage B2A local warning recording regression tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_b2a_basic_warning_event_creation(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs = {
        "id": "obs-b2a-1",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-1",
        "vehicleLabel": "Sentinel Vehicle"
    }
    res = await runner.process_observation(obs)
    assert len(res.get("_warning_events", [])) == 1
    warn_id = res["_warning_events"][0]

    graph = await graph_service.build_graph(hazard_id=res["id"])
    node_types = [n["type"] for n in graph["nodes"]]
    edge_types = [e["type"] for e in graph["edges"]]
    assert "Warning" in node_types
    assert "TRIGGERED_WARNING" in edge_types
    assert "DELIVERED_TO" in edge_types


@pytest.mark.anyio
async def test_b2a_idempotent_duplicate_observation(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs = {
        "id": "obs-b2a-2",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-1",
        "vehicleLabel": "Sentinel Vehicle"
    }
    res1 = await runner.process_observation(obs)
    res2 = await runner.process_observation(obs)

    assert res1["id"] == res2["id"]
    assert res1["_warning_events"] == res2["_warning_events"]

    graph = await graph_service.build_graph(hazard_id=res1["id"])
    warning_nodes = [n for n in graph["nodes"] if n["type"] == "Warning"]
    assert len(warning_nodes) == 1


@pytest.mark.anyio
async def test_b2a_warning_failure_is_non_fatal(graph_service):
    runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs = {
        "id": "obs-b2a-3",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-1",
        "vehicleLabel": "Sentinel Vehicle"
    }

    from unittest.mock import patch, AsyncMock
    with patch.object(graph_service, "record_warning", new=AsyncMock(side_effect=RuntimeError("warning db failed"))):
        res = await runner.process_observation(obs)

        # Verify success
        assert res is not None
        assert "id" in res
        assert "warnings" in res
        assert "en" in res["warnings"]
        assert res["_warning_events"] == []

        # Verify graph still has the hazard
        graph_hz = await graph_service.get_observation_hazard("obs-b2a-3")
        assert graph_hz is not None
        assert graph_hz["id"] == res["id"]

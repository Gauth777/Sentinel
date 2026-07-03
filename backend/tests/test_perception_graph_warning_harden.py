"""Focused tests for hardened Stage B2A warning persistence.

Run targeted:
    pytest backend/tests/test_perception_graph_warning_harden.py -v
"""

import os
import sys
import pytest
import math
from unittest.mock import MagicMock, AsyncMock, patch

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.perception_graph_service import PerceptionGraphService, SCENARIO_ID


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ============================================================================
# Mock Neo4j Infrastructure for Equivalence / Parity checks
# ============================================================================

class MockRecord:
    def __init__(self, data):
        self._data = data
    def __getitem__(self, key):
        return self._data[key]
    def get(self, key, default=None):
        return self._data.get(key, default)
    def keys(self):
        return self._data.keys()
    def data(self):
        return dict(self._data)


class MockResult:
    def __init__(self, records):
        self._records = [MockRecord(r) if isinstance(r, dict) else r for r in records]
        self._index = 0
    def __aiter__(self):
        self._index = 0
        return self
    async def __anext__(self):
        if self._index >= len(self._records):
            raise StopAsyncIteration
        r = self._records[self._index]
        self._index += 1
        return r
    async def single(self):
        if not self._records:
            return None
        return self._records[0]


class MockSession:
    def __init__(self, driver):
        self.driver = driver
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        pass
    async def run(self, query, **params):
        self.driver.queries.append((query, params))
        if self.driver.result_queue:
            return MockResult(self.driver.result_queue.pop(0))
        return MockResult([])
    async def execute_write(self, tx_func, **params):
        return await tx_func(self, **params)
    async def execute_read(self, tx_func, **params):
        return await tx_func(self, **params)


class MockNeo4jDriver:
    def __init__(self):
        self.queries = []
        self.result_queue = []
    def session(self, database=None):
        return MockSession(self)
    async def close(self):
        pass


# ============================================================================
# 1. Validation & Nodes Existence
# ============================================================================

@pytest.mark.anyio
async def test_warning_input_validations():
    svc = PerceptionGraphService()
    await svc.initialize()

    # Empty/invalid warning_id
    with pytest.raises(ValueError, match="warning_id"):
        await svc.record_warning("", "hz-1", "v-1", "text", "en")

    # Empty/invalid hazard_id
    with pytest.raises(ValueError, match="hazard_id"):
        await svc.record_warning("wrn-1", "", "v-1", "text", "en")

    # Empty/invalid vehicle_id
    with pytest.raises(ValueError, match="vehicle_id"):
        await svc.record_warning("wrn-1", "hz-1", "", "text", "en")

    # Empty/invalid warning_text
    with pytest.raises(ValueError, match="warning_text"):
        await svc.record_warning("wrn-1", "hz-1", "v-1", "", "en")

    # Invalid language
    with pytest.raises(ValueError, match="language"):
        await svc.record_warning("wrn-1", "hz-1", "v-1", "text", "fr")

    # Invalid road_segment_id
    with pytest.raises(ValueError, match="road_segment_id"):
        await svc.record_warning("wrn-1", "hz-1", "v-1", "text", "en", road_segment_id="")

    # Invalid timestamp
    with pytest.raises(ValueError, match="timestamp"):
        await svc.record_warning("wrn-1", "hz-1", "v-1", "text", "en", timestamp=-5.0)
    with pytest.raises(ValueError, match="timestamp"):
        await svc.record_warning("wrn-1", "hz-1", "v-1", "text", "en", timestamp=float("nan"))
    with pytest.raises(ValueError, match="timestamp"):
        await svc.record_warning("wrn-1", "hz-1", "v-1", "text", "en", timestamp=True)


@pytest.mark.anyio
async def test_missing_nodes_rejected_without_mutation():
    svc = PerceptionGraphService()
    await svc.initialize()

    # Empty state: Hazard doesn't exist
    with pytest.raises(ValueError, match="Hazard node hz-missing does not exist"):
        await svc.record_warning("wrn-1", "hz-missing", "v-1", "text", "en")

    # Record observation to create Hazard and Vehicle A, and RoadSegment
    await svc.record_observation(
        observation_id="obs-1",
        vehicle_id="v-A",
        vehicle_label="Vehicle A",
        hazard_id="hz-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        road_segment_id="seg-1",
        road_segment_name="Segment 1",
        timestamp=100.0,
    )

    # Hazard exists but Vehicle B is missing
    with pytest.raises(ValueError, match="Vehicle node v-B does not exist"):
        await svc.record_warning("wrn-1", "hz-1", "v-B", "text", "en")

    # Hazard & Vehicle A exist but RoadSegment seg-missing is missing
    with pytest.raises(ValueError, match="RoadSegment node seg-missing does not exist"):
        await svc.record_warning("wrn-1", "hz-1", "v-A", "text", "en", road_segment_id="seg-missing")

    # Ensure no placeholder Warning node was created in the process
    graph = await svc.build_graph(hazard_id="hz-1")
    warning_nodes = [n for n in graph["nodes"] if n["type"] == "Warning"]
    assert len(warning_nodes) == 0


# ============================================================================
# 2. Immutable Idempotency / Retries
# ============================================================================

@pytest.mark.anyio
async def test_warning_immutable_idempotency():
    svc = PerceptionGraphService()
    await svc.initialize()

    # Pre-populate observation (Hazard hz-1, Vehicle v-A, RoadSegment seg-1)
    await svc.record_observation(
        observation_id="obs-1",
        vehicle_id="v-A",
        vehicle_label="Vehicle A",
        hazard_id="hz-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        road_segment_id="seg-1",
        road_segment_name="Segment 1",
        timestamp=100.0,
    )

    # 1. First record_warning
    await svc.record_warning(
        warning_id="wrn-1",
        hazard_id="hz-1",
        vehicle_id="v-A",
        warning_text="Pothole ahead",
        language="en",
        road_segment_id="seg-1",
        timestamp=200.0,
    )

    graph = await svc.build_graph(hazard_id="hz-1")
    warning_nodes = [n for n in graph["nodes"] if n["type"] == "Warning"]
    assert len(warning_nodes) == 1
    warn_node = warning_nodes[0]
    assert warn_node["properties"]["timestamp"] == 200.0

    # 2. Exact retry: should keep original warning timestamp
    await svc.record_warning(
        warning_id="wrn-1",
        hazard_id="hz-1",
        vehicle_id="v-A",
        warning_text="Pothole ahead",
        language="en",
        road_segment_id="seg-1",
        timestamp=300.0, # different timestamp
    )

    graph2 = await svc.build_graph(hazard_id="hz-1")
    warning_nodes2 = [n for n in graph2["nodes"] if n["type"] == "Warning"]
    assert len(warning_nodes2) == 1
    assert warning_nodes2[0]["properties"]["timestamp"] == 200.0 # kept original

    # Exact retry produces exactly 1 TRIGGERED_WARNING and 1 DELIVERED_TO
    edges = graph2["edges"]
    triggered = [e for e in edges if e["type"] == "TRIGGERED_WARNING"]
    delivered = [e for e in edges if e["type"] == "DELIVERED_TO"]
    approaching = [e for e in edges if e["type"] == "APPROACHING"]
    assert len(triggered) == 1
    assert len(delivered) == 1
    assert len(approaching) == 1 # from the observation/warning merges

    # 3. Conflicts
    # Pre-create Hazard hz-2 to bypass existence check and test conflict
    await svc.record_observation(
        observation_id="obs-3",
        vehicle_id="v-A",
        vehicle_label="Vehicle A",
        hazard_id="hz-2",
        hazard_type="pothole",
        hazard_label="Pothole 2",
        road_segment_id="seg-1",
        road_segment_name="Segment 1",
        timestamp=100.0,
    )
    # Same warning ID but another hazard
    with pytest.raises(ValueError, match="conflict|relationships"):
        await svc.record_warning(
            warning_id="wrn-1",
            hazard_id="hz-2",
            vehicle_id="v-A",
            warning_text="Pothole ahead",
            language="en",
            road_segment_id="seg-1",
            timestamp=200.0,
        )

    # Same warning ID but another vehicle (pre-create Vehicle B first to satisfy existing vehicle requirement)
    await svc.record_observation(
        observation_id="obs-2",
        vehicle_id="v-B",
        vehicle_label="Vehicle B",
        hazard_id="hz-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        road_segment_id="seg-1",
        road_segment_name="Segment 1",
        timestamp=100.0,
    )
    with pytest.raises(ValueError, match="conflict|relationships"):
        await svc.record_warning(
            warning_id="wrn-1",
            hazard_id="hz-1",
            vehicle_id="v-B",
            warning_text="Pothole ahead",
            language="en",
            road_segment_id="seg-1",
            timestamp=200.0,
        )

    # Same warning ID with different text
    with pytest.raises(ValueError, match="conflict"):
        await svc.record_warning(
            warning_id="wrn-1",
            hazard_id="hz-1",
            vehicle_id="v-A",
            warning_text="Different warning text",
            language="en",
            road_segment_id="seg-1",
            timestamp=200.0,
        )

    # Same warning ID with different language
    with pytest.raises(ValueError, match="conflict"):
        await svc.record_warning(
            warning_id="wrn-1",
            hazard_id="hz-1",
            vehicle_id="v-A",
            warning_text="Pothole ahead",
            language="hi",
            road_segment_id="seg-1",
            timestamp=200.0,
        )


# ============================================================================
# 3. Same-Backend Limit (No Fallback / Mode Swapping on Warning Failure)
# ============================================================================

@pytest.mark.anyio
async def test_neo4j_warning_failure_does_not_invoke_memory_or_switch_mode():
    svc = PerceptionGraphService()
    svc._mode = "neo4j"
    svc._neo4j_connected = True
    svc._strict = False # non-strict mode to test that it still doesn't fallback

    # Mock Neo4j backend warning write to raise an exception
    svc._neo4j = MagicMock()
    svc._neo4j.record_warning = AsyncMock(side_effect=RuntimeError("Neo4j database connection lost"))

    # Mock memory backend to verify it is NEVER called
    svc._memory = MagicMock()
    svc._memory.record_warning = AsyncMock()

    # record_warning must raise sanitized RuntimeError
    with pytest.raises(RuntimeError, match="Neo4j warning write failed"):
        await svc.record_warning(
            warning_id="wrn-fail",
            hazard_id="hz-1",
            vehicle_id="v-A",
            warning_text="Pothole ahead",
            language="en",
        )

    # Verify memory backend was never called
    svc._memory.record_warning.assert_not_called()

    # Verify that the service did NOT switch mode to memory
    assert svc._mode == "neo4j"


@pytest.mark.anyio
async def test_workflow_returns_empty_warning_events_after_warning_failure():
    from workflows.hazard_workflow import LocalWorkflowRunner

    svc = PerceptionGraphService()
    await svc.initialize()

    # Mock record_warning to raise RuntimeError (simulating Neo4j or memory failure)
    with patch.object(svc, "record_warning", new=AsyncMock(side_effect=RuntimeError("DB connection error"))):
        runner = LocalWorkflowRunner(graph_service=svc, ego_location={"latitude": 12.9436, "longitude": 80.1502})
        obs = {
            "id": "obs-wf-fail",
            "type": "pothole",
            "label": "Pothole",
            "location": {"latitude": 12.9450, "longitude": 80.1503},
            "sourceVehicleId": "v-1",
            "vehicleLabel": "Sentinel Vehicle"
        }

        # Process should succeed but return empty _warning_events
        res = await runner.process_observation(obs)
        assert res is not None
        assert "id" in res
        assert res["_warning_events"] == []

        # Graph should still contain the hazard and observation
        graph_hz = await svc.get_observation_hazard("obs-wf-fail")
        assert graph_hz is not None
        assert graph_hz["id"] == res["id"]


# ============================================================================
# 4. Neo4j Repository Behavior (Validation & Idempotency / Retry Parity)
# ============================================================================

@pytest.mark.anyio
async def test_neo4j_warning_missing_nodes_rejected():
    svc = PerceptionGraphService()
    mock_driver = MockNeo4jDriver()
    svc._neo4j._driver = mock_driver
    svc._neo4j._database = "neo4j"
    svc._mode = "neo4j"
    svc._neo4j_connected = True

    # 1. Hazard missing
    # Result queue for hazard check query returns empty (not found)
    mock_driver.result_queue.append([]) 
    with pytest.raises(ValueError, match="Hazard node hz-1 does not exist"):
        await svc.record_warning("wrn-1", "hz-1", "v-A", "text", "en")

    # 2. Vehicle missing
    # Hazard check: exists
    mock_driver.result_queue.append([{"exists": True}])
    # Vehicle check: missing
    mock_driver.result_queue.append([]) 
    with pytest.raises(ValueError, match="Vehicle node v-A does not exist"):
        await svc.record_warning("wrn-1", "hz-1", "v-A", "text", "en")

    # 3. Road segment missing
    # Hazard check: exists
    mock_driver.result_queue.append([{"exists": True}])
    # Vehicle check: exists
    mock_driver.result_queue.append([{"exists": True}])
    # Road segment check: missing
    mock_driver.result_queue.append([])
    with pytest.raises(ValueError, match="RoadSegment node seg-1 does not exist"):
        await svc.record_warning("wrn-1", "hz-1", "v-A", "text", "en", road_segment_id="seg-1")


@pytest.mark.anyio
async def test_neo4j_warning_retry_and_conflicts():
    svc = PerceptionGraphService()
    mock_driver = MockNeo4jDriver()
    svc._neo4j._driver = mock_driver
    svc._neo4j._database = "neo4j"
    svc._mode = "neo4j"
    svc._neo4j_connected = True

    # --- Case A: Exact retry matches ---
    # Hazard exists
    mock_driver.result_queue.append([{"exists": True}])
    # Vehicle exists
    mock_driver.result_queue.append([{"exists": True}])
    # Warning merge returns text, lang, road segment
    mock_driver.result_queue.append([{
        "text": "Pothole ahead",
        "language": "en",
        "roadSegmentId": None
    }])
    # rel_check returns hazard_ids, vehicle_ids matching current
    mock_driver.result_queue.append([{
        "hazard_ids": ["hz-1"],
        "vehicle_ids": ["v-A"]
    }])

    # Call record_warning: should complete without calling CREATE queries
    await svc.record_warning("wrn-1", "hz-1", "v-A", "Pothole ahead", "en")

    # Verify no CREATE statements were run
    for q, p in mock_driver.queries:
        assert "CREATE (" not in q.upper()

    # --- Case B: Conflicts ---
    # Same warning ID but conflicting properties
    mock_driver.queries.clear()
    # Hazard exists
    mock_driver.result_queue.append([{"exists": True}])
    # Vehicle exists
    mock_driver.result_queue.append([{"exists": True}])
    # Warning merge returns different text
    mock_driver.result_queue.append([{
        "text": "Different text",
        "language": "en",
        "roadSegmentId": None
    }])

    with pytest.raises(ValueError, match="properties conflict"):
        await svc.record_warning("wrn-1", "hz-1", "v-A", "Pothole ahead", "en")


# ============================================================================
# 5. Stage B2B focused tests
# ============================================================================

@pytest.mark.anyio
async def test_upsert_vehicle_approach_validations():
    svc = PerceptionGraphService()
    await svc.initialize()

    # validate all four arguments as non-empty strings
    with pytest.raises(ValueError, match="vehicle_id"):
        await svc.upsert_vehicle_approach("", "Label", "road-1", "Road 1")
    with pytest.raises(ValueError, match="vehicle_label"):
        await svc.upsert_vehicle_approach("v-1", "", "road-1", "Road 1")
    with pytest.raises(ValueError, match="road_segment_id"):
        await svc.upsert_vehicle_approach("v-1", "Label", "", "Road 1")
    with pytest.raises(ValueError, match="road_segment_name"):
        await svc.upsert_vehicle_approach("v-1", "Label", "road-1", "")


@pytest.mark.anyio
async def test_upsert_vehicle_approach_memory_parity():
    svc = PerceptionGraphService()
    await svc.initialize() # default memory mode

    await svc.upsert_vehicle_approach("v-1", "Vehicle 1", "road-1", "Road 1")
    # Repeated seeding is idempotent
    await svc.upsert_vehicle_approach("v-1", "Vehicle 1", "road-1", "Road 1")

    # Conflict check: existing ID with wrong type
    # Hazard hz-1 exists
    await svc.record_observation(
        observation_id="obs-1",
        vehicle_id="v-1",
        vehicle_label="Vehicle 1",
        hazard_id="hz-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        road_segment_id="road-1",
        road_segment_name="Road 1",
        timestamp=100.0,
    )
    # Merging road-1 (which is RoadSegment) as Vehicle must raise ValueError
    with pytest.raises(ValueError, match="already exists|exists but is not"):
        await svc.upsert_vehicle_approach(
            vehicle_id="road-1",
            vehicle_label="Vehicle Wrong",
            road_segment_id="road-another",
            road_segment_name="Road Another"
        )


@pytest.mark.anyio
async def test_recipient_lookup_scenarios():
    svc = PerceptionGraphService()
    await svc.initialize()

    # Pre-create Hazard hz-1, Vehicle v-A, RoadSegment road-1
    await svc.record_observation(
        observation_id="obs-1",
        vehicle_id="v-A",
        vehicle_label="Vehicle A",
        hazard_id="hz-1",
        hazard_type="pothole",
        hazard_label="Pothole",
        road_segment_id="road-1",
        road_segment_name="Road 1",
        timestamp=100.0,
    )

    # Pre-create Hazard hz-2, Vehicle v-B, RoadSegment road-2
    await svc.record_observation(
        observation_id="obs-2",
        vehicle_id="v-B",
        vehicle_label="Vehicle B",
        hazard_id="hz-2",
        hazard_type="pothole",
        hazard_label="Pothole",
        road_segment_id="road-2",
        road_segment_name="Road 2",
        timestamp=100.0,
    )

    # Approach road-1 for peer vehicle v-C and v-D
    await svc.upsert_vehicle_approach("v-C", "Vehicle C", "road-1", "Road 1")
    await svc.upsert_vehicle_approach("v-D", "Vehicle D", "road-1", "Road 1")

    # 1. Same-road peers returned sorted & source vehicle excluded
    peers = await svc.get_warning_recipient_vehicle_ids(hazard_id="hz-1", source_vehicle_id="v-A")
    assert peers == ["v-C", "v-D"]

    # 2. Different-road vehicle v-B is excluded from hz-1 recipients
    assert "v-B" not in peers

    # 3. Duplicate APPROACHING edges do not duplicate recipients
    await svc.upsert_vehicle_approach("v-C", "Vehicle C", "road-1", "Road 1")
    peers_dup = await svc.get_warning_recipient_vehicle_ids(hazard_id="hz-1", source_vehicle_id="v-A")
    assert peers_dup == ["v-C", "v-D"]

    # 4. Resolved hazard returns no recipients
    # Set hazard status to resolved
    svc._memory._nodes["hz-1"]["properties"]["status"] = "resolved"
    peers_resolved = await svc.get_warning_recipient_vehicle_ids(hazard_id="hz-1", source_vehicle_id="v-A")
    assert peers_resolved == []

    # Restore active status
    svc._memory._nodes["hz-1"]["properties"]["status"] = "active"

    # 5. Zero or multiple valid ON_ROAD relationships rejected
    # Zero ON_ROAD: delete ON_ROAD edge
    edge_id = f"ON_ROAD:hz-1:road-1"
    old_edge = svc._memory._edges.pop(edge_id, None)
    with pytest.raises(ValueError, match="zero valid ON_ROAD"):
        await svc.get_warning_recipient_vehicle_ids(hazard_id="hz-1", source_vehicle_id="v-A")

    # Multiple ON_ROAD: put original and add a second road segment
    if old_edge:
        svc._memory._edges[edge_id] = old_edge
    await svc.upsert_vehicle_approach("v-dummy", "Dummy", "road-2", "Road 2")
    svc._memory._merge_edge_sync("ON_ROAD:hz-1:road-2", "ON_ROAD", "hz-1", "road-2", {})
    with pytest.raises(ValueError, match="multiple valid ON_ROAD"):
        await svc.get_warning_recipient_vehicle_ids(hazard_id="hz-1", source_vehicle_id="v-A")


@pytest.mark.anyio
async def test_workflow_peer_delivery_integration():
    from workflows.hazard_workflow import LocalWorkflowRunner

    svc = PerceptionGraphService()
    await svc.initialize()

    # Pre-register peers v-B and v-C approaching "gst"
    await svc.upsert_vehicle_approach("v-B", "Vehicle B", "gst", "GST Road Northbound")
    await svc.upsert_vehicle_approach("v-C", "Vehicle C", "gst", "GST Road Northbound")

    runner = LocalWorkflowRunner(graph_service=svc, ego_location={"latitude": 12.9436, "longitude": 80.1502})
    obs = {
        "id": "obs-wf-1",
        "type": "pothole",
        "label": "Pothole",
        "location": {"latitude": 12.9450, "longitude": 80.1503},
        "sourceVehicleId": "v-A",
        "vehicleLabel": "Vehicle A"
    }

    # Process: should write observation/hazard, source warning (v-A), and peer warnings (v-B, v-C)
    res = await runner.process_observation(obs)
    assert res is not None

    # 3 warning events: source v-A, peer v-B, peer v-C
    warning_events = res.get("_warning_events", [])
    assert len(warning_events) == 3

    # Check ordering is deterministic: source first, then peers sorted lexicographically
    # warning ID format: warn-{hazard_id}-{obs_id}-{vehicle_id}-{lang}
    hazard_id = res["id"]
    expected_source = f"warn-{hazard_id}-obs-wf-1-v-A-en"
    expected_b = f"warn-{hazard_id}-obs-wf-1-v-B-en"
    expected_c = f"warn-{hz_id if 'hz_id' in locals() else hazard_id}-obs-wf-1-v-C-en"
    assert warning_events == [expected_source, expected_b, expected_c]

    # Verify Warning nodes and relationships in the graph
    graph = await svc.build_graph(hazard_id=hazard_id)
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert expected_source in nodes
    assert expected_b in nodes
    assert expected_c in nodes

    # Duplicate observation retry does not duplicate nodes or relationships
    res_dup = await runner.process_observation(obs)
    assert res_dup["_warning_events"] == [expected_source, expected_b, expected_c]

    # Check peer warning failure doesn't block other peers (mock record_warning to fail for v-B only)
    orig_record_warning = svc.record_warning
    async def mock_record_warning(*args, **kwargs):
        if kwargs.get("vehicle_id") == "v-B":
            raise RuntimeError("v-B warning write failure")
        return await orig_record_warning(*args, **kwargs)

    with patch.object(svc, "record_warning", new=mock_record_warning):
        obs2 = {
            "id": "obs-wf-2",
            "type": "pothole",
            "label": "Pothole",
            "location": {"latitude": 12.9450, "longitude": 80.1503},
            "sourceVehicleId": "v-A",
            "vehicleLabel": "Vehicle A"
        }
        res2 = await runner.process_observation(obs2)
        # Should record v-A and v-C warnings successfully and skip v-B
        hz2_id = res2["id"]
        expected_source2 = f"warn-{hz2_id}-obs-wf-2-v-A-en"
        expected_c2 = f"warn-{hz2_id}-obs-wf-2-v-C-en"
        assert res2["_warning_events"] == [expected_source2, expected_c2]

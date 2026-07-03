import os
import sys
import math
import pytest
from datetime import datetime

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.perception_graph_service import PerceptionGraphService, SCENARIO_ID, RISK_LEVELS
from utils.geo import haversine_meters

# ---------------------------------------------------------------------------
# Neo4j Mocks
# ---------------------------------------------------------------------------

class MockRecord:
    def __init__(self, data_dict):
        self._data = data_dict
    def __getitem__(self, key):
        return self._data[key]
    def get(self, key, default=None):
        return self._data.get(key, default)
    def keys(self):
        return self._data.keys()
    def __iter__(self):
        return iter(self._data)
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

# ---------------------------------------------------------------------------
# Memory Backend Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_get_observation_hazard_unknown():
    # 1. get_observation_hazard returns None for unknown observation.
    svc = PerceptionGraphService()
    await svc.initialize()
    res = await svc.get_observation_hazard("obs-unknown")
    assert res is None

@pytest.mark.anyio
async def test_upsert_creates_hazard_and_observation():
    # 2. New upsert creates one hazard and one observation.
    # 3. Returned normalized hazard contains the required stable keys.
    # 4. New hazard returns hazardCreated=true and observationCreated=true.
    svc = PerceptionGraphService()
    await svc.initialize()
    res = await svc.upsert_observation_and_hazard(
        observation_id="obs-1",
        vehicle_id="v-1",
        vehicle_label="Vehicle 1",
        hazard_id="hz-1",
        hazard_type="construction",
        hazard_label="Construction Hazard",
        latitude=37.7749,
        longitude=-122.4194,
        road_segment_id="road-1",
        road_segment_name="Market St",
        timestamp=1000.0,
        hazard_fields={"risk": "medium", "visibilityState": "visible"}
    )
    assert res["hazardCreated"] is True
    assert res["observationCreated"] is True

    hz = res["hazard"]
    assert hz["id"] == "hz-1"
    assert hz["type"] == "construction"
    assert hz["location"]["latitude"] == 37.7749
    assert hz["location"]["longitude"] == -122.4194
    assert hz["segment_id"] == "road-1"
    assert hz["status"] == "active"
    assert hz["created_at"] == 1000.0
    assert hz["updated_at"] == 1000.0
    assert hz["sources"] == 1
    assert hz["source_vehicles"] == ["v-1"]
    assert hz["confidence"] == 60
    assert hz["risk"] == "medium"
    assert hz["visibilityState"] == "visible"

@pytest.mark.anyio
async def test_upsert_idempotent():
    # 5. Duplicate identical observation is idempotent.
    svc = PerceptionGraphService()
    await svc.initialize()

    # First upsert
    await svc.upsert_observation_and_hazard(
        observation_id="obs-1",
        vehicle_id="v-1",
        vehicle_label="Vehicle 1",
        hazard_id="hz-1",
        hazard_type="construction",
        hazard_label="Construction Hazard",
        latitude=37.7749,
        longitude=-122.4194,
        road_segment_id="road-1",
        road_segment_name="Market St",
        timestamp=1000.0
    )

    # Duplicate identical upsert
    res = await svc.upsert_observation_and_hazard(
        observation_id="obs-1",
        vehicle_id="v-1",
        vehicle_label="Vehicle 1",
        hazard_id="hz-1",
        hazard_type="construction",
        hazard_label="Construction Hazard",
        latitude=37.7749,
        longitude=-122.4194,
        road_segment_id="road-1",
        road_segment_name="Market St",
        timestamp=1000.0
    )
    assert res["hazardCreated"] is False
    assert res["observationCreated"] is False
    assert res["hazard"]["sources"] == 1
    assert res["hazard"]["source_vehicles"] == ["v-1"]

@pytest.mark.anyio
async def test_upsert_vehicle_conflict():
    # 6. Duplicate observation with different vehicle raises ValueError.
    svc = PerceptionGraphService()
    await svc.initialize()

    await svc.upsert_observation_and_hazard(
        observation_id="obs-1",
        vehicle_id="v-1",
        vehicle_label="Vehicle 1",
        hazard_id="hz-1",
        hazard_type="construction",
        hazard_label="Construction Hazard",
        latitude=37.7749,
        longitude=-122.4194,
        road_segment_id="road-1",
        road_segment_name="Market St",
        timestamp=1000.0
    )

    with pytest.raises(ValueError, match="already linked to vehicle"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1",
            vehicle_id="v-2",  # Different vehicle
            vehicle_label="Vehicle 2",
            hazard_id="hz-1",
            hazard_type="construction",
            hazard_label="Construction Hazard",
            latitude=37.7749,
            longitude=-122.4194,
            road_segment_id="road-1",
            road_segment_name="Market St",
            timestamp=1000.0
        )

@pytest.mark.anyio
async def test_upsert_hazard_conflict():
    # 7. Duplicate observation with different hazard raises ValueError.
    svc = PerceptionGraphService()
    await svc.initialize()

    await svc.upsert_observation_and_hazard(
        observation_id="obs-1",
        vehicle_id="v-1",
        vehicle_label="Vehicle 1",
        hazard_id="hz-1",
        hazard_type="construction",
        hazard_label="Construction Hazard",
        latitude=37.7749,
        longitude=-122.4194,
        road_segment_id="road-1",
        road_segment_name="Market St",
        timestamp=1000.0
    )

    with pytest.raises(ValueError, match="already linked to hazard"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1",
            vehicle_id="v-1",
            vehicle_label="Vehicle 1",
            hazard_id="hz-2",  # Different hazard
            hazard_type="construction",
            hazard_label="Construction Hazard",
            latitude=37.7749,
            longitude=-122.4194,
            road_segment_id="road-1",
            road_segment_name="Market St",
            timestamp=1000.0
        )

@pytest.mark.anyio
async def test_upsert_type_conflict():
    # 8. Existing hazard with different type raises ValueError.
    svc = PerceptionGraphService()
    await svc.initialize()

    await svc.upsert_observation_and_hazard(
        observation_id="obs-1",
        vehicle_id="v-1",
        vehicle_label="Vehicle 1",
        hazard_id="hz-1",
        hazard_type="construction",
        hazard_label="Construction Hazard",
        latitude=37.7749,
        longitude=-122.4194,
        road_segment_id="road-1",
        road_segment_name="Market St",
        timestamp=1000.0
    )

    with pytest.raises(ValueError, match="exists with type"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-2",
            vehicle_id="v-2",
            vehicle_label="Vehicle 2",
            hazard_id="hz-1",  # Same hazard ID
            hazard_type="pothole",  # Different type
            hazard_label="Pothole Hazard",
            latitude=37.7749,
            longitude=-122.4194,
            road_segment_id="road-1",
            road_segment_name="Market St",
            timestamp=1000.0
        )

@pytest.mark.anyio
async def test_upsert_road_segment_conflict():
    # 9. Existing hazard with different road segment raises ValueError.
    svc = PerceptionGraphService()
    await svc.initialize()

    await svc.upsert_observation_and_hazard(
        observation_id="obs-1",
        vehicle_id="v-1",
        vehicle_label="Vehicle 1",
        hazard_id="hz-1",
        hazard_type="construction",
        hazard_label="Construction Hazard",
        latitude=37.7749,
        longitude=-122.4194,
        road_segment_id="road-1",
        road_segment_name="Market St",
        timestamp=1000.0
    )

    with pytest.raises(ValueError, match="already connected to road segment"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-2",
            vehicle_id="v-2",
            vehicle_label="Vehicle 2",
            hazard_id="hz-1",
            hazard_type="construction",
            hazard_label="Construction Hazard",
            latitude=37.7749,
            longitude=-122.4194,
            road_segment_id="road-2",  # Different road segment
            road_segment_name="Mission St",
            timestamp=1000.0
        )

@pytest.mark.anyio
async def test_multi_vehicle_confidence():
    # 10. Second distinct vehicle raises sources from 1 to 2 and confidence 60 to 80.
    # 11. Second observation from the same vehicle does not increase sources.
    # 12. Three distinct vehicles produce confidence 100.
    # 13. source_vehicles is unique and sorted.
    svc = PerceptionGraphService()
    await svc.initialize()

    # 1st vehicle
    res1 = await svc.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-1", hazard_type="construction", hazard_label="Hazard 1",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1000.0
    )
    assert res1["hazard"]["sources"] == 1
    assert res1["hazard"]["confidence"] == 60
    assert res1["hazard"]["source_vehicles"] == ["v-1"]

    # Same vehicle, another observation
    res2 = await svc.upsert_observation_and_hazard(
        observation_id="obs-2", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-1", hazard_type="construction", hazard_label="Hazard 1",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1001.0
    )
    assert res2["hazard"]["sources"] == 1
    assert res2["hazard"]["confidence"] == 60

    # 2nd vehicle
    res3 = await svc.upsert_observation_and_hazard(
        observation_id="obs-3", vehicle_id="v-3", vehicle_label="V3",
        hazard_id="hz-1", hazard_type="construction", hazard_label="Hazard 1",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1002.0
    )
    assert res3["hazard"]["sources"] == 2
    assert res3["hazard"]["confidence"] == 80
    assert res3["hazard"]["source_vehicles"] == ["v-1", "v-3"]  # sorted

    # 3rd vehicle
    res4 = await svc.upsert_observation_and_hazard(
        observation_id="obs-4", vehicle_id="v-2", vehicle_label="V2",
        hazard_id="hz-1", hazard_type="construction", hazard_label="Hazard 1",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1003.0
    )
    assert res4["hazard"]["sources"] == 3
    assert res4["hazard"]["confidence"] == 100
    assert res4["hazard"]["source_vehicles"] == ["v-1", "v-2", "v-3"]  # sorted unique

@pytest.mark.anyio
async def test_find_similar_active_hazard_filtering():
    # 14. find_similar_active_hazard filters by type, active status, road segment, radius, min_updated_at.
    # 16. Resolved hazards are not returned as similar matches.
    svc = PerceptionGraphService()
    await svc.initialize()

    # Create an active hazard
    await svc.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-active", hazard_type="construction", hazard_label="Active Construction",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1000.0,
        hazard_fields={"status": "active"}
    )

    # Create a resolved hazard
    await svc.upsert_observation_and_hazard(
        observation_id="obs-2", vehicle_id="v-2", vehicle_label="V2",
        hazard_id="hz-resolved", hazard_type="construction", hazard_label="Resolved Construction",
        latitude=37.7750, longitude=-122.4195, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1005.0,
        hazard_fields={"status": "resolved"}
    )

    # Try finding similar hazard
    # 1. Matching type and active status on road-1
    res = await svc.find_similar_active_hazard(
        hazard_type="construction", latitude=37.7749, longitude=-122.4194,
        road_segment_id="road-1", radius_m=500.0, min_updated_at=999.0
    )
    assert res is not None
    assert res["hazard"]["id"] == "hz-active"

    # 2. Type mismatch
    res_type = await svc.find_similar_active_hazard(
        hazard_type="pothole", latitude=37.7749, longitude=-122.4194,
        road_segment_id="road-1", radius_m=500.0, min_updated_at=999.0
    )
    assert res_type is None

    # 3. Road segment mismatch
    res_road = await svc.find_similar_active_hazard(
        hazard_type="construction", latitude=37.7749, longitude=-122.4194,
        road_segment_id="road-2", radius_m=500.0, min_updated_at=999.0
    )
    assert res_road is None

    # 4. Out of radius
    res_radius = await svc.find_similar_active_hazard(
        hazard_type="construction", latitude=37.7800, longitude=-122.4200,
        road_segment_id="road-1", radius_m=500.0, min_updated_at=999.0
    )
    assert res_radius is None

    # 5. min_updated_at filter
    res_time = await svc.find_similar_active_hazard(
        hazard_type="construction", latitude=37.7749, longitude=-122.4194,
        road_segment_id="road-1", radius_m=500.0, min_updated_at=1001.0
    )
    assert res_time is None

@pytest.mark.anyio
async def test_find_similar_active_hazard_ordering():
    # 15. Similar-hazard ordering is deterministic.
    svc = PerceptionGraphService()
    await svc.initialize()

    # 3 active hazards on same road, same type, all within radius
    # hz-1: dist=11m, updated_at=1000.0
    # hz-2: dist=22m, updated_at=1005.0
    # hz-3: dist=11m, updated_at=1005.0

    await svc.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1000.0
    )

    await svc.upsert_observation_and_hazard(
        observation_id="obs-2", vehicle_id="v-2", vehicle_label="V2",
        hazard_id="hz-2", hazard_type="pothole", hazard_label="P2",
        latitude=37.7750, longitude=-122.4195, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1005.0
    )

    await svc.upsert_observation_and_hazard(
        observation_id="obs-3", vehicle_id="v-3", vehicle_label="V3",
        hazard_id="hz-3", hazard_type="pothole", hazard_label="P3",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1005.0
    )

    # Query from 37.7749, -122.4194 (same as hz-1 and hz-3)
    res = await svc.find_similar_active_hazard(
        hazard_type="pothole", latitude=37.7749, longitude=-122.4194,
        road_segment_id="road-1", radius_m=500.0, min_updated_at=999.0
    )
    assert res is not None
    # hz-1 and hz-3 both have dist=0m (smallest). hz-3 has updated_at=1005.0 (newest), hz-1 has 1000.0.
    # Therefore hz-3 must be selected because of newest updated_at.
    assert res["hazard"]["id"] == "hz-3"

@pytest.mark.anyio
async def test_risk_decrease_prevented():
    # 17. Risk cannot decrease on an existing hazard.
    svc = PerceptionGraphService()
    await svc.initialize()

    await svc.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1000.0,
        hazard_fields={"risk": "high"}
    )

    with pytest.raises(ValueError, match="Cannot decrease risk level"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-2", vehicle_id="v-2", vehicle_label="V2",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
            road_segment_name="Road 1", timestamp=1001.0,
            hazard_fields={"risk": "medium"}  # medium is < high
        )

@pytest.mark.anyio
async def test_validation_rules():
    # 18. Unknown hazard_fields keys are rejected before mutation.
    # 19. Invalid coordinates and numeric values are rejected.
    svc = PerceptionGraphService()
    await svc.initialize()

    # Unknown key
    with pytest.raises(ValueError, match="Unknown key in hazard_fields"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
            road_segment_name="Road 1", timestamp=1000.0,
            hazard_fields={"unknownKey": 123}
        )

    # Invalid coordinates
    with pytest.raises(ValueError, match="latitude must be <= 90"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=95.0, longitude=-122.4194, road_segment_id="road-1",
            road_segment_name="Road 1", timestamp=1000.0
        )

    with pytest.raises(ValueError, match="longitude must be >= -180"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=37.7749, longitude=-190.0, road_segment_id="road-1",
            road_segment_name="Road 1", timestamp=1000.0
        )

@pytest.mark.anyio
async def test_validation_failure_no_mutation():
    # 20. Failure leaves memory graph unchanged.
    svc = PerceptionGraphService()
    await svc.initialize()

    # Create initial state
    await svc.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1000.0,
        hazard_fields={"risk": "medium"}
    )

    # Try updating but fail with validation
    with pytest.raises(ValueError, match="Cannot decrease risk level"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-2", vehicle_id="v-2", vehicle_label="V2",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
            road_segment_name="Road 1", timestamp=1001.0,
            hazard_fields={"risk": "low"}
        )

    # Check that hazard remained unchanged
    graph_res = await svc.build_graph(hazard_id="hz-1")
    hz_node = next(n for n in graph_res["nodes"] if n["id"] == "hz-1")
    assert hz_node["properties"]["risk"] == "medium"
    assert not any(n["id"] == "obs-2" for n in graph_res["nodes"])

# ---------------------------------------------------------------------------
# Neo4j Backend Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_neo4j_queries_scenario_scoped(monkeypatch):
    # 21. Queries are scoped by scenario_id.
    svc = PerceptionGraphService()
    mock_driver = MockNeo4jDriver()

    monkeypatch.setattr(svc._neo4j, "_driver", mock_driver)
    monkeypatch.setattr(svc._neo4j, "_database", "neo4j")
    svc._mode = "neo4j"
    svc._neo4j_connected = True

    # Mock return values for get_observation_hazard query
    mock_driver.result_queue.append([{"hazard_id": "hz-1"}])  # lookup
    mock_driver.result_queue.append([{"h": {"id": "hz-1", "latitude": 37.0, "longitude": -122.0}, "segment_id": "road-1"}]) # norm
    mock_driver.result_queue.append([{"vehicle_id": "v-1"}]) # vehicles

    await svc.get_observation_hazard("obs-1")

    # Verify that scenario_id was passed to the query
    assert len(mock_driver.queries) > 0
    for query, params in mock_driver.queries:
        assert "scenario_id" in params
        assert params["scenario_id"] == SCENARIO_ID

@pytest.mark.anyio
async def test_neo4j_find_similar_cypher(monkeypatch):
    # 22. Similar matching uses point.distance and parameterized coordinates.
    # 23. Similar matching does not interpolate user values into Cypher.
    # 24. Deterministic ORDER BY is present.
    svc = PerceptionGraphService()
    mock_driver = MockNeo4jDriver()

    monkeypatch.setattr(svc._neo4j, "_driver", mock_driver)
    monkeypatch.setattr(svc._neo4j, "_database", "neo4j")
    svc._mode = "neo4j"
    svc._neo4j_connected = True

    mock_driver.result_queue.append([{"hazard_id": "hz-1", "dist": 5.0}])  # find query
    mock_driver.result_queue.append([{"h": {"id": "hz-1", "latitude": 37.0, "longitude": -122.0}, "segment_id": "road-1"}]) # norm query
    mock_driver.result_queue.append([{"vehicle_id": "v-1"}]) # vehicles query

    await svc.find_similar_active_hazard(
        hazard_type="pothole", latitude=37.7749, longitude=-122.4194,
        road_segment_id="road-1", radius_m=500.0, min_updated_at=1000.0
    )

    # Extract query
    find_query, params = mock_driver.queries[0]
    assert "point.distance" in find_query
    assert "ORDER BY dist ASC, h_updated DESC, h.id ASC" in find_query
    assert params["latitude"] == 37.7749
    assert params["longitude"] == -122.4194
    assert params["radius_m"] == 500.0

@pytest.mark.anyio
async def test_neo4j_upsert_managed_tx(monkeypatch):
    # 25. Upsert uses a managed write transaction.
    # 27. Idempotent retry returns observationCreated=false.
    svc = PerceptionGraphService()
    mock_driver = MockNeo4jDriver()

    monkeypatch.setattr(svc._neo4j, "_driver", mock_driver)
    monkeypatch.setattr(svc._neo4j, "_database", "neo4j")
    svc._mode = "neo4j"
    svc._neo4j_connected = True

    # 1. First mock results for a non-existing obs and non-existing hazard
    mock_driver.result_queue.append([{"exists": False, "vehicle_ids": [], "hazard_ids": [], "road_ids": []}]) # obs exists
    mock_driver.result_queue.append([{"hazard_node": None, "road_ids": []}]) # hazard exists
    mock_driver.result_queue.append([]) # merge V
    mock_driver.result_queue.append([]) # merge R
    mock_driver.result_queue.append([]) # create H
    mock_driver.result_queue.append([]) # merge Obs
    mock_driver.result_queue.append([]) # merge edges
    mock_driver.result_queue.append([]) # stats query
    # norm query
    mock_driver.result_queue.append([{"h": {"id": "hz-1", "latitude": 37.7749, "longitude": -122.4194}, "segment_id": "road-1"}])
    # vehicles query
    mock_driver.result_queue.append([{"vehicle_id": "v-1"}])

    res = await svc.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1000.0
    )

    assert res["hazardCreated"] is True
    assert res["observationCreated"] is True

    # 2. Duplicate idempotent retry
    mock_driver.result_queue.clear()
    mock_driver.queries.clear()

    mock_driver.result_queue.append([{"exists": True, "vehicle_ids": ["v-1"], "hazard_ids": ["hz-1"], "road_ids": ["road-1"]}]) # obs exists
    mock_driver.result_queue.append([{"h": {"id": "hz-1", "type": "pothole", "latitude": 37.7749, "longitude": -122.4194}}]) # hazard exists
    mock_driver.result_queue.append([{"vehicle_id": "v-1"}]) # vehicles query

    res2 = await svc.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1000.0
    )
    assert res2["hazardCreated"] is False
    assert res2["observationCreated"] is False

@pytest.mark.anyio
async def test_neo4j_validation_failure_rolls_back(monkeypatch):
    # 26. Validation failure rolls back/no write query is committed.
    svc = PerceptionGraphService()
    mock_driver = MockNeo4jDriver()

    monkeypatch.setattr(svc._neo4j, "_driver", mock_driver)
    monkeypatch.setattr(svc._neo4j, "_database", "neo4j")
    svc._mode = "neo4j"
    svc._neo4j_connected = True

    # Mock existing hazard with risk high (new observation)
    mock_driver.result_queue.append([{"exists": False, "vehicle_id": None, "hazard_id": None}]) # obs does not exist
    mock_driver.result_queue.append([{"hazard_node": {"id": "hz-1", "type": "pothole", "risk": "high"}, "road_ids": ["road-1"]}]) # hazard exists

    # Try upserting with lower risk
    with pytest.raises(ValueError, match="Cannot decrease risk level"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
            road_segment_name="Road 1", timestamp=1000.0,
            hazard_fields={"risk": "medium"}
        )

    # Verify no writing MERGE or CREATE queries were run
    for q, p in mock_driver.queries:
        assert "MERGE" not in q
        assert "CREATE" not in q
        assert "SET" not in q

@pytest.mark.anyio
async def test_strict_mode_dispatch_rules(monkeypatch):
    # 30. New facade methods obey existing strict/no-fallback dispatch.
    svc = PerceptionGraphService()
    monkeypatch.setenv("SENTINEL_NEO4J_STRICT", "true")
    monkeypatch.setenv("NEO4J_ENABLED", "true")

    # Mock Neo4j initialize to fail
    async def mock_neo4j_init_fail():
        raise RuntimeError("Neo4j connection failed")
    monkeypatch.setattr(svc._neo4j, "initialize", mock_neo4j_init_fail)

    with pytest.raises(RuntimeError, match="initialization failed"):
        await svc.initialize()

    with pytest.raises(RuntimeError, match="Active mode is not neo4j"):
        await svc.get_observation_hazard("obs-1")

    with pytest.raises(RuntimeError, match="Active mode is not neo4j"):
        await svc.find_similar_active_hazard(
            hazard_type="pothole", latitude=37.7749, longitude=-122.4194,
            road_segment_id="road-1", radius_m=500.0, min_updated_at=1000.0
        )

    with pytest.raises(RuntimeError, match="Active mode is not neo4j"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
            road_segment_name="Road 1", timestamp=1000.0
        )


@pytest.mark.anyio
async def test_idempotent_retry_memory():
    svc = PerceptionGraphService()
    await svc.initialize()

    res1 = await svc.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1000.0,
        hazard_fields={"risk": "medium", "status": "active"}
    )
    assert res1["hazardCreated"] is True
    assert res1["observationCreated"] is True
    assert res1["hazard"]["updated_at"] == 1000.0
    assert res1["hazard"]["risk"] == "medium"

    res2 = await svc.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1005.0,
        hazard_fields={"risk": "high", "status": "resolved"}
    )
    assert res2["hazardCreated"] is False
    assert res2["observationCreated"] is False
    assert res2["hazard"]["updated_at"] == 1000.0
    assert res2["hazard"]["risk"] == "medium"
    assert res2["hazard"]["status"] == "active"


@pytest.mark.anyio
async def test_idempotent_retry_neo4j_queries(monkeypatch):
    svc = PerceptionGraphService()
    mock_driver = MockNeo4jDriver()

    monkeypatch.setattr(svc._neo4j, "_driver", mock_driver)
    monkeypatch.setattr(svc._neo4j, "_database", "neo4j")
    svc._mode = "neo4j"
    svc._neo4j_connected = True

    mock_driver.result_queue.append([{"exists": True, "vehicle_ids": ["v-1"], "hazard_ids": ["hz-1"], "road_ids": ["road-1"]}])
    mock_driver.result_queue.append([{"h": {"id": "hz-1", "type": "pothole", "latitude": 37.0, "longitude": -122.0}}])
    mock_driver.result_queue.append([{"vehicle_id": "v-1"}])

    res = await svc.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1000.0
    )
    assert res["hazardCreated"] is False
    assert res["observationCreated"] is False

    for query, params in mock_driver.queries:
        upper_query = query.upper()
        assert "MERGE" not in upper_query
        assert "CREATE" not in upper_query
        assert "SET" not in upper_query


@pytest.mark.anyio
async def test_memory_atomic_rollback_late_failure():
    svc = PerceptionGraphService()
    await svc.initialize()

    svc._memory._merge_node_sync("road-conflict", "Vehicle", "Conflicting Vehicle Node", {})

    initial_nodes = dict(svc._memory._nodes)
    initial_edges = dict(svc._memory._edges)

    with pytest.raises(ValueError, match="cannot merge as RoadSegment"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1", vehicle_id="v-new", vehicle_label="VNew",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=37.7749, longitude=-122.4194, road_segment_id="road-conflict",
            road_segment_name="Road Conflict", timestamp=1000.0
        )

    assert svc._memory._nodes == initial_nodes
    assert svc._memory._edges == initial_edges


@pytest.mark.anyio
async def test_memory_observation_lookup_complete_path():
    svc = PerceptionGraphService()
    await svc.initialize()

    svc._memory._merge_node_sync("obs-only", "Observation", "Obs Only", {"type": "pothole"})
    svc._memory._merge_node_sync("hz-only", "Hazard", "Hz Only", {"type": "pothole"})
    svc._memory._merge_edge_sync("SUPPORTS:obs-only:hz-only", "SUPPORTS", "obs-only", "hz-only", {})
    assert await svc.get_observation_hazard("obs-only") is None

    svc._memory._merge_node_sync("source-hz", "Hazard", "Source Hz", {"type": "pothole"})
    svc._memory._merge_edge_sync("OBSERVED:source-hz:obs-only", "OBSERVED", "source-hz", "obs-only", {})
    assert await svc.get_observation_hazard("obs-only") is None

    del svc._memory._edges["OBSERVED:source-hz:obs-only"]

    svc._memory._merge_node_sync("v-1", "Vehicle", "V1", {})
    svc._memory._merge_edge_sync("OBSERVED:v-1:obs-only", "OBSERVED", "v-1", "obs-only", {})
    del svc._memory._edges["SUPPORTS:obs-only:hz-only"]
    assert await svc.get_observation_hazard("obs-only") is None

    svc._memory._merge_node_sync("hz-bad", "Vehicle", "Hz Bad", {})
    svc._memory._merge_edge_sync("SUPPORTS:obs-only:hz-bad", "SUPPORTS", "obs-only", "hz-bad", {})
    assert await svc.get_observation_hazard("obs-only") is None


@pytest.mark.anyio
async def test_required_string_validation():
    svc = PerceptionGraphService()
    await svc.initialize()

    async def assert_val_err(**kwargs):
        base = {
            "observation_id": "obs-1", "vehicle_id": "v-1", "vehicle_label": "V1",
            "hazard_id": "hz-1", "hazard_type": "pothole", "hazard_label": "P1",
            "latitude": 37.7749, "longitude": -122.4194, "road_segment_id": "road-1",
            "road_segment_name": "Road 1", "timestamp": 1000.0
        }
        base.update(kwargs)
        with pytest.raises(ValueError):
            await svc.upsert_observation_and_hazard(**base)

    await assert_val_err(observation_id="")
    await assert_val_err(observation_id="   ")
    await assert_val_err(vehicle_id="")
    await assert_val_err(vehicle_label="")
    await assert_val_err(hazard_id="")
    await assert_val_err(hazard_type="")
    await assert_val_err(hazard_label="")
    await assert_val_err(road_segment_id="")
    await assert_val_err(road_segment_name="")

    await assert_val_err(observation_id=123)
    await assert_val_err(vehicle_id=True)
    await assert_val_err(hazard_label=None)


@pytest.mark.anyio
async def test_boolean_numeric_validation():
    svc = PerceptionGraphService()
    await svc.initialize()

    async def assert_bool_rejected(**kwargs):
        base = {
            "observation_id": "obs-1", "vehicle_id": "v-1", "vehicle_label": "V1",
            "hazard_id": "hz-1", "hazard_type": "pothole", "hazard_label": "P1",
            "latitude": 37.7749, "longitude": -122.4194, "road_segment_id": "road-1",
            "road_segment_name": "Road 1", "timestamp": 1000.0
        }
        base.update(kwargs)
        with pytest.raises(ValueError, match="must be a number, not a boolean"):
            await svc.upsert_observation_and_hazard(**base)

    await assert_bool_rejected(latitude=True)
    await assert_bool_rejected(longitude=False)
    await assert_bool_rejected(timestamp=True)

    with pytest.raises(ValueError, match="must be a number, not a boolean"):
        await svc.find_similar_active_hazard(
            hazard_type="pothole", latitude=37.7749, longitude=-122.4194,
            road_segment_id="road-1", radius_m=True, min_updated_at=1000.0
        )


@pytest.mark.anyio
async def test_malformed_stored_hazards_skipped():
    svc = PerceptionGraphService()
    await svc.initialize()

    svc._memory._merge_node_sync("road-1", "RoadSegment", "Road 1", {})

    async def create_malformed_hazard(hz_id, props):
        svc._memory._merge_node_sync(hz_id, "Hazard", hz_id, props)
        svc._memory._merge_edge_sync(f"ON_ROAD:{hz_id}:road-1", "ON_ROAD", hz_id, "road-1", {})

    await create_malformed_hazard("hz-valid", {
        "type": "pothole", "latitude": 37.7749, "longitude": -122.4194,
        "status": "active", "updated_at": 1000.0
    })

    await create_malformed_hazard("hz-bad-lat1", {
        "type": "pothole", "latitude": "not-a-number", "longitude": -122.4194,
        "status": "active", "updated_at": 1000.0
    })
    await create_malformed_hazard("hz-bad-lat2", {
        "type": "pothole", "latitude": True, "longitude": -122.4194,
        "status": "active", "updated_at": 1000.0
    })
    import math
    await create_malformed_hazard("hz-bad-lat3", {
        "type": "pothole", "latitude": float("nan"), "longitude": -122.4194,
        "status": "active", "updated_at": 1000.0
    })
    await create_malformed_hazard("hz-bad-lon", {
        "type": "pothole", "latitude": 37.7749, "longitude": 200.0,
        "status": "active", "updated_at": 1000.0
    })
    await create_malformed_hazard("hz-bad-status", {
        "type": "pothole", "latitude": 37.7749, "longitude": -122.4194,
        "status": "invalid-status-value", "updated_at": 1000.0
    })
    await create_malformed_hazard("hz-bad-upd", {
        "type": "pothole", "latitude": 37.7749, "longitude": -122.4194,
        "status": "active", "updated_at": float("inf")
    })

    res = await svc.find_similar_active_hazard(
        hazard_type="pothole", latitude=37.7749, longitude=-122.4194,
        road_segment_id="road-1", radius_m=500.0, min_updated_at=999.0
    )
    assert res is not None
    assert res["hazard"]["id"] == "hz-valid"


@pytest.mark.anyio
async def test_neo4j_incomplete_obs_handling(monkeypatch):
    svc = PerceptionGraphService()
    mock_driver = MockNeo4jDriver()
    monkeypatch.setattr(svc._neo4j, "_driver", mock_driver)
    monkeypatch.setattr(svc._neo4j, "_database", "neo4j")
    svc._mode = "neo4j"
    svc._neo4j_connected = True

    # 1. Missing OBSERVED relation (exists=True, empty vehicle_ids, valid hazard, valid road)
    mock_driver.result_queue.append([{
        "exists": True, "vehicle_ids": [], "hazard_ids": ["hz-1"], "road_ids": ["road-1"]
    }])
    with pytest.raises(ValueError, match="missing an OBSERVED relationship"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
            road_segment_name="Road 1", timestamp=1000.0
        )

    # 2. Missing SUPPORTS relation (exists=True, valid vehicle, empty hazard_ids, valid road)
    mock_driver.result_queue.append([{
        "exists": True, "vehicle_ids": ["v-1"], "hazard_ids": [], "road_ids": ["road-1"]
    }])
    with pytest.raises(ValueError, match="missing a SUPPORTS relationship"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
            road_segment_name="Road 1", timestamp=1000.0
        )

    # 3. Multiple linked vehicles (exists=True, multiple vehicle_ids)
    mock_driver.result_queue.append([{
        "exists": True, "vehicle_ids": ["v-1", "v-2"], "hazard_ids": ["hz-1"], "road_ids": ["road-1"]
    }])
    with pytest.raises(ValueError, match="linked to multiple vehicles"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
            road_segment_name="Road 1", timestamp=1000.0
        )

    # 4. Multiple linked hazards (exists=True, multiple hazard_ids)
    mock_driver.result_queue.append([{
        "exists": True, "vehicle_ids": ["v-1"], "hazard_ids": ["hz-1", "hz-2"], "road_ids": ["road-1"]
    }])
    with pytest.raises(ValueError, match="linked to multiple hazards"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
            road_segment_name="Road 1", timestamp=1000.0
        )

    # 5. Missing ON_ROAD relationship (exists=True, valid vehicle, valid hazard, empty road_ids)
    mock_driver.result_queue.append([{
        "exists": True, "vehicle_ids": ["v-1"], "hazard_ids": ["hz-1"], "road_ids": []
    }])
    with pytest.raises(ValueError, match="missing an ON_ROAD relationship"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
            road_segment_name="Road 1", timestamp=1000.0
        )

    # 6. Multiple ON_ROAD relationships (exists=True, valid vehicle, valid hazard, multiple road_ids)
    mock_driver.result_queue.append([{
        "exists": True, "vehicle_ids": ["v-1"], "hazard_ids": ["hz-1"], "road_ids": ["road-1", "road-2"]
    }])
    with pytest.raises(ValueError, match="connected to multiple road segments"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
            road_segment_name="Road 1", timestamp=1000.0
        )

    for query, params in mock_driver.queries:
        uq = query.upper()
        assert "MERGE" not in uq
        assert "CREATE" not in uq
        assert "SET" not in uq
        assert "DELETE" not in uq


@pytest.mark.anyio
async def test_memory_idempotency_parity_edge_cases():
    svc = PerceptionGraphService()
    await svc.initialize()

    svc._memory._merge_node_sync("obs-1", "Observation", "Obs 1", {"type": "pothole"})
    svc._memory._merge_node_sync("v-1", "Vehicle", "V1", {})
    svc._memory._merge_node_sync("hz-1", "Hazard", "Pothole Hazard", {"type": "pothole"})
    svc._memory._merge_node_sync("road-1", "RoadSegment", "Road 1", {})

    svc._memory._merge_edge_sync("OBSERVED:v-1:obs-1", "OBSERVED", "v-1", "obs-1", {})
    svc._memory._edges["OBSERVED:v-1:obs-1"]["scenarioId"] = "other-scenario"
    svc._memory._merge_edge_sync("SUPPORTS:obs-1:hz-1", "SUPPORTS", "obs-1", "hz-1", {})
    svc._memory._merge_edge_sync("ON_ROAD:hz-1:road-1", "ON_ROAD", "hz-1", "road-1", {})

    with pytest.raises(ValueError, match="must have exactly one scenario-scoped OBSERVED relationship"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
            road_segment_name="Road 1", timestamp=1000.0
        )

    svc._memory._edges["OBSERVED:v-1:obs-1"]["scenarioId"] = SCENARIO_ID

    del svc._memory._edges["SUPPORTS:obs-1:hz-1"]
    svc._memory._merge_edge_sync("SUPPORTS:obs-1:v-1", "SUPPORTS", "obs-1", "v-1", {})
    with pytest.raises(ValueError, match="Observation obs-1 is already linked to hazard v-1"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
            road_segment_name="Road 1", timestamp=1000.0
        )


@pytest.mark.anyio
async def test_hazard_fields_safety_checks():
    svc = PerceptionGraphService()
    await svc.initialize()

    with pytest.raises(ValueError, match="direction must be a string"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
            road_segment_name="Road 1", timestamp=1000.0,
            hazard_fields={"direction": {"nested": "dict"}}
        )

    res = await svc.upsert_observation_and_hazard(
        observation_id="obs-2", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-2", hazard_type="pothole", hazard_label="P2",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1000.0,
        hazard_fields={"distanceMeters": 150, "replayConfidence": 1}
    )
    assert isinstance(res["hazard"]["distanceMeters"], float)
    assert res["hazard"]["distanceMeters"] == 150.0
    assert isinstance(res["hazard"]["replayConfidence"], float)
    assert res["hazard"]["replayConfidence"] == 1.0


# ---------------------------------------------------------------------------
# Scenario Isolation Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_memory_source_count_ignores_cross_scenario_vehicle():
    """Source count must not include a Vehicle whose scenarioId differs."""
    svc = PerceptionGraphService()
    await svc.initialize()

    # Create a valid first observation via the facade
    res = await svc.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1000.0
    )
    assert res["hazard"]["sources"] == 1

    # Manually inject a second vehicle with a different scenario
    svc._memory._merge_node_sync("v-cross", "Vehicle", "Cross Vehicle", {})
    svc._memory._nodes["v-cross"]["scenarioId"] = "other-scenario"
    svc._memory._merge_node_sync("obs-2", "Observation", "Obs 2", {"type": "pothole"})
    svc._memory._merge_edge_sync("OBSERVED:v-cross:obs-2", "OBSERVED", "v-cross", "obs-2", {})
    svc._memory._merge_edge_sync("SUPPORTS:obs-2:hz-1", "SUPPORTS", "obs-2", "hz-1", {})

    # Normalize: cross-scenario vehicle must not be counted
    hazard = svc._memory._normalize_hazard_record_sync("hz-1")
    assert hazard["sources"] == 1
    assert "v-cross" not in hazard["source_vehicles"]


@pytest.mark.anyio
async def test_memory_source_count_ignores_cross_scenario_observation():
    """Source count must not traverse an Observation whose scenarioId differs."""
    svc = PerceptionGraphService()
    await svc.initialize()

    res = await svc.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1000.0
    )
    assert res["hazard"]["sources"] == 1

    # Manually inject a second observation with a different scenario
    svc._memory._merge_node_sync("v-2", "Vehicle", "V2", {})
    svc._memory._merge_node_sync("obs-cross", "Observation", "Obs Cross", {"type": "pothole"})
    svc._memory._nodes["obs-cross"]["scenarioId"] = "other-scenario"
    svc._memory._merge_edge_sync("OBSERVED:v-2:obs-cross", "OBSERVED", "v-2", "obs-cross", {})
    svc._memory._merge_edge_sync("SUPPORTS:obs-cross:hz-1", "SUPPORTS", "obs-cross", "hz-1", {})

    hazard = svc._memory._normalize_hazard_record_sync("hz-1")
    assert hazard["sources"] == 1
    assert "v-2" not in hazard["source_vehicles"]


@pytest.mark.anyio
async def test_memory_source_count_ignores_cross_scenario_edges():
    """Source count must not traverse OBSERVED/SUPPORTS edges with wrong scenarioId."""
    svc = PerceptionGraphService()
    await svc.initialize()

    res = await svc.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1000.0
    )
    assert res["hazard"]["sources"] == 1

    # Inject valid nodes but with cross-scenario edges
    svc._memory._merge_node_sync("v-2", "Vehicle", "V2", {})
    svc._memory._merge_node_sync("obs-2", "Observation", "Obs 2", {"type": "pothole"})
    svc._memory._merge_edge_sync("OBSERVED:v-2:obs-2", "OBSERVED", "v-2", "obs-2", {})
    svc._memory._edges["OBSERVED:v-2:obs-2"]["scenarioId"] = "other-scenario"
    svc._memory._merge_edge_sync("SUPPORTS:obs-2:hz-1", "SUPPORTS", "obs-2", "hz-1", {})

    hazard = svc._memory._normalize_hazard_record_sync("hz-1")
    assert hazard["sources"] == 1
    assert "v-2" not in hazard["source_vehicles"]

    # Now test cross-scenario SUPPORTS
    svc._memory._edges["OBSERVED:v-2:obs-2"]["scenarioId"] = SCENARIO_ID
    svc._memory._edges["SUPPORTS:obs-2:hz-1"]["scenarioId"] = "other-scenario"

    hazard = svc._memory._normalize_hazard_record_sync("hz-1")
    assert hazard["sources"] == 1
    assert "v-2" not in hazard["source_vehicles"]


@pytest.mark.anyio
async def test_memory_source_count_ignores_wrong_type_nodes():
    """Source count must not include nodes with wrong type labels."""
    svc = PerceptionGraphService()
    await svc.initialize()

    res = await svc.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1000.0
    )
    assert res["hazard"]["sources"] == 1

    # Inject a node with type "Warning" where a "Vehicle" is expected
    svc._memory._merge_node_sync("not-a-vehicle", "Warning", "W1", {})
    svc._memory._merge_node_sync("obs-2", "Observation", "Obs 2", {"type": "pothole"})
    svc._memory._merge_edge_sync("OBSERVED:not-a-vehicle:obs-2", "OBSERVED", "not-a-vehicle", "obs-2", {})
    svc._memory._merge_edge_sync("SUPPORTS:obs-2:hz-1", "SUPPORTS", "obs-2", "hz-1", {})

    hazard = svc._memory._normalize_hazard_record_sync("hz-1")
    assert hazard["sources"] == 1
    assert "not-a-vehicle" not in hazard["source_vehicles"]

    # Inject a node with type "Hazard" where "Observation" is expected
    svc._memory._merge_node_sync("v-3", "Vehicle", "V3", {})
    svc._memory._merge_node_sync("not-an-obs", "Hazard", "H2", {"type": "pothole"})
    svc._memory._merge_edge_sync("OBSERVED:v-3:not-an-obs", "OBSERVED", "v-3", "not-an-obs", {})
    svc._memory._merge_edge_sync("SUPPORTS:not-an-obs:hz-1", "SUPPORTS", "not-an-obs", "hz-1", {})

    hazard = svc._memory._normalize_hazard_record_sync("hz-1")
    assert hazard["sources"] == 1
    assert "v-3" not in hazard["source_vehicles"]


@pytest.mark.anyio
async def test_memory_segment_ignores_cross_scenario_road():
    """Segment normalization must not return a cross-scenario RoadSegment."""
    svc = PerceptionGraphService()
    await svc.initialize()

    svc._memory._merge_node_sync("hz-1", "Hazard", "P1", {
        "type": "pothole", "latitude": 37.7749, "longitude": -122.4194,
        "status": "active", "created_at": 1000.0, "updated_at": 1000.0
    })
    svc._memory._merge_node_sync("road-cross", "RoadSegment", "Cross Road", {})
    svc._memory._nodes["road-cross"]["scenarioId"] = "other-scenario"
    svc._memory._merge_edge_sync("ON_ROAD:hz-1:road-cross", "ON_ROAD", "hz-1", "road-cross", {})

    hazard = svc._memory._normalize_hazard_record_sync("hz-1")
    assert hazard["segment_id"] == ""

    # Also test wrong-type node masquerading as a road
    svc._memory._merge_node_sync("not-a-road", "Vehicle", "V1", {})
    svc._memory._merge_edge_sync("ON_ROAD:hz-1:not-a-road", "ON_ROAD", "hz-1", "not-a-road", {})

    hazard = svc._memory._normalize_hazard_record_sync("hz-1")
    assert hazard["segment_id"] == ""


@pytest.mark.anyio
async def test_neo4j_source_stat_queries_scope_all_nodes(monkeypatch):
    """Neo4j source/stat queries must scope Vehicle, Observation, Hazard and RoadSegment nodes."""
    svc = PerceptionGraphService()
    mock_driver = MockNeo4jDriver()
    monkeypatch.setattr(svc._neo4j, "_driver", mock_driver)
    monkeypatch.setattr(svc._neo4j, "_database", "neo4j")
    svc._mode = "neo4j"
    svc._neo4j_connected = True

    # Mock _get_normalized_hazard_neo4j: road query + source query
    mock_driver.result_queue.append([{
        "h": {"id": "hz-1", "type": "pothole", "latitude": 37.0, "longitude": -122.0},
        "segment_id": "road-1"
    }])
    mock_driver.result_queue.append([{"vehicle_id": "v-1"}, {"vehicle_id": "v-2"}])

    await svc._neo4j._get_normalized_hazard_neo4j("hz-1")

    # Road query
    road_query = mock_driver.queries[0][0]
    assert "{scenario_id: $scenario_id}]->(r:SentinelPerception:RoadSegment {scenario_id: $scenario_id})" in road_query

    # Source query
    src_query = mock_driver.queries[1][0]
    assert "(v:SentinelPerception:Vehicle {scenario_id: $scenario_id})" in src_query
    assert "(o:SentinelPerception:Observation {scenario_id: $scenario_id})" in src_query
    assert "(h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})" in src_query


@pytest.mark.anyio
async def test_neo4j_similarity_excludes_infinite_updated_at(monkeypatch):
    """Similarity Cypher must explicitly exclude positive infinity on updated_at."""
    svc = PerceptionGraphService()
    mock_driver = MockNeo4jDriver()
    monkeypatch.setattr(svc._neo4j, "_driver", mock_driver)
    monkeypatch.setattr(svc._neo4j, "_database", "neo4j")
    svc._mode = "neo4j"
    svc._neo4j_connected = True

    mock_driver.result_queue.append([{"hazard_id": "hz-1", "dist": 5.0}])
    mock_driver.result_queue.append([{
        "h": {"id": "hz-1", "latitude": 37.0, "longitude": -122.0},
        "segment_id": "road-1"
    }])
    mock_driver.result_queue.append([{"vehicle_id": "v-1"}])

    await svc.find_similar_active_hazard(
        hazard_type="pothole", latitude=37.7749, longitude=-122.4194,
        road_segment_id="road-1", radius_m=500.0, min_updated_at=1000.0
    )

    find_query = mock_driver.queries[0][0]
    assert "h_updated < 1.0e308" in find_query
    assert "h_updated = h_updated" in find_query
    assert "h_updated >= 0.0" in find_query


@pytest.mark.anyio
async def test_three_vehicle_source_count_confidence_100():
    """Three distinct vehicles must produce confidence 100."""
    svc = PerceptionGraphService()
    await svc.initialize()

    for i in range(1, 4):
        await svc.upsert_observation_and_hazard(
            observation_id=f"obs-{i}", vehicle_id=f"v-{i}", vehicle_label=f"V{i}",
            hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
            latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
            road_segment_name="Road 1", timestamp=1000.0 + i
        )

    hazard = svc._memory._normalize_hazard_record_sync("hz-1")
    assert hazard["sources"] == 3
    assert hazard["confidence"] == 100
    assert sorted(hazard["source_vehicles"]) == ["v-1", "v-2", "v-3"]


@pytest.mark.anyio
async def test_memory_update_hazard_stats_ignores_cross_scenario():
    """_update_hazard_stats must not count cross-scenario contributions."""
    svc = PerceptionGraphService()
    await svc.initialize()

    # Create initial valid data
    res = await svc.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-1", hazard_type="pothole", hazard_label="P1",
        latitude=37.7749, longitude=-122.4194, road_segment_id="road-1",
        road_segment_name="Road 1", timestamp=1000.0
    )
    assert res["hazard"]["sources"] == 1

    # Manually inject a second vehicle with cross-scenario edges
    svc._memory._merge_node_sync("v-2", "Vehicle", "V2", {})
    svc._memory._merge_node_sync("obs-2", "Observation", "Obs 2", {"type": "pothole"})
    svc._memory._merge_edge_sync("OBSERVED:v-2:obs-2", "OBSERVED", "v-2", "obs-2", {})
    svc._memory._merge_edge_sync("SUPPORTS:obs-2:hz-1", "SUPPORTS", "obs-2", "hz-1", {})
    svc._memory._edges["SUPPORTS:obs-2:hz-1"]["scenarioId"] = "other-scenario"

    # Re-run stats
    svc._memory._update_hazard_stats("hz-1")
    props = svc._memory._nodes["hz-1"]["properties"]
    assert props["sourceCount"] == 1
    assert props["confidence"] == 60


@pytest.mark.anyio
async def test_scoping_gap_checks():
    svc = PerceptionGraphService()
    await svc.initialize()
    
    # 1. Multiple valid roads rejected
    svc._memory._merge_node_sync("hz-test", "Hazard", "Test Hazard", {"type": "pothole", "latitude": 12.9450, "longitude": 80.1503, "status": "active"})
    svc._memory._merge_node_sync("gst", "RoadSegment", "GST", {})
    svc._memory._merge_node_sync("side", "RoadSegment", "Side", {})
    svc._memory._merge_edge_sync("ON_ROAD:hz-test:gst", "ON_ROAD", "hz-test", "gst", {})
    svc._memory._merge_edge_sync("ON_ROAD:hz-test:side", "ON_ROAD", "hz-test", "side", {})
    
    with pytest.raises(ValueError, match="must have exactly one scenario-scoped ON_ROAD relationship"):
        await svc.upsert_observation_and_hazard(
            observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
            hazard_id="hz-test", hazard_type="pothole", hazard_label="P1",
            latitude=12.9450, longitude=80.1503, road_segment_id="gst",
            road_segment_name="GST", timestamp=1000.0
        )

    # 2. Cross-scenario ON_ROAD edge ignored
    svc2 = PerceptionGraphService()
    await svc2.initialize()
    svc2._memory._merge_node_sync("hz-test", "Hazard", "Test Hazard", {"type": "pothole", "latitude": 12.9450, "longitude": 80.1503, "status": "active"})
    svc2._memory._merge_node_sync("gst", "RoadSegment", "GST", {})
    svc2._memory._merge_edge_sync("ON_ROAD:hz-test:gst", "ON_ROAD", "hz-test", "gst", {})
    svc2._memory._merge_edge_sync("ON_ROAD:hz-test:other", "ON_ROAD", "hz-test", "gst", {})
    svc2._memory._edges["ON_ROAD:hz-test:other"]["scenarioId"] = "other-scenario"
    
    await svc2.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-test", hazard_type="pothole", hazard_label="P1",
        latitude=12.9450, longitude=80.1503, road_segment_id="gst",
        road_segment_name="GST", timestamp=1000.0
    )

    # 3. Wrong-type road target ignored
    svc3 = PerceptionGraphService()
    await svc3.initialize()
    svc3._memory._merge_node_sync("hz-test", "Hazard", "Test Hazard", {"type": "pothole", "latitude": 12.9450, "longitude": 80.1503, "status": "active"})
    svc3._memory._merge_node_sync("gst", "RoadSegment", "GST", {})
    svc3._memory._merge_node_sync("v-wrong", "Vehicle", "Wrong Node", {})
    svc3._memory._merge_edge_sync("ON_ROAD:hz-test:gst", "ON_ROAD", "hz-test", "gst", {})
    svc3._memory._merge_edge_sync("ON_ROAD:hz-test:v-wrong", "ON_ROAD", "hz-test", "v-wrong", {})
    
    await svc3.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-test", hazard_type="pothole", hazard_label="P1",
        latitude=12.9450, longitude=80.1503, road_segment_id="gst",
        road_segment_name="GST", timestamp=1000.0
    )

    # 4. Cross-scenario RoadSegment ignored
    svc4 = PerceptionGraphService()
    await svc4.initialize()
    svc4._memory._merge_node_sync("hz-test", "Hazard", "Test Hazard", {"type": "pothole", "latitude": 12.9450, "longitude": 80.1503, "status": "active"})
    svc4._memory._merge_node_sync("gst", "RoadSegment", "GST", {})
    svc4._memory._merge_node_sync("cross-road", "RoadSegment", "Cross Road", {})
    svc4._memory._nodes["cross-road"]["scenarioId"] = "other-scenario"
    svc4._memory._merge_edge_sync("ON_ROAD:hz-test:gst", "ON_ROAD", "hz-test", "gst", {})
    svc4._memory._merge_edge_sync("ON_ROAD:hz-test:cross", "ON_ROAD", "hz-test", "cross-road", {})
    
    await svc4.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-test", hazard_type="pothole", hazard_label="P1",
        latitude=12.9450, longitude=80.1503, road_segment_id="gst",
        road_segment_name="GST", timestamp=1000.0
    )

    # 5. One valid road plus malformed foreign edges succeeds
    svcf = PerceptionGraphService()
    await svcf.initialize()
    svcf._memory._merge_node_sync("hz-test", "Hazard", "Test Hazard", {"type": "pothole", "latitude": 12.9450, "longitude": 80.1503, "status": "active"})
    svcf._memory._merge_node_sync("gst", "RoadSegment", "GST", {})
    svcf._memory._merge_node_sync("cross-road", "RoadSegment", "Cross Road", {})
    svcf._memory._nodes["cross-road"]["scenarioId"] = "other-scenario"
    svcf._memory._merge_node_sync("v-wrong", "Vehicle", "Wrong Node", {})
    
    svcf._memory._merge_edge_sync("ON_ROAD:hz-test:gst", "ON_ROAD", "hz-test", "gst", {})
    svcf._memory._merge_edge_sync("ON_ROAD:hz-test:cross", "ON_ROAD", "hz-test", "cross-road", {})
    svcf._memory._merge_edge_sync("ON_ROAD:hz-test:v-wrong", "ON_ROAD", "hz-test", "v-wrong", {})
    svcf._memory._merge_edge_sync("ON_ROAD:hz-test:other-edge", "ON_ROAD", "hz-test", "gst", {})
    svcf._memory._edges["ON_ROAD:hz-test:other-edge"]["scenarioId"] = "other-scenario"
    
    await svcf.upsert_observation_and_hazard(
        observation_id="obs-1", vehicle_id="v-1", vehicle_label="V1",
        hazard_id="hz-test", hazard_type="pothole", hazard_label="P1",
        latitude=12.9450, longitude=80.1503, road_segment_id="gst",
        road_segment_name="GST", timestamp=1000.0
    )


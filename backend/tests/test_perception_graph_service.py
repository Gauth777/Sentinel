"""Tests for PerceptionGraphService.

Run targeted:
    pytest backend/tests/test_perception_graph_service.py -q

Run full regression:
    pytest backend/tests -q
"""

import asyncio
import os
import sys
import types

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import pytest
from services.perception_graph_service import PerceptionGraphService, SCENARIO_ID


# ---------------------------------------------------------------------------
# Fake Neo4j infrastructure
# ---------------------------------------------------------------------------

class FakeNode:
    def __init__(self, node_id, labels, **props):
        self._id = node_id
        self._labels = frozenset(labels)
        self._props = {"id": node_id, **props}

    @property
    def labels(self):
        return self._labels

    def __getitem__(self, key):
        return self._props[key]

    def __iter__(self):
        return iter(self._props)

    def keys(self):
        return self._props.keys()

    def get(self, key, default=None):
        return self._props.get(key, default)


class FakeRelationship:
    def __init__(self, rel_type, start_node, end_node, **props):
        self.type = rel_type
        self.start_node = start_node
        self.end_node = end_node
        self._props = props

    def __getitem__(self, key):
        return self._props[key]

    def __iter__(self):
        return iter(self._props)

    def keys(self):
        return self._props.keys()

    def get(self, key, default=None):
        return self._props.get(key, default)


class FakeRecord:
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


class FakeResult:
    def __init__(self, records):
        self._records = records
        self._index = 0

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self):
        if self._index >= len(self._records):
            raise StopAsyncIteration
        record = self._records[self._index]
        self._index += 1
        return record

    async def single(self):
        if not self._records:
            return None
        return self._records[0]


class FakeSession:
    def __init__(self, driver, records):
        self.driver = driver
        self.queries = []
        self._records = records

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def run(self, query, **params):
        self.queries.append((query, params))
        if "CONSTRAINT" in query.upper() and getattr(self.driver, "_fail_constraints", False):
            raise RuntimeError("Fake constraint failure")
        return FakeResult(self._records)


class FakeNeo4jDriver:
    def __init__(self):
        self.closed = False
        self.sessions = []
        self._next_result = []
        self._fail_constraints = False

    def session(self, database=None):
        sess = FakeSession(self, self._next_result)
        self.sessions.append(sess)
        self._next_result = []
        return sess

    def set_next_result(self, records):
        self._next_result = records

    def fail_constraints(self, fail=True):
        self._fail_constraints = fail

    async def verify_connectivity(self):
        pass

    async def close(self):
        self.closed = True


class FakeNeo4jModule:
    class AsyncGraphDatabase:
        @staticmethod
        def driver(uri, auth=None):
            return FakeNeo4jDriver()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_neo4j_env(monkeypatch):
    """Ensure no test can accidentally connect to a real Neo4j instance."""
    for key in list(os.environ.keys()):
        if key.startswith("NEO4J_"):
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def fake_neo4j_module(monkeypatch):
    fake_mod = FakeNeo4jModule()
    monkeypatch.setitem(sys.modules, "neo4j", fake_mod)
    return fake_mod


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _make_service():
    svc = PerceptionGraphService()
    await svc.initialize()
    return svc


# ---------------------------------------------------------------------------
# 1. Import succeeds without Neo4j configuration
# ---------------------------------------------------------------------------

def test_import_without_neo4j_config():
    svc = PerceptionGraphService()
    assert svc._mode == "memory"


# ---------------------------------------------------------------------------
# 2. Empty graph
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_empty_graph():
    svc = await _make_service()
    graph = await svc.build_graph()
    assert graph["mode"] == "memory"
    assert graph["focusHazardId"] is None
    assert graph["nodes"] == []
    assert graph["edges"] == []
    assert graph["summary"]["nodeCount"] == 0
    assert graph["summary"]["edgeCount"] == 0
    assert graph["summary"]["focus"] is None
    required_keys = {
        "mode",
        "generatedAt",
        "focusHazardId",
        "nodes",
        "edges",
        "summary",
        "timeline",
    }
    assert set(graph.keys()) == required_keys


# ---------------------------------------------------------------------------
# 3. One-source provenance chain
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_one_source_provenance_chain():
    svc = await _make_service()
    await svc.record_observation(
        observation_id="obs-1",
        vehicle_id="v-A",
        vehicle_label="Vehicle A",
        hazard_id="hz-1",
        hazard_type="pothole",
        hazard_label="Deep Pothole",
        road_segment_id="seg-gst",
        road_segment_name="GST Road",
        timestamp=1000.0,
    )
    await svc.record_warning(
        warning_id="wrn-1",
        hazard_id="hz-1",
        vehicle_id="v-B",
        warning_text="Pothole ahead",
        language="en",
        road_segment_id="seg-gst",
        timestamp=2000.0,
    )
    graph = await svc.build_graph(hazard_id="hz-1")
    assert graph["focusHazardId"] == "hz-1"
    assert graph["summary"]["hazardCount"] == 1
    assert graph["summary"]["observationCount"] == 1
    assert graph["summary"]["vehicleCount"] == 2
    assert graph["summary"]["warningCount"] == 1
    assert graph["summary"]["focus"]["sourceCount"] == 1
    assert graph["summary"]["focus"]["confidence"] == 60
    assert graph["summary"]["focus"]["warningCount"] == 1
    edge_types = {e["type"] for e in graph["edges"]}
    assert {
        "OBSERVED",
        "SUPPORTS",
        "ON_ROAD",
        "APPROACHING",
        "TRIGGERED_WARNING",
        "DELIVERED_TO",
    }.issubset(edge_types)


# ---------------------------------------------------------------------------
# 4. Two-source corroboration
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_two_source_corroboration():
    svc = await _make_service()
    await svc.record_observation(
        "obs-1", "v-A", "Vehicle A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
    )
    await svc.record_observation(
        "obs-2", "v-B", "Vehicle B", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 2000.0
    )
    graph = await svc.build_graph(hazard_id="hz-1")
    assert graph["summary"]["focus"]["sourceCount"] == 2
    assert graph["summary"]["focus"]["confidence"] == 80


# ---------------------------------------------------------------------------
# 5. Duplicate observation ID idempotency
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_duplicate_observation_idempotent():
    svc = await _make_service()
    await svc.record_observation(
        "obs-1", "v-A", "Vehicle A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
    )
    await svc.record_observation(
        "obs-1", "v-A", "Vehicle A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
    )
    graph = await svc.build_graph(hazard_id="hz-1")
    assert graph["summary"]["observationCount"] == 1
    assert graph["summary"]["focus"]["sourceCount"] == 1


# ---------------------------------------------------------------------------
# 6. Same vehicle does not count as an independent second source
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_same_vehicle_not_double_counted():
    svc = await _make_service()
    await svc.record_observation(
        "obs-1", "v-A", "Vehicle A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
    )
    await svc.record_observation(
        "obs-2", "v-A", "Vehicle A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 2000.0
    )
    graph = await svc.build_graph(hazard_id="hz-1")
    assert graph["summary"]["focus"]["sourceCount"] == 1
    assert graph["summary"]["focus"]["confidence"] == 60


# ---------------------------------------------------------------------------
# 7. Hazard filtering
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_hazard_filtering():
    svc = await _make_service()
    await svc.record_observation("obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0)
    await svc.record_observation("obs-2", "v-B", "B", "hz-2", "debris", "Debris", "seg-side", "Side", 2000.0)
    graph = await svc.build_graph(hazard_id="hz-1")
    assert graph["summary"]["hazardCount"] == 1
    hazard_ids = {n["id"] for n in graph["nodes"] if n["type"] == "Hazard"}
    assert hazard_ids == {"hz-1"}


# ---------------------------------------------------------------------------
# 8. Unknown hazard filtering
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_unknown_hazard_filtering():
    svc = await _make_service()
    await svc.record_observation("obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0)
    graph = await svc.build_graph(hazard_id="hz-unknown")
    assert graph["focusHazardId"] == "hz-unknown"
    assert graph["nodes"] == []
    assert graph["edges"] == []
    assert graph["summary"]["focus"] is None


# ---------------------------------------------------------------------------
# 9. Limit validation
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_limit_validation():
    svc = await _make_service()
    await svc.build_graph(limit=1)
    await svc.build_graph(limit=100)
    with pytest.raises(ValueError):
        await svc.build_graph(limit=0)
    with pytest.raises(ValueError):
        await svc.build_graph(limit=101)
    with pytest.raises(ValueError, match="integer"):
        await svc.build_graph(limit="25")
    with pytest.raises(ValueError, match="boolean"):
        await svc.build_graph(limit=True)


# ---------------------------------------------------------------------------
# 10. Complete warning chain
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_complete_warning_chain():
    svc = await _make_service()
    await svc.record_observation(
        "obs-1", "v-A", "Vehicle A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
    )
    await svc.record_observation(
        "obs-2", "v-B", "Vehicle B", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 2000.0
    )
    await svc.record_warning(
        "wrn-1", "hz-1", "v-C", "Pothole ahead", "en", "seg-gst", 3000.0
    )
    graph = await svc.build_graph(hazard_id="hz-1")
    assert graph["summary"]["focus"]["sourceCount"] == 2
    assert graph["summary"]["focus"]["confidence"] == 80
    assert graph["summary"]["focus"]["warningCount"] == 1
    edge_types = {e["type"] for e in graph["edges"]}
    assert {
        "OBSERVED",
        "SUPPORTS",
        "ON_ROAD",
        "APPROACHING",
        "TRIGGERED_WARNING",
        "DELIVERED_TO",
    }.issubset(edge_types)


# ---------------------------------------------------------------------------
# 11. Scoped demo reset
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_scoped_demo_reset():
    svc = await _make_service()
    await svc.record_observation("obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0)
    await svc.reset_demo_data()
    graph = await svc.build_graph()
    assert graph["summary"]["nodeCount"] == 0
    assert graph["summary"]["edgeCount"] == 0


# ---------------------------------------------------------------------------
# 12. Non-demo data survives reset
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_non_demo_data_survives_reset():
    svc = await _make_service()
    async with svc._memory._lock:
        svc._memory._nodes["other-node"] = {
            "id": "other-node",
            "type": "Vehicle",
            "label": "Other",
            "scenarioId": "other-scenario",
            "properties": {},
        }
    await svc.record_observation("obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0)
    await svc.reset_demo_data()
    async with svc._memory._lock:
        assert "other-node" in svc._memory._nodes
    graph = await svc.build_graph()
    assert graph["summary"]["nodeCount"] == 0


# ---------------------------------------------------------------------------
# 13. No credential leakage
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_no_credential_leakage(monkeypatch, fake_neo4j_module):
    monkeypatch.setenv("NEO4J_ENABLED", "true")
    monkeypatch.setenv("NEO4J_URI", "bolt://secret-host:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "secret-user")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret-pass")

    svc = PerceptionGraphService()
    await svc.initialize()
    graph = await svc.build_graph()

    graph_str = str(graph)
    assert "secret-host" not in graph_str
    assert "secret-user" not in graph_str
    assert "secret-pass" not in graph_str

    try:
        svc._mode = "neo4j"
        await svc.build_graph()
    except Exception as exc:
        exc_str = str(exc)
        assert "secret-host" not in exc_str
        assert "secret-user" not in exc_str
        assert "secret-pass" not in exc_str

    svc._mode = "memory"
    await svc.close()


# ---------------------------------------------------------------------------
# 14. Memory and Neo4j normalization use the same response schema
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_memory_neo4j_schema_parity(fake_neo4j_module, monkeypatch, clear_neo4j_env):
    # Build genuine memory graph
    memory_service = PerceptionGraphService()
    await memory_service.initialize()
    await memory_service.record_observation(
        "obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
    )
    memory_graph = await memory_service.build_graph(hazard_id="hz-1")

    # Build genuine fake-Neo4j graph
    monkeypatch.setenv("NEO4J_ENABLED", "true")
    monkeypatch.setenv("NEO4J_URI", "bolt://fake:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "user")
    monkeypatch.setenv("NEO4J_PASSWORD", "pass")

    neo4j_service = PerceptionGraphService()
    await neo4j_service.initialize()
    assert neo4j_service._mode == "neo4j"

    v_node = FakeNode("v-A", ["SentinelPerception", "Vehicle"], label="A")
    obs_node = FakeNode("obs-1", ["SentinelPerception", "Observation"], label="Observation obs-1", type="pothole", timestamp=1000.0)
    h_node = FakeNode("hz-1", ["SentinelPerception", "Hazard"], label="Pothole", type="pothole", sourceCount=1, confidence=60)
    r_node = FakeNode("seg-gst", ["SentinelPerception", "RoadSegment"], name="GST")

    obs_rel = FakeRelationship("OBSERVED", v_node, obs_node, scenario_id=SCENARIO_ID)
    sup_rel = FakeRelationship("SUPPORTS", obs_node, h_node, scenario_id=SCENARIO_ID)
    onr_rel = FakeRelationship("ON_ROAD", h_node, r_node, scenario_id=SCENARIO_ID)
    app_rel = FakeRelationship("APPROACHING", v_node, r_node, scenario_id=SCENARIO_ID)

    record = FakeRecord({
        "nodes": [v_node, obs_node, h_node, r_node],
        "edges": [obs_rel, sup_rel, onr_rel, app_rel],
    })

    fake_driver = neo4j_service._neo4j._driver
    fake_driver.set_next_result([record])
    neo4j_graph = await neo4j_service.build_graph(hazard_id="hz-1")

    required_keys = {
        "mode",
        "generatedAt",
        "focusHazardId",
        "nodes",
        "edges",
        "summary",
        "timeline",
    }
    assert set(memory_graph.keys()) == required_keys
    assert set(neo4j_graph.keys()) == required_keys

    for node in neo4j_graph["nodes"]:
        assert set(node.keys()) == {"id", "type", "label", "scenarioId", "properties"}
    for node in memory_graph["nodes"]:
        assert set(node.keys()) == {"id", "type", "label", "scenarioId", "properties"}

    for edge in neo4j_graph["edges"]:
        assert set(edge.keys()) == {"id", "type", "source", "target", "scenarioId", "properties"}
    for edge in memory_graph["edges"]:
        assert set(edge.keys()) == {"id", "type", "source", "target", "scenarioId", "properties"}

    summary_keys = {
        "nodeCount",
        "edgeCount",
        "vehicleCount",
        "observationCount",
        "hazardCount",
        "roadSegmentCount",
        "warningCount",
        "focus",
    }
    assert set(memory_graph["summary"].keys()) == summary_keys
    assert set(neo4j_graph["summary"].keys()) == summary_keys

    for item in neo4j_graph["timeline"]:
        assert set(item.keys()) == {"eventId", "timestamp", "type", "description"}
    for item in memory_graph["timeline"]:
        assert set(item.keys()) == {"eventId", "timestamp", "type", "description"}

    await memory_service.close()
    await neo4j_service.close()


# ---------------------------------------------------------------------------
# 15. Repeated initialization and closing are safe
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_repeated_init_close():
    svc = PerceptionGraphService()
    await svc.initialize()
    await svc.initialize()
    await svc.close()
    await svc.close()
    assert svc._mode == "memory"


# ---------------------------------------------------------------------------
# Additional: validation error on contradictory observation reuse
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_observation_reassignment_raises():
    svc = await _make_service()
    await svc.record_observation(
        "obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
    )
    with pytest.raises(ValueError, match="already owned by vehicle"):
        await svc.record_observation(
            "obs-1", "v-B", "B", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 2000.0
        )

    svc2 = await _make_service()
    await svc2.record_observation(
        "obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
    )
    with pytest.raises(ValueError, match="already supports hazard"):
        await svc2.record_observation(
            "obs-1", "v-A", "A", "hz-2", "pothole", "Pothole", "seg-gst", "GST", 2000.0
        )


# ---------------------------------------------------------------------------
# NEW: shared-road hazard isolation
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_shared_road_hazard_isolation():
    svc = await _make_service()
    await svc.record_observation("obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0)
    await svc.record_observation("obs-2", "v-B", "B", "hz-2", "debris", "Debris", "seg-gst", "GST", 2000.0)
    graph = await svc.build_graph(hazard_id="hz-1")
    hazard_ids = {n["id"] for n in graph["nodes"] if n["type"] == "Hazard"}
    assert hazard_ids == {"hz-1"}


# ---------------------------------------------------------------------------
# NEW: shared-vehicle hazard isolation
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_shared_vehicle_hazard_isolation():
    svc = await _make_service()
    await svc.record_observation("obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0)
    await svc.record_observation("obs-2", "v-A", "A", "hz-2", "debris", "Debris", "seg-side", "Side", 2000.0)
    graph = await svc.build_graph(hazard_id="hz-1")
    hazard_ids = {n["id"] for n in graph["nodes"] if n["type"] == "Hazard"}
    assert hazard_ids == {"hz-1"}


# ---------------------------------------------------------------------------
# NEW: no mutation after contradictory duplicate
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_no_mutation_after_contradictory_duplicate():
    svc = await _make_service()
    await svc.record_observation("obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0)
    with pytest.raises(ValueError, match="already owned by vehicle"):
        await svc.record_observation("obs-1", "v-B", "B", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 2000.0)
    graph = await svc.build_graph(hazard_id="hz-1")
    vehicle_ids = {n["id"] for n in graph["nodes"] if n["type"] == "Vehicle"}
    assert vehicle_ids == {"v-A"}


# ---------------------------------------------------------------------------
# NEW: concurrent duplicate idempotency
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_concurrent_duplicate_idempotency():
    svc = await _make_service()

    async def record():
        await svc.record_observation(
            "obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
        )

    await asyncio.gather(record(), record())
    graph = await svc.build_graph(hazard_id="hz-1")
    assert graph["summary"]["observationCount"] == 1
    assert graph["summary"]["focus"]["sourceCount"] == 1


# ---------------------------------------------------------------------------
# NEW: ValueError not swallowed by fallback
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_value_error_not_swallowed(fake_neo4j_module, monkeypatch, clear_neo4j_env):
    monkeypatch.setenv("NEO4J_ENABLED", "true")
    monkeypatch.setenv("NEO4J_URI", "bolt://fake:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "user")
    monkeypatch.setenv("NEO4J_PASSWORD", "pass")

    svc = PerceptionGraphService()
    await svc.initialize()
    assert svc._mode == "neo4j"

    async def raise_value_error(*args, **kwargs):
        raise ValueError("test validation error")

    svc._neo4j.record_observation = raise_value_error

    with pytest.raises(ValueError, match="test validation error"):
        await svc.record_observation(
            "obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
        )

    assert svc._mode == "neo4j"
    await svc.close()


# ---------------------------------------------------------------------------
# NEW: Hazard sourceCount and confidence properties
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_hazard_source_count_confidence_properties():
    svc = await _make_service()
    await svc.record_observation("obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0)
    graph = await svc.build_graph(hazard_id="hz-1")
    hazard = next(n for n in graph["nodes"] if n["id"] == "hz-1")
    assert hazard["properties"]["sourceCount"] == 1
    assert hazard["properties"]["confidence"] == 60
    assert graph["summary"]["focus"]["sourceCount"] == 1
    assert graph["summary"]["focus"]["confidence"] == 60

    await svc.record_observation("obs-2", "v-B", "B", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 2000.0)
    graph = await svc.build_graph(hazard_id="hz-1")
    hazard = next(n for n in graph["nodes"] if n["id"] == "hz-1")
    assert hazard["properties"]["sourceCount"] == 2
    assert hazard["properties"]["confidence"] == 80
    assert graph["summary"]["focus"]["sourceCount"] == 2
    assert graph["summary"]["focus"]["confidence"] == 80


# ---------------------------------------------------------------------------
# NEW: constraints attempted using fake Neo4j
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_constraints_attempted(fake_neo4j_module, monkeypatch, clear_neo4j_env):
    monkeypatch.setenv("NEO4J_ENABLED", "true")
    monkeypatch.setenv("NEO4J_URI", "bolt://fake:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "user")
    monkeypatch.setenv("NEO4J_PASSWORD", "pass")

    svc = PerceptionGraphService()
    await svc.initialize()
    assert svc._mode == "neo4j"

    fake_driver = svc._neo4j._driver
    assert fake_driver is not None
    assert len(fake_driver.sessions) >= 1

    constraint_queries = []
    for sess in fake_driver.sessions:
        for query, params in sess.queries:
            if "CONSTRAINT" in query.upper():
                constraint_queries.append(query)

    assert len(constraint_queries) == 5
    for q in constraint_queries:
        assert "REQUIRE (n.scenario_id, n.id) IS UNIQUE" in q
        assert "IS UNIQUE" in q
        assert ":SentinelPerception" not in q

    await svc.close()


# ---------------------------------------------------------------------------
# NEW: scoped reset query and absence of unrestricted deletion
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_scoped_reset_query(fake_neo4j_module, monkeypatch, clear_neo4j_env):
    monkeypatch.setenv("NEO4J_ENABLED", "true")
    monkeypatch.setenv("NEO4J_URI", "bolt://fake:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "user")
    monkeypatch.setenv("NEO4J_PASSWORD", "pass")

    svc = PerceptionGraphService()
    await svc.initialize()
    assert svc._mode == "neo4j"

    await svc.reset_demo_data()

    fake_driver = svc._neo4j._driver
    reset_queries = []
    for sess in fake_driver.sessions:
        for query, params in sess.queries:
            if "DELETE" in query.upper():
                reset_queries.append((query, params))

    assert len(reset_queries) == 1
    query, params = reset_queries[0]
    assert ":SentinelPerception" in query
    assert "scenario_id" in query
    assert "MATCH (n) DETACH DELETE n" not in query
    assert params.get("scenario_id") == SCENARIO_ID

    await svc.close()


# ---------------------------------------------------------------------------
# NEW: constraint failure triggers clean memory fallback, no credential leak
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_constraint_failure_fallback(fake_neo4j_module, monkeypatch, clear_neo4j_env, caplog):
    monkeypatch.setenv("NEO4J_ENABLED", "true")
    monkeypatch.setenv("NEO4J_URI", "bolt://fake:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "user")
    monkeypatch.setenv("NEO4J_PASSWORD", "pass")

    failing_driver = FakeNeo4jDriver()
    failing_driver.fail_constraints(True)
    monkeypatch.setattr(fake_neo4j_module.AsyncGraphDatabase, "driver", lambda uri, auth=None: failing_driver)

    svc = PerceptionGraphService()
    await svc.initialize()
    assert svc._mode == "memory"
    assert svc._neo4j._driver is None

    await svc.initialize()

    assert svc._mode == "memory"
    assert svc._neo4j._driver is None

    # Ensure no credentials leaked in logs or exceptions
    for record in caplog.records:
        log_text = record.message
        assert "bolt://fake:7687" not in log_text
        assert "pass" not in log_text.lower()

    await svc.close()


# ---------------------------------------------------------------------------
# NEW: actual Neo4j serialization using fake nodes, relationships and paths
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_neo4j_serialization(fake_neo4j_module, monkeypatch, clear_neo4j_env):
    monkeypatch.setenv("NEO4J_ENABLED", "true")
    monkeypatch.setenv("NEO4J_URI", "bolt://fake:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "user")
    monkeypatch.setenv("NEO4J_PASSWORD", "pass")

    svc = PerceptionGraphService()
    await svc.initialize()
    assert svc._mode == "neo4j"

    v_node = FakeNode("v-A", ["SentinelPerception", "Vehicle"], label="Vehicle A")
    obs_node = FakeNode("obs-1", ["SentinelPerception", "Observation"], label="Observation obs-1", type="pothole", timestamp=1000.0)
    h_node = FakeNode("hz-1", ["SentinelPerception", "Hazard"], label="Pothole", type="pothole", sourceCount=2, confidence=80)
    r_node = FakeNode("seg-gst", ["SentinelPerception", "RoadSegment"], name="GST Road")
    w_node = FakeNode("wrn-1", ["SentinelPerception", "Warning"], text="Pothole ahead", language="en", timestamp=2000.0)
    rv_node = FakeNode("v-C", ["SentinelPerception", "Vehicle"], label="Vehicle C")

    obs_rel = FakeRelationship("OBSERVED", v_node, obs_node, scenario_id=SCENARIO_ID)
    sup_rel = FakeRelationship("SUPPORTS", obs_node, h_node, scenario_id=SCENARIO_ID)
    onr_rel = FakeRelationship("ON_ROAD", h_node, r_node, scenario_id=SCENARIO_ID)
    app_rel = FakeRelationship("APPROACHING", v_node, r_node, scenario_id=SCENARIO_ID)
    tw_rel = FakeRelationship("TRIGGERED_WARNING", h_node, w_node, scenario_id=SCENARIO_ID)
    dt_rel = FakeRelationship("DELIVERED_TO", w_node, rv_node, scenario_id=SCENARIO_ID)
    app2_rel = FakeRelationship("APPROACHING", rv_node, r_node, scenario_id=SCENARIO_ID)

    record = FakeRecord({
        "nodes": [v_node, obs_node, h_node, r_node, w_node, rv_node],
        "edges": [obs_rel, sup_rel, onr_rel, app_rel, tw_rel, dt_rel, app2_rel],
    })

    fake_driver = svc._neo4j._driver
    fake_driver.set_next_result([record])
    graph = await svc._neo4j.build_graph(hazard_id="hz-1")

    assert graph["mode"] == "neo4j"
    assert graph["focusHazardId"] == "hz-1"

    node_ids = {n["id"] for n in graph["nodes"]}
    assert node_ids == {"v-A", "obs-1", "hz-1", "seg-gst", "wrn-1", "v-C"}

    edge_ids = {e["id"] for e in graph["edges"]}
    assert edge_ids == {
        "OBSERVED:v-A:obs-1",
        "SUPPORTS:obs-1:hz-1",
        "ON_ROAD:hz-1:seg-gst",
        "APPROACHING:v-A:seg-gst",
        "TRIGGERED_WARNING:hz-1:wrn-1",
        "DELIVERED_TO:wrn-1:v-C",
        "APPROACHING:v-C:seg-gst",
    }

    hazard = next(n for n in graph["nodes"] if n["id"] == "hz-1")
    assert hazard["properties"]["sourceCount"] == 2
    assert hazard["properties"]["confidence"] == 80

    assert graph["summary"]["focus"]["sourceCount"] == 2
    assert graph["summary"]["focus"]["confidence"] == 80
    assert graph["summary"]["focus"]["warningCount"] == 1

    # Verify nodes and edges are sorted deterministically
    node_types = [n["type"] for n in graph["nodes"]]
    assert node_types == sorted(node_types, key=lambda t: ({
        "Hazard": 0, "Vehicle": 1, "Observation": 2, "RoadSegment": 3, "Warning": 4
    }.get(t, 99), t))

    await svc.close()


# ---------------------------------------------------------------------------
# NEW: no real Neo4j connection
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_no_real_neo4j_connection(clear_neo4j_env):
    svc = PerceptionGraphService()
    await svc.initialize()
    assert svc._mode == "memory"
    assert svc._neo4j._driver is None
    await svc.close()


# ---------------------------------------------------------------------------
# NEW: response copies are independent (no mutation through returned dicts)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_returned_copies_are_independent():
    svc = await _make_service()
    await svc.record_observation("obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0)
    graph1 = await svc.build_graph(hazard_id="hz-1")

    # Mutate returned graph
    graph1["nodes"][0]["properties"]["tampered"] = True
    graph1["edges"][0]["properties"]["tampered"] = True

    graph2 = await svc.build_graph(hazard_id="hz-1")
    for node in graph2["nodes"]:
        assert "tampered" not in node["properties"]
    for edge in graph2["edges"]:
        assert "tampered" not in edge["properties"]

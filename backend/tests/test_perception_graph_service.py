"""Tests for PerceptionGraphService.

Run targeted:
    pytest backend/tests/test_perception_graph_service.py -q

Run full regression:
    pytest backend/tests -q
"""

import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import pytest
from services.perception_graph_service import PerceptionGraphService, SCENARIO_ID


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
    assert {"OBSERVED", "SUPPORTS", "ON_ROAD", "APPROACHING", "TRIGGERED_WARNING", "DELIVERED_TO"}.issubset(
        edge_types
    )


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
    await svc.record_observation(
        "obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
    )
    await svc.record_observation(
        "obs-2", "v-B", "B", "hz-2", "debris", "Debris", "seg-side", "Side", 2000.0
    )
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
    await svc.record_observation(
        "obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
    )
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
    # Valid
    await svc.build_graph(limit=1)
    await svc.build_graph(limit=100)
    # Invalid
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
    await svc.record_observation(
        "obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
    )
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
    await svc.record_observation(
        "obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
    )
    await svc.reset_demo_data()
    async with svc._memory._lock:
        assert "other-node" in svc._memory._nodes
    graph = await svc.build_graph()
    assert graph["summary"]["nodeCount"] == 0


# ---------------------------------------------------------------------------
# 13. No credential leakage
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_no_credential_leakage(monkeypatch):
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

    # Also verify that errors do not leak credentials
    try:
        # Force a neo4j method failure by temporarily switching to neo4j mode
        # without a real driver. _execute will catch and fall back.
        svc._mode = "neo4j"
        await svc.build_graph()
    except Exception as exc:
        exc_str = str(exc)
        assert "secret-host" not in exc_str
        assert "secret-user" not in exc_str
        assert "secret-pass" not in exc_str

    # Restore
    svc._mode = "memory"


# ---------------------------------------------------------------------------
# 14. Memory and Neo4j normalization use the same response schema
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_memory_neo4j_schema_parity():
    svc = await _make_service()
    await svc.record_observation(
        "obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
    )
    memory_graph = await svc.build_graph(hazard_id="hz-1")

    # Monkeypatch neo4j backend to return the memory graph without touching a driver
    async def mock_build_graph(**kwargs):
        return dict(memory_graph)

    original = svc._neo4j.build_graph
    svc._neo4j.build_graph = mock_build_graph
    svc._mode = "neo4j"

    neo4j_graph = await svc.build_graph(hazard_id="hz-1")

    svc._neo4j.build_graph = original
    svc._mode = "memory"

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

    # Node schema
    for node in neo4j_graph["nodes"]:
        assert set(node.keys()) == {"id", "type", "label", "scenarioId", "properties"}
    for node in memory_graph["nodes"]:
        assert set(node.keys()) == {"id", "type", "label", "scenarioId", "properties"}

    # Edge schema
    for edge in neo4j_graph["edges"]:
        assert set(edge.keys()) == {"id", "type", "source", "target", "scenarioId", "properties"}
    for edge in memory_graph["edges"]:
        assert set(edge.keys()) == {"id", "type", "source", "target", "scenarioId", "properties"}

    # Summary schema
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

    # Timeline item schema
    for item in neo4j_graph["timeline"]:
        assert set(item.keys()) == {"eventId", "timestamp", "type", "description"}
    for item in memory_graph["timeline"]:
        assert set(item.keys()) == {"eventId", "timestamp", "type", "description"}


# ---------------------------------------------------------------------------
# 15. Repeated initialization and closing are safe
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_repeated_init_close():
    svc = PerceptionGraphService()
    # Repeated initialization must be safe regardless of whether Neo4j is available
    await svc.initialize()
    await svc.initialize()
    # close() must always reset to memory
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

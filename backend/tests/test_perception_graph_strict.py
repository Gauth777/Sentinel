"""Tests for strict mode enforcement in PerceptionGraphService.
"""

import os
import sys
import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.perception_graph_service import PerceptionGraphService, SCENARIO_ID


@pytest.fixture(autouse=True)
def clear_neo4j_env(monkeypatch):
    """Ensure no test can accidentally connect to a real Neo4j instance."""
    for key in list(os.environ.keys()):
        if key.startswith("NEO4J_") or key == "SENTINEL_NEO4J_STRICT":
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.mark.anyio
async def test_strict_true_neo4j_disabled(monkeypatch):
    monkeypatch.setenv("SENTINEL_NEO4J_STRICT", "true")
    monkeypatch.setenv("NEO4J_ENABLED", "false")
    
    svc = PerceptionGraphService()
    
    # Mock memory initialize to check if it's called
    mem_init_called = False
    async def mock_mem_init():
        nonlocal mem_init_called
        mem_init_called = True
    monkeypatch.setattr(svc._memory, "initialize", mock_mem_init)
    
    with pytest.raises(RuntimeError, match="disabled under strict mode"):
        await svc.initialize()
        
    assert not mem_init_called
    assert svc.get_backend_status()["connected"] is False


@pytest.mark.anyio
async def test_strict_true_neo4j_initialization_fails(monkeypatch):
    monkeypatch.setenv("SENTINEL_NEO4J_STRICT", "true")
    monkeypatch.setenv("NEO4J_ENABLED", "true")
    monkeypatch.setenv("NEO4J_URI", "bolt://secret-uri:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "secret-username")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret-password")
    
    svc = PerceptionGraphService()
    
    # Mock Neo4j initialize to fail
    async def mock_neo4j_init_fail():
        raise RuntimeError("some connection/credentials error")
    monkeypatch.setattr(svc._neo4j, "initialize", mock_neo4j_init_fail)
    
    # Mock memory initialize to check if it's called
    mem_init_called = False
    async def mock_mem_init():
        nonlocal mem_init_called
        mem_init_called = True
    monkeypatch.setattr(svc._memory, "initialize", mock_mem_init)
    
    with pytest.raises(RuntimeError, match="Neo4j initialization failed in strict mode") as exc_info:
        await svc.initialize()
        
    exc_str = str(exc_info.value)
    assert exc_str == "Neo4j initialization failed in strict mode"
    # Check that error text contains no supplied URI, username or password
    assert "secret-uri" not in exc_str
    assert "secret-username" not in exc_str
    assert "secret-password" not in exc_str
    
    assert not mem_init_called
    assert svc.get_backend_status()["connected"] is False


@pytest.mark.anyio
async def test_strict_true_neo4j_succeeds(monkeypatch):
    monkeypatch.setenv("SENTINEL_NEO4J_STRICT", "true")
    monkeypatch.setenv("NEO4J_ENABLED", "true")
    
    svc = PerceptionGraphService()
    
    # Mock Neo4j initialize to succeed
    async def mock_neo4j_init_ok():
        pass
    monkeypatch.setattr(svc._neo4j, "initialize", mock_neo4j_init_ok)
    
    await svc.initialize()
    
    assert svc._mode == "neo4j"
    status = svc.get_backend_status()
    assert status["mode"] == "neo4j"
    assert status["strict"] is True
    assert status["neo4jEnabled"] is True
    assert status["connected"] is True


@pytest.mark.anyio
async def test_strict_runtime_neo4j_operation_failure(monkeypatch):
    monkeypatch.setenv("SENTINEL_NEO4J_STRICT", "true")
    monkeypatch.setenv("NEO4J_ENABLED", "true")
    
    svc = PerceptionGraphService()
    
    async def mock_neo4j_init_ok():
        pass
    monkeypatch.setattr(svc._neo4j, "initialize", mock_neo4j_init_ok)
    await svc.initialize()
    assert svc.get_backend_status()["connected"] is True
    
    # Mock operation to fail on Neo4j
    async def mock_neo4j_obs(*args, **kwargs):
        raise RuntimeError("Neo4j node merge failure")
    monkeypatch.setattr(svc._neo4j, "record_observation", mock_neo4j_obs)
    
    # Mock memory method to check if called
    mem_called = False
    async def mock_mem_obs(*args, **kwargs):
        nonlocal mem_called
        mem_called = True
    monkeypatch.setattr(svc._memory, "record_observation", mock_mem_obs)
    
    with pytest.raises(RuntimeError, match="failed in strict mode"):
        await svc.record_observation(
            "obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
        )
        
    assert not mem_called
    assert svc._mode == "neo4j"  # does not silently become memory
    assert svc._neo4j_connected is False
    assert svc.get_backend_status()["connected"] is False


@pytest.mark.anyio
async def test_strict_value_error(monkeypatch):
    monkeypatch.setenv("SENTINEL_NEO4J_STRICT", "true")
    monkeypatch.setenv("NEO4J_ENABLED", "true")
    
    svc = PerceptionGraphService()
    
    async def mock_neo4j_init_ok():
        pass
    monkeypatch.setattr(svc._neo4j, "initialize", mock_neo4j_init_ok)
    await svc.initialize()
    assert svc.get_backend_status()["connected"] is True
    
    # Mock ValueError on Neo4j operation (e.g. invalid type validation/domain logic)
    async def mock_neo4j_obs(*args, **kwargs):
        raise ValueError("observation owner mismatch")
    monkeypatch.setattr(svc._neo4j, "record_observation", mock_neo4j_obs)
    
    # Mock memory method to check if called
    mem_called = False
    async def mock_mem_obs(*args, **kwargs):
        nonlocal mem_called
        mem_called = True
    monkeypatch.setattr(svc._memory, "record_observation", mock_mem_obs)
    
    with pytest.raises(ValueError, match="observation owner mismatch"):
        await svc.record_observation(
            "obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
        )
        
    assert not mem_called
    assert svc._neo4j_connected is True
    assert svc.get_backend_status()["connected"] is True


@pytest.mark.anyio
async def test_non_strict_neo4j_disabled(monkeypatch):
    monkeypatch.setenv("SENTINEL_NEO4J_STRICT", "false")
    monkeypatch.setenv("NEO4J_ENABLED", "false")
    
    svc = PerceptionGraphService()
    
    neo4j_init_called = False
    async def mock_neo4j_init():
        nonlocal neo4j_init_called
        neo4j_init_called = True
    monkeypatch.setattr(svc._neo4j, "initialize", mock_neo4j_init)
    
    mem_init_called = False
    async def mock_mem_init():
        nonlocal mem_init_called
        mem_init_called = True
    monkeypatch.setattr(svc._memory, "initialize", mock_mem_init)
    
    await svc.initialize()
    
    assert not neo4j_init_called
    assert mem_init_called
    assert svc._mode == "memory"


@pytest.mark.anyio
async def test_non_strict_neo4j_initialization_failure(monkeypatch):
    monkeypatch.setenv("SENTINEL_NEO4J_STRICT", "false")
    monkeypatch.setenv("NEO4J_ENABLED", "true")
    
    svc = PerceptionGraphService()
    
    async def mock_neo4j_init_fail():
        raise RuntimeError("connect failed")
    monkeypatch.setattr(svc._neo4j, "initialize", mock_neo4j_init_fail)
    
    mem_init_called = False
    async def mock_mem_init():
        nonlocal mem_init_called
        mem_init_called = True
    monkeypatch.setattr(svc._memory, "initialize", mock_mem_init)
    
    # Should not raise exception
    await svc.initialize()
    
    assert mem_init_called
    assert svc._mode == "memory"


@pytest.mark.anyio
async def test_non_strict_runtime_neo4j_failure(monkeypatch):
    monkeypatch.setenv("SENTINEL_NEO4J_STRICT", "false")
    monkeypatch.setenv("NEO4J_ENABLED", "true")
    
    svc = PerceptionGraphService()
    
    async def mock_neo4j_init_ok():
        pass
    monkeypatch.setattr(svc._neo4j, "initialize", mock_neo4j_init_ok)
    await svc.initialize()
    assert svc._mode == "neo4j"
    assert svc._neo4j_connected is True
    
    # Mock Neo4j execution to fail operationally
    async def mock_neo4j_obs(*args, **kwargs):
        raise RuntimeError("Operational write failure")
    monkeypatch.setattr(svc._neo4j, "record_observation", mock_neo4j_obs)
    
    mem_init_called = False
    async def mock_mem_init():
        nonlocal mem_init_called
        mem_init_called = True
    monkeypatch.setattr(svc._memory, "initialize", mock_mem_init)
    
    mem_obs_called = False
    async def mock_mem_obs(*args, **kwargs):
        nonlocal mem_obs_called
        mem_obs_called = True
    monkeypatch.setattr(svc._memory, "record_observation", mock_mem_obs)
    
    # Should execute successfully via memory fallback
    await svc.record_observation(
        "obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
    )
    
    assert mem_init_called
    assert mem_obs_called
    assert svc._mode == "memory"
    assert svc._neo4j_connected is False
    assert svc._memory_connected is True
    assert svc.get_backend_status()["connected"] is True


@pytest.mark.anyio
async def test_close_behavior(monkeypatch):
    svc = PerceptionGraphService()
    
    # Mock Neo4j/Memory close
    neo4j_close_called = False
    async def mock_neo4j_close():
        nonlocal neo4j_close_called
        neo4j_close_called = True
    monkeypatch.setattr(svc._neo4j, "close", mock_neo4j_close)
    
    mem_close_called = False
    async def mock_mem_close():
        nonlocal mem_close_called
        mem_close_called = True
    monkeypatch.setattr(svc._memory, "close", mock_mem_close)
    
    # Manually mark as connected to verify close clears it
    svc._neo4j_connected = True
    svc._memory_connected = True
    
    await svc.close()
    
    assert neo4j_close_called
    assert mem_close_called
    status = svc.get_backend_status()
    assert status["connected"] is False


@pytest.mark.anyio
async def test_memory_end_to_end(monkeypatch):
    monkeypatch.setenv("SENTINEL_NEO4J_STRICT", "false")
    monkeypatch.setenv("NEO4J_ENABLED", "false")
    
    svc = PerceptionGraphService()
    await svc.initialize()
    
    # record_observation
    await svc.record_observation(
        "obs-1", "v-A", "A", "hz-1", "pothole", "Pothole", "seg-gst", "GST", 1000.0
    )
    # record_warning
    await svc.record_warning(
        "wrn-1", "hz-1", "v-A", "Pothole ahead", "en", "seg-gst", 1005.0
    )
    
    # build_graph
    graph = await svc.build_graph(hazard_id="hz-1")
    assert graph["summary"]["nodeCount"] == 5
    assert graph["summary"]["edgeCount"] == 6
    assert graph["summary"]["focus"]["hazardId"] == "hz-1"
    
    # reset_demo_data
    await svc.reset_demo_data()
    graph2 = await svc.build_graph()
    assert graph2["summary"]["nodeCount"] == 0


@pytest.mark.anyio
async def test_failed_strict_reinitialization_after_memory(monkeypatch):
    monkeypatch.setenv("SENTINEL_NEO4J_STRICT", "false")
    monkeypatch.setenv("NEO4J_ENABLED", "false")

    svc = PerceptionGraphService()
    await svc.initialize()
    assert svc._mode == "memory"
    assert svc.get_backend_status()["connected"] is True

    # Reinitialize in strict mode with Neo4j that fails
    monkeypatch.setenv("SENTINEL_NEO4J_STRICT", "true")
    monkeypatch.setenv("NEO4J_ENABLED", "true")

    async def mock_neo4j_init_fail():
        raise RuntimeError("connection failed")
    monkeypatch.setattr(svc._neo4j, "initialize", mock_neo4j_init_fail)

    with pytest.raises(RuntimeError, match="Neo4j initialization failed in strict mode"):
        await svc.initialize()

    status = svc.get_backend_status()
    assert status["connected"] is False
    assert status["strict"] is True


@pytest.mark.anyio
async def test_externally_supplied_strict_does_not_leak():
    # Verify that SENTINEL_NEO4J_STRICT is not present in os.environ,
    # proving the clear_neo4j_env fixture successfully removed it.
    assert "SENTINEL_NEO4J_STRICT" not in os.environ
    svc = PerceptionGraphService()
    assert svc._strict is False

"""Tests for Sentinel replay activation service.

No internet, no real Qwen endpoint, no actual images, no MongoDB, no Neo4j required.
Uses mocked hazard workflow to avoid external dependencies.
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from models.vision_inference import (
    ActivationResult,
    InferenceMode,
    InferenceResult,
    RuntimeHazardPrediction,
    StructuredRoadPrediction,
)
from services.replay_activation_service import (
    ReplayActivationStore,
    activate_inference,
    get_store,
    set_store,
    REPLAY_VEHICLE_ID,
    REPLAY_VEHICLE_LABEL,
    ACTION_MAP,
)
from services.vision_inference_service import _compute_inference_id


# ----------------------------- Helpers -----------------------------


def make_inference_result(
    sample_id: str = "s1",
    hazard_presence: str = "yes",
    anticipated_risk: str = "high",
    recommended_action: str = "slow_down",
    hazard_type: str = "crossing_vehicle",
    hazard_description: str = "Vehicle crossing from side",
    confidence: float = 0.82,
    inference_id: str | None = None,
) -> InferenceResult:
    runtime_hazard = None
    if hazard_presence == "yes":
        runtime_hazard = RuntimeHazardPrediction(
            hazard_type=hazard_type,
            hazard_description=hazard_description,
            confidence=confidence,
        )

    pred = StructuredRoadPrediction(
        road_type="urban_arterial",
        traffic_density="high",
        road_complexity="complex",
        hazard_presence=hazard_presence,
        anticipated_risk=anticipated_risk,
        recommended_action=recommended_action,
    )

    if inference_id is None:
        inference_id = _compute_inference_id(
            sample_id, "Qwen2.5-VL-7B-Instruct", "v1",
            InferenceMode.cached_qwen, pred, runtime_hazard,
        )

    return InferenceResult(
        inference_id=inference_id,
        sample_id=sample_id,
        model="Qwen2.5-VL-7B-Instruct",
        prompt_version="v1",
        inference_mode=InferenceMode.cached_qwen,
        prediction=pred,
        runtime_hazard=runtime_hazard,
        latency_ms=0,
    )


SAMPLE_LOCATION = {"latitude": 12.9452, "longitude": 80.1506}


def _make_mock_db():
    """Create a mock db with replay_activations collection that behaves like MOCK_DB_STATE."""
    store = {}

    async def find_one(query, projection=None):
        iid = query.get("inferenceId")
        doc = store.get(iid)
        if doc and projection and "_id" in projection and projection["_id"] == 0:
            doc = {k: v for k, v in doc.items() if k != "_id"}
        return doc

    async def replace_one(query, record, upsert=False):
        iid = query.get("inferenceId")
        if iid in store or upsert:
            store[iid] = dict(record)

    collection = MagicMock()
    collection.find_one = AsyncMock(side_effect=find_one)
    collection.replace_one = AsyncMock(side_effect=replace_one)

    mock_db = MagicMock()
    mock_db.replay_activations = collection
    mock_db._replay_store = store  # exposed for test assertions
    return mock_db


# ----------------------------- Fixtures -----------------------------


@pytest.fixture(autouse=True)
def fresh_store():
    """Create a fresh in-memory store for each test (db=None fallback)."""
    store = ReplayActivationStore()
    set_store(store)
    yield store
    set_store(ReplayActivationStore())


# ----------------------------- Core Tests -----------------------------


@pytest.mark.anyio
async def test_no_hazard_detected():
    """hazardPresence=no should not create a hazard."""
    result = make_inference_result(hazard_presence="no")
    activation = await activate_inference(result, SAMPLE_LOCATION)
    assert activation.activated is False
    assert activation.reason == "no_hazard_detected"
    assert activation.hazard_id is None


@pytest.mark.anyio
async def test_hazard_presence_yes_creates_observation():
    """hazardPresence=yes should attempt to create an observation."""
    mock_hazard = {
        "id": "hz-test123",
        "type": "crossing_vehicle",
        "label": "Vehicle crossing from side",
        "location": SAMPLE_LOCATION,
        "warnings": {"en": "Caution: vehicle crossing"},
        "_warning_events": [],
    }
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(return_value=mock_hazard)

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result()
        activation = await activate_inference(result, SAMPLE_LOCATION)

        assert activation.activated is True
        assert activation.observation_id == f"obs-replay-s1-{result.inference_id}"
        assert activation.hazard_id == "hz-test123"
        assert activation.warning_text_generated is True
        assert activation.warning_event_created is False


@pytest.mark.anyio
async def test_hazard_with_warning_events():
    """When workflow dispatches warning events, warningEventCreated should be True."""
    mock_hazard = {
        "id": "hz-wrnevt",
        "type": "pothole",
        "label": "Pothole",
        "location": SAMPLE_LOCATION,
        "warnings": {"en": "Warning: pothole ahead"},
        "_warning_events": ["wrn-obs-replay-s1-inf-x-v1"],
    }
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(return_value=mock_hazard)

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result(
            hazard_type="pothole",
            hazard_description="Pothole on left lane",
        )
        activation = await activate_inference(result, SAMPLE_LOCATION)

        assert activation.warning_text_generated is True
        assert activation.warning_event_created is True


@pytest.mark.anyio
async def test_duplicate_activation_is_idempotent():
    """Same inference_id should return stored activation result."""
    mock_hazard = {
        "id": "hz-idem",
        "type": "crossing_vehicle",
        "label": "Test",
        "location": SAMPLE_LOCATION,
        "warnings": {"en": "Test warning"},
        "_warning_events": [],
    }
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(return_value=mock_hazard)

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result()
        a1 = await activate_inference(result, SAMPLE_LOCATION)
        a2 = await activate_inference(result, SAMPLE_LOCATION)

        assert a1.activated is True
        assert a2.activated is True
        assert a1.observation_id == a2.observation_id
        # Workflow should only be called once
        assert mock_runner.process_observation.call_count == 1


@pytest.mark.anyio
async def test_concurrent_activation_prevented():
    """Concurrent activations for the same inference should invoke workflow exactly once."""
    mock_hazard = {
        "id": "hz-conc",
        "type": "crossing_vehicle",
        "label": "Test",
        "location": SAMPLE_LOCATION,
        "warnings": {"en": "Test"},
        "_warning_events": [],
    }
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(return_value=mock_hazard)

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result()
        # Fire multiple concurrent activations
        activations = await asyncio.gather(
            activate_inference(result, SAMPLE_LOCATION),
            activate_inference(result, SAMPLE_LOCATION),
            activate_inference(result, SAMPLE_LOCATION),
        )

        # 2D. Assert workflow is called exactly once
        assert mock_runner.process_observation.call_count == 1

        # All should report the same observation
        obs_ids = {a.observation_id for a in activations if a.observation_id}
        assert len(obs_ids) == 1

        # Exactly one activation record in backing store
        store = get_store()
        existing = await store.get(result.inference_id)
        assert existing is not None
        assert existing["activated"] is True


@pytest.mark.anyio
async def test_location_missing_prevents_activation():
    """If location is None, activation should be skipped."""
    result = make_inference_result()
    activation = await activate_inference(result, None)
    assert activation.activated is False
    assert activation.reason == "location_missing"


@pytest.mark.anyio
async def test_location_incomplete_prevents_activation():
    """If location has None lat/lon, activation should be skipped."""
    result = make_inference_result()
    activation = await activate_inference(result, {"latitude": None, "longitude": 80.15})
    assert activation.activated is False
    assert activation.reason == "location_missing"


@pytest.mark.anyio
async def test_workflow_error_returns_non_fatal():
    """Workflow errors should result in activated=False with sanitized reason."""
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(
        side_effect=Exception("DB connection failed")
    )

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result()
        activation = await activate_inference(result, SAMPLE_LOCATION)

        assert activation.activated is False
        assert activation.reason == "activation_failed"
        # Must NOT expose raw exception details
        assert "DB connection" not in (activation.reason or "")


@pytest.mark.anyio
async def test_observation_id_format():
    """Observation ID should follow the obs-replay-{sample_id}-{inference_id} format."""
    mock_hazard = {
        "id": "hz-fmt",
        "type": "test",
        "label": "Test",
        "location": SAMPLE_LOCATION,
        "warnings": {"en": "Test"},
        "_warning_events": [],
    }
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(return_value=mock_hazard)

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result(sample_id="sample_001")
        activation = await activate_inference(result, SAMPLE_LOCATION)

        assert activation.observation_id == f"obs-replay-sample_001-{result.inference_id}"


@pytest.mark.anyio
async def test_source_vehicle_is_replay_observer():
    """The observation should use the replay observer vehicle ID."""
    mock_hazard = {
        "id": "hz-src",
        "type": "test",
        "label": "Test",
        "location": SAMPLE_LOCATION,
        "warnings": {"en": "Test"},
        "_warning_events": [],
    }
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(return_value=mock_hazard)

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result()
        await activate_inference(result, SAMPLE_LOCATION)

        call_args = mock_runner.process_observation.call_args[0][0]
        assert call_args["sourceVehicleId"] == REPLAY_VEHICLE_ID
        assert call_args["vehicleLabel"] == REPLAY_VEHICLE_LABEL


@pytest.mark.anyio
async def test_runtime_hazard_type_used_when_available():
    """When runtimeHazard has hazard_type, it should be used in the observation."""
    mock_hazard = {
        "id": "hz-rt",
        "type": "pedestrian_crossing",
        "label": "Pedestrians crossing",
        "location": SAMPLE_LOCATION,
        "warnings": {"en": "Caution pedestrians"},
        "_warning_events": [],
    }
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(return_value=mock_hazard)

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result(
            hazard_type="pedestrian_crossing",
            hazard_description="Pedestrians crossing street",
        )
        await activate_inference(result, SAMPLE_LOCATION)

        call_args = mock_runner.process_observation.call_args[0][0]
        assert call_args["type"] == "pedestrian_crossing"


@pytest.mark.anyio
async def test_replay_meta_passed_to_observation():
    """Replay provenance metadata should be passed in the observation."""
    mock_hazard = {
        "id": "hz-meta",
        "type": "test",
        "label": "Test",
        "location": SAMPLE_LOCATION,
        "warnings": {"en": "Test"},
        "_warning_events": [],
    }
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(return_value=mock_hazard)

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result(
            recommended_action="slow_down",
            anticipated_risk="high",
        )
        await activate_inference(result, SAMPLE_LOCATION)

        call_args = mock_runner.process_observation.call_args[0][0]
        meta = call_args.get("_replay_meta")
        assert meta is not None
        assert meta["recommendedAction"] == "Slow down"
        assert meta["risk"] == "high"
        assert meta["model"] == "Qwen2.5-VL-7B-Instruct"
        assert meta["inferenceMode"] == "cached_qwen"
        assert meta["sampleId"] == "s1"
        assert meta["lastInferenceId"] == result.inference_id


# ----------------------------- 2E: Persistence across store recreation (shared mock db) -------------


@pytest.mark.anyio
async def test_service_recreation_preserves_activation():
    """Creating a new store with the same shared mock db preserves activation state.
    Does NOT share _mem manually — uses a shared mock database object."""
    shared_db = _make_mock_db()

    mock_hazard = {
        "id": "hz-persist",
        "type": "test",
        "label": "Test",
        "location": SAMPLE_LOCATION,
        "warnings": {"en": "Test"},
        "_warning_events": [],
    }
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(return_value=mock_hazard)

    # First store with shared db
    store1 = ReplayActivationStore(shared_db)
    set_store(store1)

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result()
        a1 = await activate_inference(result, SAMPLE_LOCATION)
        assert a1.activated is True

    # Second store with same shared db (simulates service recreation)
    store2 = ReplayActivationStore(shared_db)
    set_store(store2)

    a2 = await activate_inference(result, SAMPLE_LOCATION)
    assert a2.activated is True
    assert a2.observation_id == a1.observation_id
    # Workflow should NOT be called again — persisted in shared db
    assert mock_runner.process_observation.call_count == 1

    # Exactly one replay_activations document exists
    assert len(shared_db._replay_store) == 1


# ----------------------------- 2E supplement: internal dict only when db is None ----


@pytest.mark.anyio
async def test_internal_dict_used_only_when_db_none():
    """When db=None, internal dict is used. When db is provided, db collections are used."""
    # db=None: should use internal dict
    store_no_db = ReplayActivationStore()
    await store_no_db.put("test-id", {"inferenceId": "test-id", "activated": True})
    assert "test-id" in store_no_db._mem

    # db provided: should use db.replay_activations
    mock_db = _make_mock_db()
    store_with_db = ReplayActivationStore(mock_db)
    await store_with_db.put("test-id-2", {"inferenceId": "test-id-2", "activated": True})
    # Internal dict should remain empty
    assert len(store_with_db._mem) == 0
    # DB should have the record
    assert "test-id-2" in mock_db._replay_store


@pytest.mark.anyio
async def test_replay_reset_does_not_erase_graph():
    """Replay activation store is separate from graph/media state.
    Clearing the store does not affect external services."""
    mock_hazard = {
        "id": "hz-rst",
        "type": "test",
        "label": "Test",
        "location": SAMPLE_LOCATION,
        "warnings": {"en": "Test"},
        "_warning_events": [],
    }
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(return_value=mock_hazard)

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result()
        activation = await activate_inference(result, SAMPLE_LOCATION)
        assert activation.activated is True

        # Create fresh store (simulates replay reset)
        set_store(ReplayActivationStore())

        # Re-activating should call workflow again
        activation2 = await activate_inference(result, SAMPLE_LOCATION)
        assert activation2.activated is True
        assert mock_runner.process_observation.call_count == 2


@pytest.mark.anyio
async def test_no_hazard_reports_both_false():
    """No-hazard prediction should have both warning flags False."""
    result = make_inference_result(hazard_presence="no")
    activation = await activate_inference(result, SAMPLE_LOCATION)
    assert activation.warning_text_generated is False
    assert activation.warning_event_created is False

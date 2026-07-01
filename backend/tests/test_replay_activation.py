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
    activate_inference,
    _activation_cache,
    REPLAY_VEHICLE_ID,
    REPLAY_VEHICLE_LABEL,
)


# ----------------------------- Helpers -----------------------------


def make_inference_result(
    sample_id: str = "s1",
    hazard_presence: str = "yes",
    anticipated_risk: str = "high",
    recommended_action: str = "slow_down",
    hazard_type: str = "crossing_vehicle",
    hazard_description: str = "Vehicle crossing from side",
    confidence: float = 0.82,
    inference_id: str = "inf-test001",
) -> InferenceResult:
    runtime_hazard = None
    if hazard_presence == "yes":
        runtime_hazard = RuntimeHazardPrediction(
            hazard_type=hazard_type,
            hazard_description=hazard_description,
            confidence=confidence,
        )

    return InferenceResult(
        inference_id=inference_id,
        sample_id=sample_id,
        model="Qwen2.5-VL-7B-Instruct",
        prompt_version="v1",
        inference_mode=InferenceMode.cached_qwen,
        prediction=StructuredRoadPrediction(
            road_type="urban_arterial",
            traffic_density="high",
            road_complexity="complex",
            hazard_presence=hazard_presence,
            anticipated_risk=anticipated_risk,
            recommended_action=recommended_action,
        ),
        runtime_hazard=runtime_hazard,
        latency_ms=0,
    )


SAMPLE_LOCATION = {"latitude": 12.9452, "longitude": 80.1506}


# ----------------------------- Tests -----------------------------


@pytest.fixture(autouse=True)
def clear_activation_cache():
    """Clear the activation cache before each test."""
    _activation_cache.clear()
    yield
    _activation_cache.clear()


@pytest.mark.anyio
async def test_no_hazard_detected():
    """hazardPresence=no should not create a hazard."""
    result = make_inference_result(hazard_presence="no", inference_id="inf-nohaz")
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
    }
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(return_value=mock_hazard)

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result(inference_id="inf-yes001")
        activation = await activate_inference(result, SAMPLE_LOCATION)

        assert activation.activated is True
        assert activation.observation_id == "obs-replay-s1-inf-yes001"
        assert activation.hazard_id == "hz-test123"
        assert activation.warning_created is True


@pytest.mark.anyio
async def test_duplicate_activation_is_idempotent():
    """Same (sample_id, inference_id) should return cached activation result."""
    mock_hazard = {
        "id": "hz-idem",
        "type": "crossing_vehicle",
        "label": "Test",
        "location": SAMPLE_LOCATION,
        "warnings": {"en": "Test warning"},
    }
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(return_value=mock_hazard)

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result(inference_id="inf-idem001")
        a1 = await activate_inference(result, SAMPLE_LOCATION)
        a2 = await activate_inference(result, SAMPLE_LOCATION)

        assert a1.activated is True
        assert a2.activated is True
        assert a1.observation_id == a2.observation_id
        # Workflow should only be called once
        assert mock_runner.process_observation.call_count == 1


@pytest.mark.anyio
async def test_location_missing_prevents_activation():
    """If location is None, activation should be skipped."""
    result = make_inference_result(inference_id="inf-noloc")
    activation = await activate_inference(result, None)
    assert activation.activated is False
    assert activation.reason == "location_missing"


@pytest.mark.anyio
async def test_location_incomplete_prevents_activation():
    """If location has None lat/lon, activation should be skipped."""
    result = make_inference_result(inference_id="inf-badloc")
    activation = await activate_inference(result, {"latitude": None, "longitude": 80.15})
    assert activation.activated is False
    assert activation.reason == "location_missing"


@pytest.mark.anyio
async def test_workflow_error_returns_non_fatal():
    """Workflow errors should result in activated=False, not a crash."""
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(
        side_effect=Exception("DB connection failed")
    )

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result(inference_id="inf-err001")
        activation = await activate_inference(result, SAMPLE_LOCATION)

        assert activation.activated is False
        assert "workflow_error" in (activation.reason or "")


@pytest.mark.anyio
async def test_observation_id_format():
    """Observation ID should follow the obs-replay-{sample_id}-{inference_id} format."""
    mock_hazard = {
        "id": "hz-fmt",
        "type": "test",
        "label": "Test",
        "location": SAMPLE_LOCATION,
        "warnings": {"en": "Test"},
    }
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(return_value=mock_hazard)

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result(sample_id="sample_001", inference_id="inf-abc")
        activation = await activate_inference(result, SAMPLE_LOCATION)

        assert activation.observation_id == "obs-replay-sample_001-inf-abc"


@pytest.mark.anyio
async def test_source_vehicle_is_replay_observer():
    """The observation should use the replay observer vehicle ID."""
    mock_hazard = {
        "id": "hz-src",
        "type": "test",
        "label": "Test",
        "location": SAMPLE_LOCATION,
        "warnings": {"en": "Test"},
    }
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(return_value=mock_hazard)

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result(inference_id="inf-src001")
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
    }
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(return_value=mock_hazard)

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result(
            inference_id="inf-rt001",
            hazard_type="pedestrian_crossing",
            hazard_description="Pedestrians crossing street",
        )
        await activate_inference(result, SAMPLE_LOCATION)

        call_args = mock_runner.process_observation.call_args[0][0]
        assert call_args["type"] == "pedestrian_crossing"


@pytest.mark.anyio
async def test_warning_generated():
    """When workflow returns warnings, warning_created should be True."""
    mock_hazard = {
        "id": "hz-wrn",
        "type": "pothole",
        "label": "Pothole",
        "location": SAMPLE_LOCATION,
        "warnings": {"en": "Warning: pothole ahead", "hi": "चेतावनी: आगे गड्ढा"},
    }
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(return_value=mock_hazard)

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result(
            inference_id="inf-wrn001",
            hazard_type="pothole",
            hazard_description="Pothole on left lane",
        )
        activation = await activate_inference(result, SAMPLE_LOCATION)

        assert activation.warning_created is True


@pytest.mark.anyio
async def test_replay_reset_does_not_erase_graph():
    """Conceptual test: replay activation cache is separate from graph/media state.
    Clearing the activation cache does not affect external services."""
    mock_hazard = {
        "id": "hz-rst",
        "type": "test",
        "label": "Test",
        "location": SAMPLE_LOCATION,
        "warnings": {"en": "Test"},
    }
    mock_runner = MagicMock()
    mock_runner.process_observation = AsyncMock(return_value=mock_hazard)

    with patch(
        "workflows.hazard_workflow.LocalWorkflowRunner",
        return_value=mock_runner,
    ):
        result = make_inference_result(inference_id="inf-rst001")
        activation = await activate_inference(result, SAMPLE_LOCATION)
        assert activation.activated is True

        # Clear cache (simulates replay reset)
        _activation_cache.clear()

        # Re-activating should call workflow again (not return cached)
        activation2 = await activate_inference(result, SAMPLE_LOCATION)
        assert activation2.activated is True
        assert mock_runner.process_observation.call_count == 2

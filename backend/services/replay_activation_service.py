"""Replay activation service — maps structured predictions to Sentinel observations.

This service bridges the inference output (StructuredRoadPrediction + RuntimeHazardPrediction)
to the existing hazard workflow (LocalWorkflowRunner) so that replay inference results
get recorded as observations, create/update hazards, generate warnings, and update
the provenance graph.

Rules:
  - hazard_presence=no → no hazard created, activated=false
  - hazard_presence=yes → create observation via LocalWorkflowRunner
  - Location required for activation (sample must have GPS coordinates)
  - Idempotent: same (sample_id, inference_id) → same activation result
  - Source vehicle: v-replay-observer / Sentinel Dataset Observer
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from models.vision_inference import (
    ActivationResult,
    InferenceResult,
    RecommendedAction,
)

logger = logging.getLogger(__name__)

REPLAY_VEHICLE_ID = "v-replay-observer"
REPLAY_VEHICLE_LABEL = "Sentinel Dataset Observer"

# Map recommended_action to human-readable action text for the hazard
_ACTION_MAP = {
    "slow_down": "Slow down",
    "maintain_speed": "Maintain speed",
    "increase_attention": "Increase attention",
    "yield": "Yield to traffic",
    "prepare_to_stop": "Prepare to stop",
    "change_lane": "Change lane",
}

# Cache of completed activations keyed by (sample_id, inference_id)
_activation_cache: Dict[str, ActivationResult] = {}


def _cache_key(sample_id: str, inference_id: str) -> str:
    return f"{sample_id}:{inference_id}"


def _derive_hazard_type(result: InferenceResult) -> str:
    """Derive a hazard type from inference result when runtime_hazard is absent."""
    action = result.prediction.recommended_action.value
    risk = result.prediction.anticipated_risk.value

    if action in ("prepare_to_stop", "slow_down") and risk == "high":
        return "road_hazard"
    if action == "change_lane":
        return "lane_obstruction"
    if action == "yield":
        return "traffic_conflict"
    if risk == "high":
        return "high_risk_condition"
    return "general_hazard"


async def activate_inference(
    result: InferenceResult,
    sample_location: Optional[Dict[str, float]],
) -> ActivationResult:
    """Map an inference result to a Sentinel observation via the hazard workflow.

    Args:
        result: Completed InferenceResult with prediction
        sample_location: GPS location dict with latitude/longitude, or None

    Returns:
        ActivationResult with activated=True/False
    """
    cache_k = _cache_key(result.sample_id, result.inference_id)

    # Idempotency check
    if cache_k in _activation_cache:
        logger.info("Returning cached activation for %s", cache_k)
        return _activation_cache[cache_k]

    # No hazard detected → skip activation
    if result.prediction.hazard_presence.value == "no":
        activation = ActivationResult(
            activated=False,
            reason="no_hazard_detected",
        )
        _activation_cache[cache_k] = activation
        return activation

    # Location required for activation
    if not sample_location:
        activation = ActivationResult(
            activated=False,
            reason="location_missing",
        )
        _activation_cache[cache_k] = activation
        return activation

    lat = sample_location.get("latitude")
    lon = sample_location.get("longitude")
    if lat is None or lon is None:
        activation = ActivationResult(
            activated=False,
            reason="location_missing",
        )
        _activation_cache[cache_k] = activation
        return activation

    # Determine hazard type
    if result.runtime_hazard and result.runtime_hazard.hazard_type:
        hazard_type = result.runtime_hazard.hazard_type
    else:
        hazard_type = _derive_hazard_type(result)

    # Build hazard label
    if result.runtime_hazard and result.runtime_hazard.hazard_description:
        hazard_label = result.runtime_hazard.hazard_description
    else:
        hazard_label = hazard_type.replace("_", " ").title()

    # Build observation
    obs_id = f"obs-replay-{result.sample_id}-{result.inference_id}"
    observation = {
        "id": obs_id,
        "type": hazard_type,
        "label": hazard_label,
        "location": {"latitude": lat, "longitude": lon},
        "polygon": None,
        "sourceVehicleId": REPLAY_VEHICLE_ID,
        "vehicleLabel": REPLAY_VEHICLE_LABEL,
    }

    try:
        from workflows.hazard_workflow import LocalWorkflowRunner

        runner = LocalWorkflowRunner()
        hazard_result = await runner.process_observation(observation)

        hazard_id = hazard_result.get("id") if hazard_result else None
        has_warnings = bool(hazard_result and hazard_result.get("warnings"))

        activation = ActivationResult(
            activated=True,
            observation_id=obs_id,
            hazard_id=hazard_id,
            warning_created=has_warnings,
        )
    except Exception as e:
        logger.error("Hazard workflow failed for %s: %s", obs_id, e)
        activation = ActivationResult(
            activated=False,
            reason=f"workflow_error: {e}",
        )

    _activation_cache[cache_k] = activation
    return activation

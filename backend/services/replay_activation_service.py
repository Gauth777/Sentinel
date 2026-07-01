"""Replay activation service — maps structured predictions to Sentinel observations.

This service bridges the inference output (StructuredRoadPrediction + RuntimeHazardPrediction)
to the existing hazard workflow (LocalWorkflowRunner) so that replay inference results
get recorded as observations, create/update hazards, generate warnings, and update
the provenance graph.

Rules:
  - hazard_presence=no → no hazard created, activated=false
  - hazard_presence=yes → create observation via LocalWorkflowRunner
  - Location required for activation (sample must have GPS coordinates)
  - Idempotent: same inference_id → same activation result
  - Source vehicle: v-replay-observer / Sentinel Dataset Observer

Storage:
  - Primary: db.replay_activations (MongoDB when reachable)
  - Fallback: in-memory dict guarded by asyncio.Lock
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from models.vision_inference import (
    ActivationResult,
    InferenceResult,
)

logger = logging.getLogger(__name__)

REPLAY_VEHICLE_ID = "v-replay-observer"
REPLAY_VEHICLE_LABEL = "Sentinel Dataset Observer"

# Map recommended_action to human-readable action text for the hazard
ACTION_MAP = {
    "slow_down": "Slow down",
    "maintain_speed": "Maintain speed",
    "increase_attention": "Increase attention",
    "yield": "Yield to traffic",
    "prepare_to_stop": "Prepare to stop",
    "change_lane": "Change lane",
}


class ReplayActivationStore:
    """Durable activation store with db primary and in-memory fallback.

    Uses the project's db abstraction (Motor or MOCK_DB_STATE) when provided.
    Only falls back to an internal dict when db is actually None (e.g. isolated
    unit tests). Per-inference asyncio.Lock ensures atomicity.
    """

    def __init__(self, db: Any = None) -> None:
        self._db = db
        self._mem: Dict[str, Dict[str, Any]] = {}
        self._mem_lock = asyncio.Lock()
        self._locks: Dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def lock_for(self, inference_id: str) -> asyncio.Lock:
        """Return (or create) a per-inference asyncio.Lock."""
        async with self._locks_guard:
            lock = self._locks.get(inference_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[inference_id] = lock
            return lock

    async def get(self, inference_id: str) -> Optional[Dict[str, Any]]:
        if self._db is not None:
            return await self._db.replay_activations.find_one(
                {"inferenceId": inference_id}, {"_id": 0}
            )
        async with self._mem_lock:
            return self._mem.get(inference_id)

    async def put(self, inference_id: str, record: Dict[str, Any]) -> None:
        if self._db is not None:
            await self._db.replay_activations.replace_one(
                {"inferenceId": inference_id}, record, upsert=True
            )
            return
        async with self._mem_lock:
            self._mem[inference_id] = record


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


def _action_text(action_value: str) -> str:
    """Convert enum value to human-readable action text."""
    return ACTION_MAP.get(action_value, "Exercise caution")


# Module-level store; initialized by server.py lifespan
_store: Optional[ReplayActivationStore] = None


def get_store() -> ReplayActivationStore:
    global _store
    if _store is None:
        _store = ReplayActivationStore()
    return _store


def set_store(store: ReplayActivationStore) -> None:
    global _store
    _store = store


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
    store = get_store()
    lock = await store.lock_for(result.inference_id)

    async with lock:
        # Idempotency check — look up by deterministic inference_id
        existing = await store.get(result.inference_id)
        if existing is not None:
            logger.info("Returning stored activation for inference %s", result.inference_id)
            return ActivationResult(
                activated=existing.get("activated", False),
                reason=existing.get("reason"),
                observation_id=existing.get("observationId"),
                hazard_id=existing.get("hazardId"),
                warning_text_generated=existing.get("warningTextGenerated", False),
                warning_event_created=existing.get("warningEventCreated", False),
            )

        # No hazard detected → skip activation
        if result.prediction.hazard_presence.value == "no":
            activation = ActivationResult(
                activated=False,
                reason="no_hazard_detected",
            )
            await _persist(store, result, activation)
            return activation

        # Location required for activation
        if not sample_location:
            activation = ActivationResult(
                activated=False,
                reason="location_missing",
            )
            await _persist(store, result, activation)
            return activation

        lat = sample_location.get("latitude")
        lon = sample_location.get("longitude")
        if lat is None or lon is None:
            activation = ActivationResult(
                activated=False,
                reason="location_missing",
            )
            await _persist(store, result, activation)
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

        # Recommended action for the hazard
        rec_action = _action_text(result.prediction.recommended_action.value)

        # Build observation with replay provenance metadata
        obs_id = f"obs-replay-{result.sample_id}-{result.inference_id}"
        observation = {
            "id": obs_id,
            "type": hazard_type,
            "label": hazard_label,
            "location": {"latitude": lat, "longitude": lon},
            "polygon": None,
            "sourceVehicleId": REPLAY_VEHICLE_ID,
            "vehicleLabel": REPLAY_VEHICLE_LABEL,
            # Replay provenance metadata (used by workflow for replay obs)
            "_replay_meta": {
                "recommendedAction": rec_action,
                "risk": result.prediction.anticipated_risk.value,
                "model": result.model,
                "inferenceMode": result.inference_mode.value,
                "sampleId": result.sample_id,
                "lastInferenceId": result.inference_id,
                "confidence": (
                    result.runtime_hazard.confidence
                    if result.runtime_hazard and result.runtime_hazard.confidence is not None
                    else None
                ),
            },
        }

        try:
            from workflows.hazard_workflow import LocalWorkflowRunner

            runner = LocalWorkflowRunner()
            hazard_result = await runner.process_observation(observation)

            hazard_id = hazard_result.get("id") if hazard_result else None

            # Determine warning semantics accurately
            has_warning_text = bool(hazard_result and hazard_result.get("warnings"))
            # Check for actual warning events dispatched
            warning_events = hazard_result.get("_warning_events", []) if hazard_result else []
            has_warning_event = len(warning_events) > 0

            activation = ActivationResult(
                activated=True,
                observation_id=obs_id,
                hazard_id=hazard_id,
                warning_text_generated=has_warning_text,
                warning_event_created=has_warning_event,
            )
        except Exception as e:
            logger.error("Hazard workflow failed for %s: %s", obs_id, type(e).__name__)
            activation = ActivationResult(
                activated=False,
                reason="activation_failed",
            )

        await _persist(store, result, activation)
        return activation


async def _persist(
    store: ReplayActivationStore,
    result: InferenceResult,
    activation: ActivationResult,
) -> None:
    """Persist an activation record to the durable store."""
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "inferenceId": result.inference_id,
        "sampleId": result.sample_id,
        "observationId": activation.observation_id,
        "hazardId": activation.hazard_id,
        "activated": activation.activated,
        "reason": activation.reason,
        "warningTextGenerated": activation.warning_text_generated,
        "warningEventCreated": activation.warning_event_created,
        "createdAt": now,
        "updatedAt": now,
    }
    try:
        await store.put(result.inference_id, record)
    except Exception as e:
        logger.warning("Failed to persist activation record: %s", type(e).__name__)

"""Structured inference models for Sentinel Qwen road perception.

These models define the schema for structured road predictions, runtime
hazard predictions, and complete inference results. They enforce strict
enum validation — no unrecognised labels are accepted.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _to_camel(snake: str) -> str:
    parts = snake.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


# --------------- Enums (reuse values from demo_replay) ---------------


class RoadType(str, Enum):
    urban_arterial = "urban_arterial"
    residential = "residential"
    highway = "highway"
    junction = "junction"


class TrafficDensity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class RoadComplexity(str, Enum):
    simple = "simple"
    moderate = "moderate"
    complex = "complex"


class HazardPresence(str, Enum):
    yes = "yes"
    no = "no"


class AnticipatedRisk(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class RecommendedAction(str, Enum):
    slow_down = "slow_down"
    maintain_speed = "maintain_speed"
    increase_attention = "increase_attention"
    yield_ = "yield"
    prepare_to_stop = "prepare_to_stop"
    change_lane = "change_lane"


class InferenceMode(str, Enum):
    live_qwen = "live_qwen"
    cached_qwen = "cached_qwen"


# --------------- Core prediction models ---------------


class StructuredRoadPrediction(BaseModel):
    """Six-label structured road prediction from Qwen VLM."""

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=_to_camel,
        extra="forbid",
    )

    road_type: RoadType
    traffic_density: TrafficDensity
    road_complexity: RoadComplexity
    hazard_presence: HazardPresence
    anticipated_risk: AnticipatedRisk
    recommended_action: RecommendedAction


class RuntimeHazardPrediction(BaseModel):
    """Optional runtime hazard detail from Qwen VLM."""

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=_to_camel,
        extra="forbid",
    )

    hazard_type: str = Field(min_length=1)
    hazard_description: str = Field(min_length=1)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    warning_text: Optional[str] = None


class InferenceResult(BaseModel):
    """Complete inference result for one replay sample.

    inference_id should be computed deterministically via _compute_inference_id().
    """

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=_to_camel,
    )

    inference_id: str
    sample_id: str
    model: str
    prompt_version: str
    inference_mode: InferenceMode
    prediction: StructuredRoadPrediction
    runtime_hazard: Optional[RuntimeHazardPrediction] = None
    latency_ms: float
    raw_response: Optional[str] = Field(default=None, exclude=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# --------------- Cached prediction file schema ---------------

class CachedPredictionValidationError(ValueError):
    """Raised when a cached prediction file fails strict validation."""
    pass


class CachedPredictionFile(BaseModel):
    """Schema for cached_prediction.json files in the demo scenarios directory."""

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=_to_camel,
        extra="forbid",
    )

    sample_id: str = Field(alias="sampleId")
    model: str
    prompt_version: str = Field(alias="promptVersion")
    generated_at: str = Field(alias="generatedAt")
    prediction: StructuredRoadPrediction
    runtime_hazard: Optional[RuntimeHazardPrediction] = Field(
        default=None, alias="runtimeHazard"
    )
    raw_response: Optional[str] = Field(default=None, alias="rawResponse")
    validated: Literal[True]


# --------------- Activation result ---------------

class ActivationResult(BaseModel):
    """Result of mapping an inference to Sentinel hazard workflow."""

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=_to_camel,
    )

    activated: bool
    reason: Optional[str] = None
    observation_id: Optional[str] = None
    hazard_id: Optional[str] = None
    warning_text_generated: bool = False
    warning_event_created: bool = False


class ActivationPublicResponse(BaseModel):
    """Subset of activation result safe for public API output."""

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=_to_camel,
    )

    activated: bool
    reason: Optional[str] = None
    observation_id: Optional[str] = None
    hazard_id: Optional[str] = None
    warning_text_generated: bool = False
    warning_event_created: bool = False
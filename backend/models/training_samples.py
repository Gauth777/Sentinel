"""Canonical training-sample models for Sentinel VLM dataset engine.

Schema version: sentinel.training.v1
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Mapping, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


# ======================= Canonical enums =======================
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


class DatasetStatus(str, Enum):
    pending = "pending"
    verified = "verified"
    rejected = "rejected"


class FeedbackStatus(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    corrected = "corrected"
    rejected = "rejected"


class TelemetrySource(str, Enum):
    demo = "demo"
    live = "live"
    imported = "imported"


class MediaType(str, Enum):
    image = "image"
    video = "video"


class StorageMode(str, Enum):
    demo_uri = "demo_uri"
    local_uri = "local_uri"
    remote_uri = "remote_uri"
    managed_upload = "managed_upload"


class InferenceMode(str, Enum):
    demo = "demo"
    remote = "remote"
    local = "local"
    imported = "imported"


class ProvenanceSource(str, Enum):
    demo = "demo"
    live = "live"
    imported = "imported"
    api = "api"


class PrivacyStatus(str, Enum):
    not_reviewed = "not_reviewed"
    cleared = "cleared"
    blocked = "blocked"


# ======================= Utility =======================

def _to_camel(snake: str) -> str:
    """Convert snake_case to camelCase."""
    parts = snake.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _normalize_utc(dt: Optional[Union[datetime, str]]) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ======================= Sub-models =======================

class GeoLocation(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class Context(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    location: GeoLocation
    heading_degrees: Optional[float] = Field(default=None, ge=0, lt=360)
    speed_kmh: Optional[float] = Field(default=None, ge=0)
    road_name: Optional[str] = None
    route_direction: Optional[str] = None
    telemetry_source: TelemetrySource = TelemetrySource.demo


class Media(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    type: MediaType = MediaType.image
    uri: str = Field(min_length=1)
    mime_type: Optional[str] = None
    width: Optional[int] = Field(default=None, gt=0)
    height: Optional[int] = Field(default=None, gt=0)
    duration_ms: Optional[int] = Field(default=None, ge=0)
    sha256: Optional[str] = None
    storage_mode: StorageMode = StorageMode.demo_uri

    @field_validator("sha256")
    @classmethod
    def _sha256_hex(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError("sha256 must be a 64-character hexadecimal value")
        return v


class ModelInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    provider: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    prompt_version: Optional[str] = None
    inference_id: Optional[str] = None
    inference_mode: InferenceMode = InferenceMode.demo


class PredictionLabels(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    road_type: RoadType
    traffic_density: TrafficDensity
    road_complexity: RoadComplexity
    hazard_presence: HazardPresence
    anticipated_risk: AnticipatedRisk
    recommended_action: RecommendedAction
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    per_label_confidence: Optional[Mapping[str, float]] = None
    raw_response: Optional[str] = None

    @field_validator("per_label_confidence")
    @classmethod
    def _per_label_confidence(cls, v: Optional[Mapping[str, float]]) -> Optional[Mapping[str, float]]:
        if v is None:
            return v
        allowed = {
            "road_type",
            "traffic_density",
            "road_complexity",
            "hazard_presence",
            "anticipated_risk",
            "recommended_action",
        }
        for key, val in v.items():
            if key not in allowed:
                raise ValueError(f"per_label_confidence key '{key}' is not a canonical label name")
            if not (0 <= val <= 1):
                raise ValueError(f"per_label_confidence value for '{key}' must be between 0 and 1")
        return v


class Provenance(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    source: ProvenanceSource = ProvenanceSource.demo
    graph_hazard_id: Optional[str] = None
    graph_observation_id: Optional[str] = None
    session_id: Optional[str] = None
    device_id: Optional[str] = None


class QualityMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    privacy_status: PrivacyStatus = PrivacyStatus.not_reviewed
    unusable_reason: Optional[str] = None
    notes: Optional[List[str]] = None


class FeedbackEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    status: FeedbackStatus
    corrected_labels: Optional[Dict[str, Any]] = None
    submitted_by: Optional[str] = None
    submitted_at: datetime
    note: Optional[str] = None

    @field_validator("note")
    @classmethod
    def _note_max_length(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 500:
            raise ValueError("note must not exceed 500 characters")
        return v


class FinalVerifiedLabels(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    road_type: RoadType
    traffic_density: TrafficDensity
    road_complexity: RoadComplexity
    hazard_presence: HazardPresence
    anticipated_risk: AnticipatedRisk
    recommended_action: RecommendedAction


# ======================= Create / input models =======================

class TrainingSampleCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    schema_version: str = "sentinel.training.v1"
    sample_id: str = Field(min_length=1)
    observation_id: Optional[str] = None
    hazard_id: Optional[str] = None
    source_vehicle_id: str = Field(min_length=1)
    captured_at: datetime
    context: Context
    media: Media
    model: ModelInfo
    prediction: PredictionLabels
    provenance: Provenance = Provenance()
    quality: Optional[QualityMetadata] = None

    @field_validator("captured_at", mode="before")
    @classmethod
    def _capture_at_utc(cls, v: Any) -> datetime:
        return _normalize_utc(v)


class TrainingFeedbackCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    status: FeedbackStatus
    corrected_labels: Optional[Dict[str, Any]] = None
    submitted_by: Optional[str] = None
    submitted_at: Optional[datetime] = None
    note: Optional[str] = None

    @field_validator("submitted_at", mode="before")
    @classmethod
    def _submitted_at_utc(cls, v: Optional[Any]) -> Optional[datetime]:
        if v is None:
            return None
        return _normalize_utc(v)

    @field_validator("note")
    @classmethod
    def _note_max_length(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 500:
            raise ValueError("note must not exceed 500 characters")
        return v

    @model_validator(mode="after")
    def _check_feedback_rules(self) -> TrainingFeedbackCreate:
        status = self.status
        corrections = self.corrected_labels

        if status == FeedbackStatus.confirmed:
            if corrections and any(v is not None for v in corrections.values()):
                raise ValueError("confirmed feedback must not contain corrected_labels")
        elif status == FeedbackStatus.corrected:
            if not corrections or not any(v is not None for v in corrections.values()):
                raise ValueError("corrected feedback must contain at least one corrected label")
            # Validate that corrected labels only contain valid enum values for canonical keys
            allowed_keys = {
                "road_type",
                "traffic_density",
                "road_complexity",
                "hazard_presence",
                "anticipated_risk",
                "recommended_action",
            }
            for key in corrections:
                if key not in allowed_keys:
                    raise ValueError(f"corrected_labels contains invalid key: {key}")
        elif status == FeedbackStatus.rejected:
            if corrections and any(v is not None for v in corrections.values()):
                raise ValueError("rejected feedback must not contain corrected_labels")
        return self


# ======================= Full response model =======================

class TrainingSample(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    schema_version: str = "sentinel.training.v1"
    sample_id: str
    observation_id: Optional[str] = None
    hazard_id: Optional[str] = None
    source_vehicle_id: str
    captured_at: datetime
    context: Context
    media: Media
    model: ModelInfo
    prediction: PredictionLabels
    original_prediction: PredictionLabels
    final_verified_labels: Optional[FinalVerifiedLabels] = None
    provenance: Provenance
    quality: Optional[QualityMetadata] = None
    dataset_status: DatasetStatus = DatasetStatus.pending
    feedback_status: FeedbackStatus = FeedbackStatus.pending
    feedback_history: List[FeedbackEvent] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    revision: int = 1

    @field_validator("captured_at", "created_at", "updated_at", mode="before")
    @classmethod
    def _dt_utc(cls, v: Any) -> datetime:
        return _normalize_utc(v)

    def to_api_dict(self) -> dict:
        """Return a camelCase dict for API responses, stripping _id."""
        return self.model_dump(by_alias=True, exclude={"_id"})

    def to_export_dict(self) -> dict:
        """Return a snake_case dict for JSONL export, stripping _id."""
        return self.model_dump(by_alias=False, exclude={"_id"})


class TrainingSampleListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    items: List[TrainingSample]
    count: int
    limit: int
    skip: int
    mode: Literal["mongo", "memory"]


class TrainingStatsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    mode: Literal["mongo", "memory"]
    total: int
    pending: int
    verified: int
    rejected: int
    confirmed: int
    corrected: int
    exportable: int
    by_road_type: Dict[str, int]
    by_hazard_presence: Dict[str, int]
    by_recommended_action: Dict[str, int]

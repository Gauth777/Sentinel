"""Deterministic Indian-road dataset replay models for Sentinel.

Schema version: sentinel.demo_replay.v1
"""
from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _to_camel(snake: str) -> str:
    parts = snake.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


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
    yield_ = "yield"  # yield is Python keyword
    prepare_to_stop = "prepare_to_stop"
    change_lane = "change_lane"


class DemoExpectedLabels(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    road_type: RoadType
    traffic_density: TrafficDensity
    road_complexity: RoadComplexity
    hazard_presence: HazardPresence
    anticipated_risk: AnticipatedRisk
    recommended_action: RecommendedAction


class DemoLocation(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class DemoReplaySample(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    sample_id: str = Field(min_length=1)
    sequence_index: int = Field(ge=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    dashcam_path: str = Field(min_length=1)
    topview_path: str = Field(min_length=1)
    location: Optional[DemoLocation] = None
    heading_degrees: Optional[float] = Field(default=None, ge=0, lt=360)
    captured_at: Optional[datetime] = None
    tags: list[str] = Field(default_factory=list)
    expected_labels: Optional[DemoExpectedLabels] = None
    cached_prediction_path: Optional[str] = None
    enabled: bool = True

    @field_validator("sample_id")
    @classmethod
    def _safe_sample_id(cls, v: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_\-]+", v):
            raise ValueError("sample_id must contain only alphanumeric, underscore or hyphen")
        return v

    @field_validator("dashcam_path", "topview_path")
    @classmethod
    def _relative_path(cls, v: str) -> str:
        if v.startswith("/") or v.startswith("\\"):
            raise ValueError("paths must be relative, not absolute")
        if ".." in v:
            raise ValueError("paths must not contain parent directory references")
        ext = v.split(".")[-1].lower()
        if ext not in {"jpg", "jpeg", "png", "webp"}:
            raise ValueError(f"unsupported image extension: {ext}")
        return v

    @field_validator("tags")
    @classmethod
    def _no_empty_tags(cls, v: list[str]) -> list[str]:
        if any(t.strip() == "" for t in v):
            raise ValueError("tags must not contain empty strings")
        return v

    @field_validator("captured_at", mode="before")
    @classmethod
    def _captured_at_utc(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if v.tzinfo is None:
            v = v.replace(tzinfo=datetime.utcnow().astimezone().tzinfo)
        return v


class DemoReplayManifest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    schema_version: str = "1.0"
    mode: str = "dataset_replay"
    loop: bool = True
    samples: list[DemoReplaySample]

    @field_validator("mode")
    @classmethod
    def _mode_value(cls, v: str) -> str:
        if v != "dataset_replay":
            raise ValueError("mode must be dataset_replay")
        return v

    @field_validator("samples")
    @classmethod
    def _validate_samples(cls, samples: list[DemoReplaySample]) -> list[DemoReplaySample]:
        ids = [s.sample_id for s in samples]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate sample_id values")

        seqs = [s.sequence_index for s in samples if s.enabled]
        if len(seqs) != len(set(seqs)):
            raise ValueError("duplicate sequence_index among enabled samples")

        # Check enabled samples are ordered by sequence_index
        enabled_sorted = sorted(seqs)
        if seqs != enabled_sorted:
            raise ValueError("enabled samples must be ordered by sequence_index")

        return samples

    def enabled_samples(self) -> list[DemoReplaySample]:
        return [s for s in self.samples if s.enabled]


class DemoReplayStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    mode: str = "dataset_replay"
    status: str  # ready, unconfigured, invalid
    sample_count: int
    current_index: int
    current_sample_id: Optional[str] = None
    loop: bool


# --------------- Public API response models (camelCase) ---------------


class DemoReplayPublicSample(BaseModel):
    """Safe public sample metadata — no filesystem paths or expected labels."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    sample_id: str
    sequence_index: int
    title: str
    description: str
    dashcam_url: str
    topview_url: str
    location: Optional[DemoLocation] = None
    heading_degrees: Optional[float] = None
    tags: list[str] = Field(default_factory=list)


class DemoReplayStatusResponse(BaseModel):
    """GET /api/sentinel/demo-replay"""

    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    mode: str = "dataset_replay"
    status: str
    sample_count: int
    current_index: int
    current_sample_id: Optional[str] = None
    loop: bool


class DemoReplayCurrentResponse(BaseModel):
    """GET /api/sentinel/demo-replay/current"""

    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    mode: str = "dataset_replay"
    sample: DemoReplayPublicSample
    sample_count: int
    current_index: int
    has_next: bool


class DemoReplayAdvanceResponse(BaseModel):
    """POST /api/sentinel/demo-replay/advance"""

    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    previous_sample_id: str
    sample: DemoReplayPublicSample
    current_index: int
    looped: bool
    sample_count: int


class DemoReplayResetResponse(BaseModel):
    """POST /api/sentinel/demo-replay/reset"""

    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    sample: DemoReplayPublicSample
    current_index: int
    sample_count: int


class DemoReplayReloadResponse(BaseModel):
    """POST /api/sentinel/demo-replay/reload"""

    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    mode: str = "dataset_replay"
    status: str
    sample_count: int
    current_index: int
    current_sample_id: Optional[str] = None
    loop: bool


class DemoReplayEvidenceResponse(BaseModel):
    """GET /api/sentinel/demo-replay/samples/{sample_id}/evidence"""

    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    sample_id: str
    source_sample_id: Optional[str] = None
    expected_labels: Optional[DemoExpectedLabels] = None
    source_map_available: bool = False


class DemoReplayGraphVerifyResponse(BaseModel):
    """GET /api/sentinel/demo-replay/graph-verify?hazardId=..."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)

    hazard_id: str
    graph_backend: str  # "neo4j" | "in_memory"
    hazard_node_found: bool = False
    observation_node_found: bool = False
    relationship_found: bool = False
    warning_node_found: bool = False
    summary: str = ""

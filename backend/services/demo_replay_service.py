"""Deterministic Indian-road dataset replay service for Sentinel.

Loads a curated manifest, maintains deterministic replay state,
and serves safe metadata and image responses.

No MongoDB dependency.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from models.demo_replay import DemoReplayManifest, DemoReplaySample

logger = logging.getLogger(__name__)

DEFAULT_SCENARIO_DIR = Path(__file__).resolve().parents[1] / "demo_scenarios"
ENV_SCENARIO_DIR = "SENTINEL_DEMO_SCENARIO_DIR"


class DemoReplayServiceError(Exception):
    pass


class DemoReplayService:
    """Deterministic dataset replay without external dependencies.

    State:
      - _manifest: parsed DemoReplayManifest or None
      - _current_index: index into enabled_samples list
      - _initialized: whether initialize() was called
      - _error: initialization error message or None
    """

    def __init__(self, scenario_dir: Optional[str] = None) -> None:
        self._lock = asyncio.Lock()
        self._manifest: Optional[DemoReplayManifest] = None
        self._current_index = 0
        self._initialized = False
        self._error: Optional[str] = None
        self._source_map: Optional[Dict[str, str]] = None
        self._source_map_loaded = False

        raw_dir = scenario_dir or os.environ.get(ENV_SCENARIO_DIR)
        if raw_dir:
            self._scenario_dir = Path(raw_dir)
        else:
            self._scenario_dir = DEFAULT_SCENARIO_DIR

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Load and validate manifest. Safe to call multiple times."""
        async with self._lock:
            if self._initialized:
                return
            self._load_manifest_locked()
            self._initialized = True

    def _load_manifest_locked(self) -> None:
        """Internal helper to load manifest. Must hold lock."""
        manifest_path = self._scenario_dir / "manifest.json"
        if not manifest_path.exists():
            self._error = f"Manifest not found at {manifest_path}"
            self._manifest = None
            logger.info("DemoReplayService: %s", self._error)
            return

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._manifest = DemoReplayManifest(**data)
            self._error = None
            logger.info(
                "DemoReplayService loaded %d enabled samples",
                len(self._manifest.enabled_samples()),
            )
        except Exception as e:
            self._error = f"Invalid manifest: {e}"
            # Do NOT clear a previously valid manifest on a failed reload/load
            logger.error("DemoReplayService: %s", self._error)

    async def close(self) -> None:
        """No-op for now; reserved for future cleanup."""
        pass

    # ------------------------------------------------------------------
    # Reload
    # ------------------------------------------------------------------

    async def reload(self) -> Dict[str, Any]:
        """Reload the manifest file.

        Resets current_index to 0 on success.
        If replacement is invalid/missing, preserves previous valid manifest.
        """
        async with self._lock:
            prev_manifest = self._manifest
            prev_error = self._error

            self._load_manifest_locked()
            self._initialized = True
            self._source_map = None
            self._source_map_loaded = False

            if self._manifest is not None:
                self._current_index = 0
            elif prev_manifest is not None:
                # Revert to previous valid manifest
                self._manifest = prev_manifest
                self._error = prev_error
                logger.warning("Manifest reload failed; preserving previous valid manifest")

            return self._status()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def status(self) -> Dict[str, Any]:
        async with self._lock:
            return self._status()

    def _status(self) -> Dict[str, Any]:
        if not self._initialized:
            return {
                "mode": "dataset_replay",
                "status": "unconfigured",
                "sample_count": 0,
                "current_index": 0,
                "current_sample_id": None,
                "loop": True,
            }

        if self._manifest is None:
            if self._error and "not found" in self._error.lower():
                return {
                    "mode": "dataset_replay",
                    "status": "unconfigured",
                    "sample_count": 0,
                    "current_index": 0,
                    "current_sample_id": None,
                    "loop": True,
                }
            return {
                "mode": "dataset_replay",
                "status": "invalid",
                "sample_count": 0,
                "current_index": 0,
                "current_sample_id": None,
                "loop": True,
            }

        enabled = self._manifest.enabled_samples()
        count = len(enabled)
        if count == 0:
            return {
                "mode": "dataset_replay",
                "status": "unconfigured",
                "sample_count": 0,
                "current_index": 0,
                "current_sample_id": None,
                "loop": self._manifest.loop,
            }

        current = enabled[self._current_index]
        return {
            "mode": "dataset_replay",
            "status": "ready",
            "sample_count": count,
            "current_index": self._current_index,
            "current_sample_id": current.sample_id,
            "loop": self._manifest.loop,
        }

    # ------------------------------------------------------------------
    # Samples
    # ------------------------------------------------------------------

    async def list_samples(self) -> List[Dict[str, Any]]:
        """Return safe metadata for enabled samples."""
        async with self._lock:
            if self._manifest is None:
                return []
            return [self._safe_sample(s) for s in self._manifest.enabled_samples()]

    async def get_current(self) -> Optional[Dict[str, Any]]:
        """Return current sample with hasNext flag."""
        async with self._lock:
            if self._manifest is None:
                return None
            enabled = self._manifest.enabled_samples()
            if not enabled:
                return None
            current = enabled[self._current_index]
            return {
                "mode": "dataset_replay",
                "sample": self._safe_sample(current),
                "sample_count": len(enabled),
                "current_index": self._current_index,
                "has_next": len(enabled) > 1,
            }

    async def get_sample(self, sample_id: str) -> Optional[Dict[str, Any]]:
        """Return one safe sample by ID, or None if not found/disabled."""
        async with self._lock:
            if self._manifest is None:
                return None
            for s in self._manifest.enabled_samples():
                if s.sample_id == sample_id:
                    return self._safe_sample(s)
            return None

    # ------------------------------------------------------------------
    # Advance / Reset
    # ------------------------------------------------------------------

    async def advance(self) -> Optional[Dict[str, Any]]:
        """Move to next sample; loop if configured."""
        async with self._lock:
            if self._manifest is None:
                return None
            enabled = self._manifest.enabled_samples()
            if not enabled:
                return None

            previous_id = enabled[self._current_index].sample_id
            self._current_index += 1
            looped = False
            if self._current_index >= len(enabled):
                if self._manifest.loop:
                    self._current_index = 0
                    looped = True
                else:
                    self._current_index = len(enabled) - 1

            current = enabled[self._current_index]
            return {
                "previous_sample_id": previous_id,
                "sample": self._safe_sample(current),
                "current_index": self._current_index,
                "looped": looped,
                "sample_count": len(enabled),
            }

    async def reset(self) -> Optional[Dict[str, Any]]:
        """Return to first enabled sample."""
        async with self._lock:
            if self._manifest is None:
                return None
            enabled = self._manifest.enabled_samples()
            if not enabled:
                return None
            self._current_index = 0
            current = enabled[0]
            return {
                "sample": self._safe_sample(current),
                "current_index": 0,
                "sample_count": len(enabled),
            }

    # ------------------------------------------------------------------
    # Media resolution
    # ------------------------------------------------------------------

    async def resolve_media(self, sample_id: str, view: str) -> Optional[Dict[str, Any]]:
        """Resolve and validate a sample image path.

        Returns dict with path, mime_type or None if invalid/missing.
        """
        async with self._lock:
            if self._manifest is None:
                return None

            sample: Optional[DemoReplaySample] = None
            for s in self._manifest.enabled_samples():
                if s.sample_id == sample_id:
                    sample = s
                    break

            if sample is None:
                return None

            rel_path = sample.dashcam_path if view == "dashcam" else sample.topview_path
            target = self._scenario_dir / rel_path

            # Security: must be inside scenario dir
            try:
                target.resolve().relative_to(self._scenario_dir.resolve())
            except ValueError:
                return None

            if not target.exists():
                return None

            ext = target.suffix.lower()
            mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
            mime = mime_map.get(ext, "application/octet-stream")

            return {"path": str(target), "mime_type": mime}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def get_internal_definition(self, sample_id: str) -> Optional[DemoReplaySample]:
        """Return the full Pydantic model for internal use (not exposed via API)."""
        async with self._lock:
            if self._manifest is None:
                return None
            for s in self._manifest.enabled_samples():
                if s.sample_id == sample_id:
                    return s
            return None

    # ------------------------------------------------------------------
    # Evidence helpers
    # ------------------------------------------------------------------

    async def get_source_map(self) -> Optional[Dict[str, str]]:
        """Load and cache source_map.example.json. Returns None if not found."""
        async with self._lock:
            if self._source_map_loaded:
                return self._source_map
            self._source_map_loaded = True
            source_map_path = self._scenario_dir / "source_map.example.json"
            if not source_map_path.exists():
                self._source_map = None
                return None
            try:
                with open(source_map_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._source_map = {str(k): str(v) for k, v in data.items()}
                else:
                    self._source_map = None
            except Exception as e:
                logger.warning("Failed to load source_map: %s", e)
                self._source_map = None
            return self._source_map

    async def get_expected_labels(self, sample_id: str) -> Optional[Dict[str, Any]]:
        """Return expected_labels dict for a sample, or None if not found."""
        async with self._lock:
            if self._manifest is None:
                return None
            for s in self._manifest.enabled_samples():
                if s.sample_id == sample_id:
                    if s.expected_labels is None:
                        return None
                    return s.expected_labels.model_dump(by_alias=True)
            return None

    def _safe_sample(self, sample: DemoReplaySample) -> Dict[str, Any]:
        """Build a safe API response dict without filesystem paths or expected labels."""
        loc = None
        if sample.location:
            loc = {"latitude": sample.location.latitude, "longitude": sample.location.longitude}

        return {
            "sampleId": sample.sample_id,
            "sequenceIndex": sample.sequence_index,
            "title": sample.title,
            "description": sample.description,
            "dashcamUrl": f"/api/sentinel/demo-replay/samples/{sample.sample_id}/dashcam",
            "topviewUrl": f"/api/sentinel/demo-replay/samples/{sample.sample_id}/topview",
            "location": loc,
            "headingDegrees": sample.heading_degrees,
            "tags": sample.tags,
        }

    async def select_sample(self, sample_id: str) -> Optional[Dict[str, Any]]:
        """Select an enabled sample by ID, update current_index, and return details."""
        async with self._lock:
            if self._manifest is None:
                return None
            enabled = self._manifest.enabled_samples()
            for idx, s in enumerate(enabled):
                if s.sample_id == sample_id:
                    self._current_index = idx
                    return {
                        "sample": self._safe_sample(s),
                        "currentIndex": idx,
                        "sampleCount": len(enabled),
                    }
            return None

    async def get_cached_prediction(self, sample_id: str) -> Optional[Dict[str, Any]]:
        """Load and parse cached prediction JSON for a sample."""
        async with self._lock:
            if self._manifest is None:
                return None
            for s in self._manifest.enabled_samples():
                if s.sample_id == sample_id:
                    if not s.cached_prediction_path:
                        return None
                    path = self._scenario_dir / s.cached_prediction_path
                    if not path.exists():
                        return None
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            return json.load(f)
                    except Exception as e:
                        logger.warning("Failed to load cached prediction: %s", e)
                        return None
            return None

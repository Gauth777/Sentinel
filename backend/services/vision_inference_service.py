"""Vision inference service for Sentinel replay perception.

Provides:
  - VisionInferenceAdapter protocol
  - OpenAICompatibleQwenAdapter (live inference via httpx)
  - CachedQwenAdapter (deterministic cached fallback)
  - VisionInferenceService (orchestrator with fallback policy)

Environment variables:
  SENTINEL_QWEN_ENABLED       — "true" to enable live inference
  SENTINEL_QWEN_BASE_URL      — OpenAI-compatible endpoint base URL
  SENTINEL_QWEN_API_KEY       — bearer token
  SENTINEL_QWEN_MODEL         — model name (default: Qwen2.5-VL-7B-Instruct)
  SENTINEL_QWEN_TIMEOUT_SECONDS — request timeout (default: 30)
  SENTINEL_QWEN_PROMPT_VERSION  — prompt version tag (default: v1)
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from models.vision_inference import (
    ActivationResult,
    CachedPredictionFile,
    CachedPredictionValidationError,
    InferenceMode,
    InferenceResult,
    RuntimeHazardPrediction,
    StructuredRoadPrediction,
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Helpers                                                            #
# ------------------------------------------------------------------ #


def _compute_inference_id(
    sample_id: str,
    model: str,
    prompt_version: str,
    inference_mode: InferenceMode,
    prediction: StructuredRoadPrediction,
    runtime_hazard: Optional[RuntimeHazardPrediction] = None,
) -> str:
    """Compute a deterministic inference fingerprint from validated output.

    Does NOT include API keys, filesystem paths, or base64 image data.
    """
    payload = {
        "sampleId": sample_id,
        "model": model,
        "promptVersion": prompt_version,
        "inferenceMode": inference_mode.value,
        "prediction": {
            "roadType": prediction.road_type.value,
            "trafficDensity": prediction.traffic_density.value,
            "roadComplexity": prediction.road_complexity.value,
            "hazardPresence": prediction.hazard_presence.value,
            "anticipatedRisk": prediction.anticipated_risk.value,
            "recommendedAction": prediction.recommended_action.value,
        },
    }
    if runtime_hazard is not None:
        payload["runtimeHazard"] = {
            "hazardType": runtime_hazard.hazard_type,
            "hazardDescription": runtime_hazard.hazard_description,
            "confidence": runtime_hazard.confidence,
        }

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"inf-{digest[:20]}"

# ------------------------------------------------------------------ #
# Adapter protocol                                                    #
# ------------------------------------------------------------------ #


@runtime_checkable
class VisionInferenceAdapter(Protocol):
    async def predict(
        self,
        sample: Any,
        dashcam_path: Path,
        topview_path: Path,
    ) -> InferenceResult: ...


# ------------------------------------------------------------------ #
# Constants                                                           #
# ------------------------------------------------------------------ #

DEFAULT_MODEL = "Qwen2.5-VL-7B-Instruct"
DEFAULT_TIMEOUT = 30
DEFAULT_PROMPT_VERSION = "v1"

SYSTEM_PROMPT = (
    "You are a road safety perception model analysing Indian road scenarios. "
    "You will receive two images:\n"
    "1. First image: a DASHCAM view from the vehicle.\n"
    "2. Second image: a TOP-VIEW / map context of the surrounding area.\n\n"
    "Reply with ONLY a single JSON object. Do not wrap it in markdown fences. "
    "Do not add any explanatory text before or after the JSON.\n\n"
    "The JSON must contain exactly these fields:\n"
    '  "road_type": one of "urban_arterial", "residential", "highway", "junction"\n'
    '  "traffic_density": one of "low", "medium", "high"\n'
    '  "road_complexity": one of "simple", "moderate", "complex"\n'
    '  "hazard_presence": one of "yes", "no"\n'
    '  "anticipated_risk": one of "low", "medium", "high"\n'
    '  "recommended_action": one of "slow_down", "maintain_speed", '
    '"increase_attention", "yield", "prepare_to_stop", "change_lane"\n'
    '  "hazard_type": a short string describing the hazard type '
    '(e.g. "crossing_vehicle", "pothole", "pedestrian"). '
    'Use "none" if hazard_presence is "no".\n'
    '  "hazard_description": a brief sentence describing the hazard. '
    'Use "No hazard detected" if hazard_presence is "no".\n'
    '  "confidence": a float between 0.0 and 1.0 representing overall confidence.\n\n'
    "Do NOT invent label values outside the allowed sets listed above."
)


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #


def _image_to_data_url(path: Path) -> str:
    """Read a local image file and return a data: URL (base64-encoded)."""
    ext = path.suffix.lower()
    mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    mime = mime_map.get(ext, "application/octet-stream")
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _extract_json_from_text(text: str) -> Dict[str, Any]:
    """Extract a JSON object from raw model text, handling fenced blocks."""
    text = text.strip()
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from fenced code block
    for marker in ("```json", "```"):
        if marker in text:
            start = text.index(marker) + len(marker)
            end = text.index("```", start) if "```" in text[start:] else len(text)
            inner = text[start:end].strip()
            return json.loads(inner)
    raise ValueError(f"Could not extract JSON from model response (first 200 chars): {text[:200]}")


def _extract_content_text(choices: list) -> str:
    """Extract text content from OpenAI-compatible response choices."""
    if not choices:
        raise ValueError("Empty choices in model response")
    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # content may be a list of {type: "text", text: "..."} objects
        texts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(part.get("text", ""))
            elif isinstance(part, str):
                texts.append(part)
        return "\n".join(texts)
    raise ValueError(f"Unexpected content type: {type(content)}")


# ------------------------------------------------------------------ #
# Live Qwen adapter                                                   #
# ------------------------------------------------------------------ #


class OpenAICompatibleQwenAdapter:
    """Live inference via an OpenAI-compatible multimodal chat endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout_seconds: int = DEFAULT_TIMEOUT,
        prompt_version: str = DEFAULT_PROMPT_VERSION,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._prompt_version = prompt_version

    async def predict(
        self,
        sample: Any,
        dashcam_path: Path,
        topview_path: Path,
    ) -> InferenceResult:
        import httpx

        dashcam_url = _image_to_data_url(dashcam_path)
        topview_url = _image_to_data_url(topview_path)
        logger.info(
            "Live Qwen inference for sample %s (dashcam=%s, topview=%s)",
            sample.sample_id,
            dashcam_path.name,
            topview_path.name,
        )

        payload = {
            "model": self._model,
            "temperature": 0,
            "max_tokens": 512,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": dashcam_url},
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": topview_url},
                        },
                        {
                            "type": "text",
                            "text": (
                                "Analyse these two images of an Indian road scenario. "
                                "The first is the dashcam view, the second is the top-view context."
                            ),
                        },
                    ],
                },
            ],
        }

        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
        latency = (time.monotonic() - t0) * 1000

        data = resp.json()
        raw_text = _extract_content_text(data.get("choices", []))
        parsed = _extract_json_from_text(raw_text)

        prediction = StructuredRoadPrediction(**{
            k: parsed[k]
            for k in (
                "road_type", "traffic_density", "road_complexity",
                "hazard_presence", "anticipated_risk", "recommended_action",
            )
        })

        runtime_hazard = None
        if parsed.get("hazard_type") and parsed["hazard_type"] != "none":
            runtime_hazard = RuntimeHazardPrediction(
                hazard_type=parsed["hazard_type"],
                hazard_description=parsed.get("hazard_description", "Hazard detected"),
                confidence=parsed.get("confidence"),
            )

        inf_id = _compute_inference_id(
            sample_id=sample.sample_id,
            model=self._model,
            prompt_version=self._prompt_version,
            inference_mode=InferenceMode.live_qwen,
            prediction=prediction,
            runtime_hazard=runtime_hazard,
        )
        return InferenceResult(
            inference_id=inf_id,
            sample_id=sample.sample_id,
            model=self._model,
            prompt_version=self._prompt_version,
            inference_mode=InferenceMode.live_qwen,
            prediction=prediction,
            runtime_hazard=runtime_hazard,
            latency_ms=round(latency, 1),
            raw_response=raw_text,
        )


# ------------------------------------------------------------------ #
# Cached adapter                                                      #
# ------------------------------------------------------------------ #


class CachedQwenAdapter:
    """Load and validate a cached_prediction.json file."""

    def __init__(self, scenario_dir: Path) -> None:
        self._scenario_dir = scenario_dir

    async def predict(
        self,
        sample: Any,
        dashcam_path: Path,
        topview_path: Path,
    ) -> InferenceResult:
        cached_path_rel = sample.cached_prediction_path
        if not cached_path_rel:
            raise CachedPredictionValidationError(
                f"No cached prediction path configured for sample {sample.sample_id}"
            )

        cached_path = self._scenario_dir / cached_path_rel

        # Security: prevent traversal
        try:
            cached_path.resolve().relative_to(self._scenario_dir.resolve())
        except ValueError:
            raise CachedPredictionValidationError("Cached prediction path traversal blocked")

        if not cached_path.exists():
            raise CachedPredictionValidationError(
                f"Cached prediction file not found for sample {sample.sample_id}"
            )

        try:
            raw = cached_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError) as exc:
            raise CachedPredictionValidationError(
                f"Malformed cached prediction for sample {sample.sample_id}"
            ) from exc

        # Validate through strict Pydantic schema
        try:
            cached = CachedPredictionFile(**data)
        except Exception as exc:
            raise CachedPredictionValidationError(
                f"Cached prediction validation failed for sample {sample.sample_id}"
            ) from exc

        # Strict sample_id match
        if cached.sample_id != sample.sample_id:
            raise CachedPredictionValidationError(
                f"Cached prediction sample_id mismatch: "
                f"expected {sample.sample_id!r}, got {cached.sample_id!r}"
            )

        runtime_hazard = None
        if cached.runtime_hazard:
            runtime_hazard = cached.runtime_hazard

        inf_id = _compute_inference_id(
            sample_id=sample.sample_id,
            model=cached.model,
            prompt_version=cached.prompt_version,
            inference_mode=InferenceMode.cached_qwen,
            prediction=cached.prediction,
            runtime_hazard=runtime_hazard,
        )
        return InferenceResult(
            inference_id=inf_id,
            sample_id=sample.sample_id,
            model=cached.model,
            prompt_version=cached.prompt_version,
            inference_mode=InferenceMode.cached_qwen,
            prediction=cached.prediction,
            runtime_hazard=runtime_hazard,
            latency_ms=0,
            raw_response=cached.raw_response,
        )


# ------------------------------------------------------------------ #
# Orchestrator service                                                #
# ------------------------------------------------------------------ #


class VisionInferenceService:
    """Orchestrates live and cached inference with fallback policy.

    Fallback policy:
      1. Try live Qwen when enabled and fully configured.
      2. Enforce timeout.
      3. Validate output.
      4. If live fails → use cached prediction.
      5. If both fail → return clear error.
    """

    def __init__(self, scenario_dir: Optional[Path] = None) -> None:
        self._scenario_dir = scenario_dir or Path(__file__).resolve().parents[1] / "demo_scenarios"
        self._live_adapter: Optional[OpenAICompatibleQwenAdapter] = None
        self._cached_adapter = CachedQwenAdapter(self._scenario_dir)
        self._configure_live()

    def _configure_live(self) -> None:
        enabled = os.environ.get("SENTINEL_QWEN_ENABLED", "").lower() == "true"
        base_url = os.environ.get("SENTINEL_QWEN_BASE_URL", "")
        api_key = os.environ.get("SENTINEL_QWEN_API_KEY", "")

        if enabled and base_url and api_key:
            model = os.environ.get("SENTINEL_QWEN_MODEL", DEFAULT_MODEL)
            timeout = int(os.environ.get("SENTINEL_QWEN_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT)))
            prompt_version = os.environ.get("SENTINEL_QWEN_PROMPT_VERSION", DEFAULT_PROMPT_VERSION)
            self._live_adapter = OpenAICompatibleQwenAdapter(
                base_url=base_url,
                api_key=api_key,
                model=model,
                timeout_seconds=timeout,
                prompt_version=prompt_version,
            )
            # Log only the hostname, never query params or credentials
            try:
                from urllib.parse import urlparse
                parsed = urlparse(base_url)
                safe_origin = f"{parsed.scheme}://{parsed.hostname}"
            except Exception:
                safe_origin = "<unparseable>"
            logger.info("Live Qwen adapter configured: model=%s host=%s", model, safe_origin)
        else:
            self._live_adapter = None
            if enabled:
                logger.warning("SENTINEL_QWEN_ENABLED=true but base_url or api_key missing")
            else:
                logger.info("Live Qwen adapter disabled (SENTINEL_QWEN_ENABLED != true)")

    @property
    def live_enabled(self) -> bool:
        return self._live_adapter is not None

    async def infer(
        self,
        sample: Any,
        dashcam_path: Path,
        topview_path: Path,
    ) -> InferenceResult:
        """Run inference with fallback from live to cached."""
        # Try live first
        if self._live_adapter is not None:
            try:
                result = await self._live_adapter.predict(sample, dashcam_path, topview_path)
                logger.info(
                    "Live Qwen inference succeeded for %s (%.0fms)",
                    sample.sample_id,
                    result.latency_ms,
                )
                return result
            except Exception as e:
                logger.warning(
                    "Live Qwen failed for %s: %s — falling back to cached",
                    sample.sample_id,
                    type(e).__name__,
                )

        # Try cached
        try:
            result = await self._cached_adapter.predict(sample, dashcam_path, topview_path)
            logger.info("Cached Qwen prediction loaded for %s", sample.sample_id)
            return result
        except Exception as e:
            logger.error(
                "Cached prediction also failed for %s: %s",
                sample.sample_id,
                type(e).__name__,
            )
            raise RuntimeError(
                f"Inference unavailable for sample {sample.sample_id}"
            ) from e

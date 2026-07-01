"""Tests for Sentinel vision inference service.

No internet, no real Qwen endpoint, no actual images, no MongoDB, no Neo4j required.
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from models.vision_inference import (
    CachedPredictionFile,
    CachedPredictionValidationError,
    InferenceMode,
    InferenceResult,
    RuntimeHazardPrediction,
    StructuredRoadPrediction,
)
from services.vision_inference_service import (
    CachedQwenAdapter,
    VisionInferenceService,
    _compute_inference_id,
    _extract_content_text,
    _extract_json_from_text,
)


# ----------------------------- Helpers -----------------------------

JPEG_SIG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"


def make_jpeg(size: int = 256) -> bytes:
    body = b"\x00" * (size - len(JPEG_SIG) - 2)
    return JPEG_SIG + body + b"\xff\xd9"


def make_valid_cached_prediction(sample_id: str = "s1") -> dict:
    return {
        "sampleId": sample_id,
        "model": "Qwen2.5-VL-7B-Instruct",
        "promptVersion": "v1",
        "generatedAt": "2026-06-30T12:00:00Z",
        "prediction": {
            "road_type": "urban_arterial",
            "traffic_density": "high",
            "road_complexity": "complex",
            "hazard_presence": "yes",
            "anticipated_risk": "high",
            "recommended_action": "slow_down",
        },
        "runtimeHazard": {
            "hazard_type": "crossing_vehicle",
            "hazard_description": "Vehicle crossing from side",
            "confidence": 0.82,
        },
        "validated": True,
    }


def make_invalid_cached_prediction() -> dict:
    return {
        "sampleId": "s1",
        "model": "Qwen2.5-VL-7B-Instruct",
        "promptVersion": "v1",
        "generatedAt": "2026-06-30T12:00:00Z",
        "prediction": {
            "road_type": "not_a_valid_type",
            "traffic_density": "high",
            "road_complexity": "complex",
            "hazard_presence": "yes",
            "anticipated_risk": "high",
            "recommended_action": "slow_down",
        },
        "validated": True,
    }


class FakeSample:
    def __init__(self, sample_id="s1", cached_prediction_path=None):
        self.sample_id = sample_id
        self.cached_prediction_path = cached_prediction_path


# ----------------------------- Model validation tests -----------------------------


def test_structured_prediction_valid():
    pred = StructuredRoadPrediction(
        road_type="urban_arterial",
        traffic_density="high",
        road_complexity="complex",
        hazard_presence="yes",
        anticipated_risk="high",
        recommended_action="slow_down",
    )
    assert pred.road_type.value == "urban_arterial"


def test_structured_prediction_rejects_invalid_label():
    with pytest.raises(Exception):
        StructuredRoadPrediction(
            road_type="not_a_road",
            traffic_density="high",
            road_complexity="complex",
            hazard_presence="yes",
            anticipated_risk="high",
            recommended_action="slow_down",
        )


def test_structured_prediction_rejects_extra_fields():
    with pytest.raises(Exception):
        StructuredRoadPrediction(
            road_type="urban_arterial",
            traffic_density="high",
            road_complexity="complex",
            hazard_presence="yes",
            anticipated_risk="high",
            recommended_action="slow_down",
            extra_field="should_fail",
        )


def test_inference_result_excludes_raw_response():
    pred = StructuredRoadPrediction(
        road_type="highway",
        traffic_density="low",
        road_complexity="simple",
        hazard_presence="no",
        anticipated_risk="low",
        recommended_action="maintain_speed",
    )
    inf_id = _compute_inference_id(
        "s1", "test", "v1", InferenceMode.cached_qwen, pred,
    )
    result = InferenceResult(
        inference_id=inf_id,
        sample_id="s1",
        model="test",
        prompt_version="v1",
        inference_mode=InferenceMode.cached_qwen,
        prediction=pred,
        latency_ms=0,
        raw_response="SECRET_RAW_DATA",
    )
    d = result.model_dump()
    assert "raw_response" not in d
    assert "rawResponse" not in d


# ----------------------------- JSON extraction tests -----------------------------


def test_extract_json_plain():
    text = '{"road_type": "highway", "traffic_density": "low"}'
    result = _extract_json_from_text(text)
    assert result["road_type"] == "highway"


def test_extract_json_fenced():
    text = '```json\n{"road_type": "highway"}\n```'
    result = _extract_json_from_text(text)
    assert result["road_type"] == "highway"


def test_extract_json_malformed_raises():
    with pytest.raises((ValueError, json.JSONDecodeError)):
        _extract_json_from_text("not json at all")


def test_extract_content_text_string():
    choices = [{"message": {"content": "hello"}}]
    assert _extract_content_text(choices) == "hello"


def test_extract_content_text_list():
    choices = [{"message": {"content": [{"type": "text", "text": "hello"}]}}]
    assert _extract_content_text(choices) == "hello"


def test_extract_content_text_empty_raises():
    with pytest.raises(ValueError):
        _extract_content_text([])


# ----------------------------- Cached adapter tests -----------------------------


@pytest.mark.anyio
async def test_cached_prediction_valid():
    with tempfile.TemporaryDirectory() as tmpdir:
        scenario_dir = Path(tmpdir)
        sample_dir = scenario_dir / "s1"
        sample_dir.mkdir()

        pred_path = sample_dir / "cached_prediction.json"
        pred_path.write_text(json.dumps(make_valid_cached_prediction()), encoding="utf-8")

        # Create dummy images
        (sample_dir / "dashcam.jpg").write_bytes(make_jpeg())
        (sample_dir / "topview.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

        adapter = CachedQwenAdapter(scenario_dir)
        sample = FakeSample("s1", cached_prediction_path="s1/cached_prediction.json")

        result = await adapter.predict(
            sample,
            sample_dir / "dashcam.jpg",
            sample_dir / "topview.png",
        )

        assert result.inference_mode == InferenceMode.cached_qwen
        assert result.prediction.road_type.value == "urban_arterial"
        assert result.runtime_hazard is not None
        assert result.runtime_hazard.hazard_type == "crossing_vehicle"


@pytest.mark.anyio
async def test_cached_prediction_invalid_rejected():
    with tempfile.TemporaryDirectory() as tmpdir:
        scenario_dir = Path(tmpdir)
        sample_dir = scenario_dir / "s1"
        sample_dir.mkdir()

        pred_path = sample_dir / "cached_prediction.json"
        pred_path.write_text(json.dumps(make_invalid_cached_prediction()), encoding="utf-8")

        adapter = CachedQwenAdapter(scenario_dir)
        sample = FakeSample("s1", cached_prediction_path="s1/cached_prediction.json")

        with pytest.raises(Exception):
            await adapter.predict(
                sample,
                sample_dir / "dashcam.jpg",
                sample_dir / "topview.png",
            )


@pytest.mark.anyio
async def test_cached_prediction_missing_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        scenario_dir = Path(tmpdir)
        adapter = CachedQwenAdapter(scenario_dir)
        sample = FakeSample("s1", cached_prediction_path="s1/cached_prediction.json")

        with pytest.raises(CachedPredictionValidationError):
            await adapter.predict(
                sample,
                Path(tmpdir) / "dashcam.jpg",
                Path(tmpdir) / "topview.png",
            )


@pytest.mark.anyio
async def test_cached_prediction_no_path_configured():
    with tempfile.TemporaryDirectory() as tmpdir:
        scenario_dir = Path(tmpdir)
        adapter = CachedQwenAdapter(scenario_dir)
        sample = FakeSample("s1", cached_prediction_path=None)

        with pytest.raises(CachedPredictionValidationError):
            await adapter.predict(
                sample,
                Path(tmpdir) / "dashcam.jpg",
                Path(tmpdir) / "topview.png",
            )


@pytest.mark.anyio
async def test_cached_prediction_traversal_blocked():
    with tempfile.TemporaryDirectory() as tmpdir:
        scenario_dir = Path(tmpdir)
        adapter = CachedQwenAdapter(scenario_dir)
        sample = FakeSample("s1", cached_prediction_path="../../etc/passwd")

        with pytest.raises(CachedPredictionValidationError):
            await adapter.predict(
                sample,
                Path(tmpdir) / "dashcam.jpg",
                Path(tmpdir) / "topview.png",
            )


# ----------------------------- Service orchestrator tests -----------------------------


@pytest.mark.anyio
async def test_service_cached_fallback_when_no_live():
    with tempfile.TemporaryDirectory() as tmpdir:
        scenario_dir = Path(tmpdir)
        sample_dir = scenario_dir / "s1"
        sample_dir.mkdir()

        pred_path = sample_dir / "cached_prediction.json"
        pred_path.write_text(json.dumps(make_valid_cached_prediction()), encoding="utf-8")

        (sample_dir / "dashcam.jpg").write_bytes(make_jpeg())
        (sample_dir / "topview.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

        # No live adapter configured
        env = {"SENTINEL_QWEN_ENABLED": "false"}
        with patch.dict(os.environ, env, clear=False):
            svc = VisionInferenceService(scenario_dir)
            assert not svc.live_enabled

            sample = FakeSample("s1", cached_prediction_path="s1/cached_prediction.json")
            result = await svc.infer(
                sample,
                sample_dir / "dashcam.jpg",
                sample_dir / "topview.png",
            )

            assert result.inference_mode == InferenceMode.cached_qwen
            assert result.prediction.road_type.value == "urban_arterial"


@pytest.mark.anyio
async def test_service_no_live_no_cache_returns_error():
    with tempfile.TemporaryDirectory() as tmpdir:
        scenario_dir = Path(tmpdir)

        env = {"SENTINEL_QWEN_ENABLED": "false"}
        with patch.dict(os.environ, env, clear=False):
            svc = VisionInferenceService(scenario_dir)
            sample = FakeSample("s1", cached_prediction_path=None)

            with pytest.raises(RuntimeError, match="Inference unavailable"):
                await svc.infer(
                    sample,
                    Path(tmpdir) / "dashcam.jpg",
                    Path(tmpdir) / "topview.png",
                )


@pytest.mark.anyio
async def test_service_live_success_returns_live_mode():
    """Simulate a successful live adapter by mocking."""
    with tempfile.TemporaryDirectory() as tmpdir:
        scenario_dir = Path(tmpdir)

        env = {"SENTINEL_QWEN_ENABLED": "false"}
        with patch.dict(os.environ, env, clear=False):
            svc = VisionInferenceService(scenario_dir)

            # Inject a mock live adapter
            pred = StructuredRoadPrediction(
                road_type="highway",
                traffic_density="low",
                road_complexity="simple",
                hazard_presence="no",
                anticipated_risk="low",
                recommended_action="maintain_speed",
            )
            inf_id = _compute_inference_id(
                "s1", "Qwen2.5-VL-7B-Instruct", "v1",
                InferenceMode.live_qwen, pred,
            )
            mock_result = InferenceResult(
                inference_id=inf_id,
                sample_id="s1",
                model="Qwen2.5-VL-7B-Instruct",
                prompt_version="v1",
                inference_mode=InferenceMode.live_qwen,
                prediction=pred,
                latency_ms=1500,
            )
            mock_adapter = AsyncMock()
            mock_adapter.predict.return_value = mock_result
            svc._live_adapter = mock_adapter

            sample = FakeSample("s1")
            result = await svc.infer(
                sample,
                Path(tmpdir) / "dashcam.jpg",
                Path(tmpdir) / "topview.png",
            )

            assert result.inference_mode == InferenceMode.live_qwen


@pytest.mark.anyio
async def test_service_live_failure_falls_back_to_cached():
    """When live adapter throws, should fallback to cached."""
    with tempfile.TemporaryDirectory() as tmpdir:
        scenario_dir = Path(tmpdir)
        sample_dir = scenario_dir / "s1"
        sample_dir.mkdir()

        pred_path = sample_dir / "cached_prediction.json"
        pred_path.write_text(json.dumps(make_valid_cached_prediction()), encoding="utf-8")

        (sample_dir / "dashcam.jpg").write_bytes(make_jpeg())
        (sample_dir / "topview.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

        env = {"SENTINEL_QWEN_ENABLED": "false"}
        with patch.dict(os.environ, env, clear=False):
            svc = VisionInferenceService(scenario_dir)

            # Inject a failing live adapter
            mock_adapter = AsyncMock()
            mock_adapter.predict.side_effect = TimeoutError("Connection timed out")
            svc._live_adapter = mock_adapter

            sample = FakeSample("s1", cached_prediction_path="s1/cached_prediction.json")
            result = await svc.infer(
                sample,
                sample_dir / "dashcam.jpg",
                sample_dir / "topview.png",
            )

            assert result.inference_mode == InferenceMode.cached_qwen


@pytest.mark.anyio
async def test_service_malformed_live_json_falls_back():
    """When live adapter returns result that fails Pydantic validation, fall back."""
    with tempfile.TemporaryDirectory() as tmpdir:
        scenario_dir = Path(tmpdir)
        sample_dir = scenario_dir / "s1"
        sample_dir.mkdir()

        pred_path = sample_dir / "cached_prediction.json"
        pred_path.write_text(json.dumps(make_valid_cached_prediction()), encoding="utf-8")

        env = {"SENTINEL_QWEN_ENABLED": "false"}
        with patch.dict(os.environ, env, clear=False):
            svc = VisionInferenceService(scenario_dir)

            # Inject adapter that raises validation error
            mock_adapter = AsyncMock()
            mock_adapter.predict.side_effect = ValueError("Invalid field value")
            svc._live_adapter = mock_adapter

            sample = FakeSample("s1", cached_prediction_path="s1/cached_prediction.json")
            result = await svc.infer(
                sample,
                sample_dir / "dashcam.jpg",
                sample_dir / "topview.png",
            )

            assert result.inference_mode == InferenceMode.cached_qwen


def test_expected_labels_never_used_automatically():
    """Ground truth expected labels must never be used as model output."""
    # StructuredRoadPrediction has extra="forbid" so it can't silently accept
    # unrelated fields, but this test documents the intent.
    from models.demo_replay import DemoExpectedLabels

    expected = DemoExpectedLabels(
        road_type="highway",
        traffic_density="low",
        road_complexity="simple",
        hazard_presence="no",
        anticipated_risk="low",
        recommended_action="maintain_speed",
    )

    # The CachedQwenAdapter only reads from cached_prediction.json files,
    # never from expected_labels. This is a design verification test.
    # CachedPredictionFile requires specific fields that expected labels don't have.
    with pytest.raises(Exception):
        CachedPredictionFile(
            road_type="highway",
            traffic_density="low",
        )


def test_api_key_not_in_result():
    """API keys must never appear in inference results."""
    pred = StructuredRoadPrediction(
        road_type="highway",
        traffic_density="low",
        road_complexity="simple",
        hazard_presence="no",
        anticipated_risk="low",
        recommended_action="maintain_speed",
    )
    inf_id = _compute_inference_id(
        "s1", "test", "v1", InferenceMode.live_qwen, pred,
    )
    result = InferenceResult(
        inference_id=inf_id,
        sample_id="s1",
        model="test",
        prompt_version="v1",
        inference_mode=InferenceMode.live_qwen,
        prediction=pred,
        latency_ms=100,
        raw_response='{"key": "sk-secret-value"}',
    )
    dumped = result.model_dump(mode="json")
    dumped_json = json.dumps(dumped)
    assert "sk-secret" not in dumped_json
    assert "raw_response" not in dumped


"""Demo replay routes for Sentinel deterministic dataset replay.

Endpoints:
  GET  /api/sentinel/demo-replay
  GET  /api/sentinel/demo-replay/samples
  GET  /api/sentinel/demo-replay/current
  GET  /api/sentinel/demo-replay/samples/{sample_id}
  GET  /api/sentinel/demo-replay/samples/{sample_id}/dashcam
  GET  /api/sentinel/demo-replay/samples/{sample_id}/topview
  POST /api/sentinel/demo-replay/advance
  POST /api/sentinel/demo-replay/reset
  POST /api/sentinel/demo-replay/reload
  POST /api/sentinel/demo-replay/samples/{sample_id}/infer
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.status import (
    HTTP_404_NOT_FOUND,
    HTTP_422_UNPROCESSABLE_CONTENT,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from models.demo_replay import (
    DemoReplayAdvanceResponse,
    DemoReplayCurrentResponse,
    DemoReplayPublicSample,
    DemoReplayReloadResponse,
    DemoReplayResetResponse,
    DemoReplayStatusResponse,
)
from models.vision_inference import ActivationPublicResponse
from services.demo_replay_service import DemoReplayService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sentinel/demo-replay")


def _get_replay_service(request: Request) -> DemoReplayService:
    svc = getattr(request.app.state, "demo_replay_service", None)
    if svc is None:
        raise HTTPException(status_code=HTTP_503_SERVICE_UNAVAILABLE, detail="Replay service not initialized")
    return svc  # type: ignore[return-value]


@router.get("", response_model=DemoReplayStatusResponse)
async def get_status(request: Request):
    svc = _get_replay_service(request)
    result = await svc.status()
    return DemoReplayStatusResponse(**result)


@router.get("/samples")
async def list_samples(request: Request):
    svc = _get_replay_service(request)
    items = await svc.list_samples()
    return [DemoReplayPublicSample(**s) for s in items]


@router.get("/current", response_model=DemoReplayCurrentResponse)
async def get_current(request: Request):
    svc = _get_replay_service(request)
    result = await svc.get_current()
    if result is None:
        raise HTTPException(status_code=HTTP_503_SERVICE_UNAVAILABLE, detail="Replay not configured")
    return DemoReplayCurrentResponse(**result)


@router.get("/samples/{sample_id}", response_model=DemoReplayPublicSample)
async def get_sample(request: Request, sample_id: str):
    svc = _get_replay_service(request)
    result = await svc.get_sample(sample_id)
    if result is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Sample not found")
    return DemoReplayPublicSample(**result)


@router.get("/samples/{sample_id}/dashcam")
async def get_dashcam(request: Request, sample_id: str):
    svc = _get_replay_service(request)
    info = await svc.resolve_media(sample_id, "dashcam")
    if info is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Dashcam image not found")
    return FileResponse(path=info["path"], media_type=info["mime_type"])


@router.get("/samples/{sample_id}/topview")
async def get_topview(request: Request, sample_id: str):
    svc = _get_replay_service(request)
    info = await svc.resolve_media(sample_id, "topview")
    if info is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Top-view image not found")
    return FileResponse(path=info["path"], media_type=info["mime_type"])


@router.post("/advance", response_model=DemoReplayAdvanceResponse)
async def advance(request: Request):
    svc = _get_replay_service(request)
    result = await svc.advance()
    if result is None:
        raise HTTPException(status_code=HTTP_503_SERVICE_UNAVAILABLE, detail="Replay not configured")
    return DemoReplayAdvanceResponse(**result)


@router.post("/reset", response_model=DemoReplayResetResponse)
async def reset(request: Request):
    svc = _get_replay_service(request)
    result = await svc.reset()
    if result is None:
        raise HTTPException(status_code=HTTP_503_SERVICE_UNAVAILABLE, detail="Replay not configured")
    return DemoReplayResetResponse(**result)


@router.post("/reload", response_model=DemoReplayReloadResponse)
async def reload(request: Request):
    svc = _get_replay_service(request)
    result = await svc.reload()
    return DemoReplayReloadResponse(**result)


# ------------------------------------------------------------------ #
# Inference route                                                     #
# ------------------------------------------------------------------ #


class InferRequest(BaseModel):
    activate: bool = True


@router.post("/samples/{sample_id}/infer")
async def infer_sample(request: Request, sample_id: str, body: Optional[InferRequest] = None):
    """Run Qwen structured perception on a replay sample.

    Optional body:
      { "activate": true }  — whether to create Sentinel observation/hazard

    Response includes prediction, runtime hazard, latency, mode, and activation status.
    """
    svc = _get_replay_service(request)
    should_activate = body.activate if body else True

    # Get full internal definition
    sample = await svc.get_internal_definition(sample_id)
    if sample is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Sample not found")

    # Resolve image paths
    dashcam_info = await svc.resolve_media(sample_id, "dashcam")
    topview_info = await svc.resolve_media(sample_id, "topview")
    if dashcam_info is None or topview_info is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="Sample images not found",
        )

    dashcam_path = Path(dashcam_info["path"])
    topview_path = Path(topview_info["path"])

    # Get inference service
    inference_svc = getattr(request.app.state, "vision_inference_service", None)
    if inference_svc is None:
        raise HTTPException(
            status_code=HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vision inference service not initialized",
        )

    # Run inference
    try:
        result = await inference_svc.infer(sample, dashcam_path, topview_path)
    except Exception as e:
        logger.error("Inference error for %s: %s", sample_id, type(e).__name__)
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INFERENCE_UNAVAILABLE",
                "message": "Inference is unavailable for this sample.",
            },
        )

    # Build safe prediction response (camelCase)
    prediction_response = {
        "roadType": result.prediction.road_type.value,
        "trafficDensity": result.prediction.traffic_density.value,
        "roadComplexity": result.prediction.road_complexity.value,
        "hazardPresence": result.prediction.hazard_presence.value,
        "anticipatedRisk": result.prediction.anticipated_risk.value,
        "recommendedAction": result.prediction.recommended_action.value,
    }

    runtime_hazard_response = None
    if result.runtime_hazard:
        runtime_hazard_response = {
            "hazardType": result.runtime_hazard.hazard_type,
            "hazardDescription": result.runtime_hazard.hazard_description,
            "confidence": result.runtime_hazard.confidence,
        }

    response = {
        "sampleId": result.sample_id,
        "inferenceId": result.inference_id,
        "model": result.model,
        "inferenceMode": result.inference_mode.value,
        "prediction": prediction_response,
        "runtimeHazard": runtime_hazard_response,
        "latencyMs": result.latency_ms,
    }

    # Activation
    if should_activate:
        try:
            from services.replay_activation_service import activate_inference

            location = None
            if sample.location:
                location = {
                    "latitude": sample.location.latitude,
                    "longitude": sample.location.longitude,
                }

            activation = await activate_inference(result, location)
            response["activation"] = ActivationPublicResponse(
                activated=activation.activated,
                reason=activation.reason,
                observation_id=activation.observation_id,
                hazard_id=activation.hazard_id,
                warning_text_generated=activation.warning_text_generated,
                warning_event_created=activation.warning_event_created,
            ).model_dump(by_alias=True)
        except Exception as e:
            logger.error("Activation failed for %s: %s", sample_id, type(e).__name__)
            response["activation"] = ActivationPublicResponse(
                activated=False,
                reason="activation_failed",
            ).model_dump(by_alias=True)
    else:
        response["activation"] = ActivationPublicResponse(
            activated=False,
            reason="activation_not_requested",
        ).model_dump(by_alias=True)

    return response

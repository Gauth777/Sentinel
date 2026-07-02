"""Evidence route for Sentinel replay provenance.

Endpoint:
  GET /api/sentinel/demo-replay/samples/{sample_id}/evidence
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from starlette.status import HTTP_404_NOT_FOUND, HTTP_503_SERVICE_UNAVAILABLE

from models.demo_replay import DemoReplayEvidenceResponse
from services.demo_replay_service import DemoReplayService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sentinel/demo-replay")


def _get_replay_service(request: Request) -> DemoReplayService:
    svc = getattr(request.app.state, "demo_replay_service", None)
    if svc is None:
        raise HTTPException(status_code=HTTP_503_SERVICE_UNAVAILABLE, detail="Replay service not initialized")
    return svc  # type: ignore[return-value]


@router.get("/samples/{sample_id}/evidence", response_model=DemoReplayEvidenceResponse)
async def get_evidence(request: Request, sample_id: str):
    """Return research provenance evidence for a replay sample.

    Includes:
      - sourceSampleId: original dataset sample ID (from source_map)
      - expectedLabels: ground truth labels from the manifest
      - sourceMapAvailable: whether source_map.example.json was loaded
    """
    svc = _get_replay_service(request)

    # Verify sample exists
    sample = await svc.get_sample(sample_id)
    if sample is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Sample not found")

    # Load source map
    source_map = await svc.get_source_map()
    source_sample_id = None
    if source_map:
        source_sample_id = source_map.get(sample_id)

    # Load expected labels
    expected_labels = await svc.get_expected_labels(sample_id)

    # Load cached prediction
    cached_pred = await svc.get_cached_prediction(sample_id)
    if cached_pred:
        actual_prediction = cached_pred.get("prediction")
        model_name = cached_pred.get("model", "Qwen2.5-VL-7B-Instruct")
        inference_mode = "cached_qwen"
    else:
        actual_prediction = None
        model_name = "Qwen2.5-VL-7B-Instruct"
        inference_mode = "cached_qwen"

    from utils.evidence_helper import compute_evidence_data
    evidence_data = compute_evidence_data(
        sample_id=sample_id,
        source_sample_id=source_sample_id,
        expected_labels=expected_labels,
        actual_prediction=actual_prediction,
        inference_mode=inference_mode,
        model=model_name
    )

    return DemoReplayEvidenceResponse(**evidence_data)

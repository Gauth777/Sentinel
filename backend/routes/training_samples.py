"""Training sample routes for Sentinel VLM dataset engine.

All routes under /api/sentinel/training-samples.
"""
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.status import HTTP_201_CREATED, HTTP_404_NOT_FOUND, HTTP_409_CONFLICT

from models.training_samples import (
    DatasetStatus,
    FeedbackStatus,
    TrainingFeedbackCreate,
    TrainingSample,
    TrainingSampleCreate,
    TrainingSampleListResponse,
    TrainingStatsResponse,
)
from services.training_sample_service import (
    DuplicateError,
    ServiceError,
    TrainingSampleService,
)

router = APIRouter(prefix="/sentinel/training-samples")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_service(request: Request) -> TrainingSampleService:
    svc = request.app.state.training_sample_service
    if svc is None:
        raise HTTPException(status_code=503, detail="Training sample service unavailable")
    return svc


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.post("", status_code=HTTP_201_CREATED, response_model=TrainingSample)
async def create_sample(
    request: Request,
    data: TrainingSampleCreate,
):
    svc = _get_service(request)
    try:
        sample = await svc.create_sample(data)
    except DuplicateError as e:
        raise HTTPException(status_code=HTTP_409_CONFLICT, detail=str(e))
    except ServiceError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return sample.to_api_dict()


@router.get("", response_model=TrainingSampleListResponse)
async def list_samples(
    request: Request,
    status: Optional[DatasetStatus] = Query(default=None),
    feedback_status: Optional[FeedbackStatus] = Query(default=None),
    hazard_id: Optional[str] = Query(default=None),
    source_vehicle_id: Optional[str] = Query(default=None),
    model_name: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
):
    svc = _get_service(request)
    try:
        result = await svc.list_samples(
            status=status,
            feedback_status=feedback_status,
            hazard_id=hazard_id,
            source_vehicle_id=source_vehicle_id,
            model_name=model_name,
            limit=limit,
            skip=skip,
        )
    except ServiceError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return result


@router.get("/stats", response_model=TrainingStatsResponse)
async def get_stats(request: Request):
    svc = _get_service(request)
    try:
        return await svc.get_stats()
    except ServiceError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/export")
async def export_dataset(
    request: Request,
    model_name: Optional[str] = Query(default=None),
    road_type: Optional[str] = Query(default=None),
    hazard_presence: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=None, ge=1, le=10000),
):
    svc = _get_service(request)
    try:
        lines = await svc.export_verified(
            model_name=model_name,
            road_type=road_type,
            hazard_presence=hazard_presence,
            limit=limit,
        )
    except ServiceError as e:
        raise HTTPException(status_code=503, detail=str(e))

    def _stream():
        for line in lines:
            yield json.dumps(line, ensure_ascii=False, sort_keys=False) + "\n"

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"sentinel_verified_dataset_{timestamp}.jsonl"

    return StreamingResponse(
        _stream(),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/{sample_id}", response_model=TrainingSample)
async def get_sample(request: Request, sample_id: str):
    svc = _get_service(request)
    try:
        sample = await svc.get_sample(sample_id)
    except ServiceError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if sample is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Sample not found")
    return sample.to_api_dict()


@router.post("/{sample_id}/feedback", response_model=TrainingSample)
async def submit_feedback(
    request: Request,
    sample_id: str,
    data: TrainingFeedbackCreate,
):
    svc = _get_service(request)
    try:
        sample = await svc.submit_feedback(sample_id, data)
    except ServiceError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if sample is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Sample not found")
    return sample.to_api_dict()

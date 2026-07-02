"""Graph verification route for Sentinel replay provenance.

Endpoint:
  GET /api/sentinel/demo-replay/graph-verify?hazardId=...
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.status import HTTP_422_UNPROCESSABLE_CONTENT, HTTP_503_SERVICE_UNAVAILABLE

from models.demo_replay import DemoReplayGraphVerifyResponse
from services.perception_graph_service import PerceptionGraphService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sentinel/demo-replay")


def _get_perception_graph(request: Request) -> PerceptionGraphService:
    svc = getattr(request.app.state, "perception_graph_service", None)
    if svc is None:
        raise HTTPException(
            status_code=HTTP_503_SERVICE_UNAVAILABLE,
            detail="Perception graph service not initialized",
        )
    return svc  # type: ignore[return-value]


@router.get("/graph-verify", response_model=DemoReplayGraphVerifyResponse)
async def graph_verify(
    request: Request,
    hazard_id: str = Query(alias="hazardId", min_length=1),
):
    """Verify that a hazard and its provenance chain exist in the perception graph.

    Returns verification status for the hazard node, observation node,
    SUPPORTS relationship, and any warning nodes.
    """
    svc = _get_perception_graph(request)

    try:
        graph = await svc.build_graph(hazard_id=hazard_id, limit=1)
    except ValueError as e:
        raise HTTPException(status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    except Exception as e:
        logger.error("Graph verification failed: %s", type(e).__name__)
        return DemoReplayGraphVerifyResponse(
            hazard_id=hazard_id,
            graph_backend="unknown",
            summary="Graph query failed",
        )

    mode = graph.get("mode", "memory")
    backend_label = "neo4j" if mode == "neo4j" else "in_memory"

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # Check for hazard node
    hazard_found = any(
        n.get("id") == hazard_id and n.get("type") == "Hazard"
        for n in nodes
    )

    # Check for observation nodes
    observation_found = any(
        n.get("type") == "Observation"
        for n in nodes
    )

    # Check for SUPPORTS relationship linking observation to hazard
    relationship_found = any(
        e.get("type") == "SUPPORTS" and e.get("target") == hazard_id
        for e in edges
    )

    # Check for warning nodes
    warning_found = any(
        n.get("type") == "Warning"
        for n in nodes
    )

    # Build summary text
    if backend_label == "neo4j":
        summary = "Persisted in Neo4j AuraDB"
    else:
        summary = "Stored in in-memory fallback"

    if not hazard_found:
        summary += " — hazard node not found"

    return DemoReplayGraphVerifyResponse(
        hazard_id=hazard_id,
        graph_backend=backend_label,
        hazard_node_found=hazard_found,
        observation_node_found=observation_found,
        relationship_found=relationship_found,
        warning_node_found=warning_found,
        summary=summary,
    )

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
    observation_id: str = Query(alias="observationId", min_length=1),
):
    """Verify that a hazard and its provenance chain exist in the perception graph.

    Checks exact hazard, observation and SUPPORTS relationship.
    """
    svc = _get_perception_graph(request)

    try:
        graph = await svc.build_graph(hazard_id=hazard_id, limit=1)
    except ValueError as e:
        raise HTTPException(status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    except Exception as e:
        logger.error("Graph verification failed: %s", type(e).__name__)
        return DemoReplayGraphVerifyResponse(
            graph_backend="unknown",
            hazard_id=hazard_id,
            observation_id=observation_id,
            exact_hazard_found=False,
            exact_observation_found=False,
            exact_supports_relationship_found=False,
            node_count=0,
            edge_count=0,
            relationship_types=[],
            warning_node_found=False,
            warning_count=0,
            verified=False,
            error="Graph query failed",
            # Legacy fields
            hazard_node_found=False,
            observation_node_found=False,
            relationship_found=False,
            summary="Graph query failed",
        )

    mode = graph.get("mode", "unknown")
    backend_label = "neo4j" if mode == "neo4j" else "memory" if mode == "memory" else "unknown"

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # Exact checks
    exact_hazard_found = any(
        n.get("id") == hazard_id and n.get("type") == "Hazard"
        for n in nodes
    )
    exact_observation_found = any(
        n.get("id") == observation_id and n.get("type") == "Observation"
        for n in nodes
    )
    exact_supports_relationship_found = any(
        e.get("type") == "SUPPORTS" and e.get("source") == observation_id and e.get("target") == hazard_id
        for e in edges
    )

    verified = exact_hazard_found and exact_observation_found and exact_supports_relationship_found

    warning_node_found = any(n.get("type") == "Warning" for n in nodes)
    warning_count = sum(1 for n in nodes if n.get("type") == "Warning")

    node_count = len(nodes)
    edge_count = len(edges)
    relationship_types = list(sorted(set(e.get("type") for e in edges if e.get("type"))))

    # Build summary text
    if backend_label == "neo4j":
        if verified:
            summary = "Persisted in Neo4j"
        else:
            summary = "Verification failed — missing exact IDs"
    elif backend_label == "memory":
        summary = "Stored in in-memory fallback"
    else:
        summary = "Graph query failed"

    return DemoReplayGraphVerifyResponse(
        graph_backend=backend_label,
        hazard_id=hazard_id,
        observation_id=observation_id,
        exact_hazard_found=exact_hazard_found,
        exact_observation_found=exact_observation_found,
        exact_supports_relationship_found=exact_supports_relationship_found,
        node_count=node_count,
        edge_count=edge_count,
        relationship_types=relationship_types,
        warning_node_found=warning_node_found,
        warning_count=warning_count,
        verified=verified,
        error=None,
        # Legacy fields
        hazard_node_found=exact_hazard_found,
        observation_node_found=exact_observation_found,
        relationship_found=exact_supports_relationship_found,
        summary=summary,
    )

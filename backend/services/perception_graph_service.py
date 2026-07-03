"""
PerceptionGraphService — isolated provenance graph backend for Sentinel.

Supports Neo4j (async driver, lazy import) and a dedicated in-memory fallback.
All data is scoped to scenario_id = 'sentinel-demo' and labeled with
:SentinelPerception so that reset_demo_data never deletes non-demo data.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from utils.geo import haversine_meters

logger = logging.getLogger(__name__)

SCENARIO_ID = "sentinel-demo"

_TYPE_PRIORITY = {
    "Hazard": 0,
    "Vehicle": 1,
    "Observation": 2,
    "RoadSegment": 3,
    "Warning": 4,
}


# ---------------------------------------------------------------------------
# Normalized response helper
# ---------------------------------------------------------------------------

def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _normalize_response(
    mode: str, hazard_id: Optional[str], nodes: Dict[str, Dict], edges: Dict[str, Dict]
) -> Dict[str, Any]:
    node_list = []
    for n in sorted(nodes.values(), key=lambda x: (_TYPE_PRIORITY.get(x["type"], 99), x["id"])):
        node_copy = dict(n)
        node_copy["properties"] = dict(n.get("properties", {}))
        node_list.append(node_copy)

    edge_list = []
    for e in sorted(edges.values(), key=lambda x: (x["type"], x["source"], x["target"])):
        edge_copy = dict(e)
        edge_copy["properties"] = dict(e.get("properties", {}))
        edge_list.append(edge_copy)

    counts = {
        "Vehicle": 0,
        "Observation": 0,
        "Hazard": 0,
        "RoadSegment": 0,
        "Warning": 0,
    }
    for n in node_list:
        if n["type"] in counts:
            counts[n["type"]] += 1

    focus = None
    if hazard_id and hazard_id in nodes:
        hazard_props = nodes[hazard_id].get("properties", {})
        source_count = hazard_props.get("sourceCount", 0)
        confidence = hazard_props.get("confidence", 60)
        warning_count = sum(
            1
            for e in edge_list
            if e["type"] == "TRIGGERED_WARNING" and e["source"] == hazard_id
        )
        focus = {
            "hazardId": hazard_id,
            "sourceCount": source_count,
            "confidence": confidence,
            "warningCount": warning_count,
        }

    summary = {
        "nodeCount": len(node_list),
        "edgeCount": len(edge_list),
        "vehicleCount": counts["Vehicle"],
        "observationCount": counts["Observation"],
        "hazardCount": counts["Hazard"],
        "roadSegmentCount": counts["RoadSegment"],
        "warningCount": counts["Warning"],
        "focus": dict(focus) if focus else None,
    }

    timeline: List[Dict[str, Any]] = []
    for e in edge_list:
        ts = 0.0
        desc = ""
        if e["type"] == "OBSERVED":
            ts = nodes.get(e["target"], {}).get("properties", {}).get("timestamp", 0.0)
            desc = f"Vehicle {e['source']} observed {e['target']}"
        elif e["type"] == "SUPPORTS":
            ts = nodes.get(e["source"], {}).get("properties", {}).get("timestamp", 0.0)
            desc = f"Observation {e['source']} supports hazard {e['target']}"
        elif e["type"] == "TRIGGERED_WARNING":
            ts = nodes.get(e["target"], {}).get("properties", {}).get("timestamp", 0.0)
            desc = f"Hazard {e['source']} triggered warning {e['target']}"
        elif e["type"] == "DELIVERED_TO":
            ts = nodes.get(e["source"], {}).get("properties", {}).get("timestamp", 0.0)
            desc = f"Warning {e['source']} delivered to vehicle {e['target']}"
        elif e["type"] == "ON_ROAD":
            ts = 0.0
            desc = f"Hazard {e['source']} on road {e['target']}"
        elif e["type"] == "APPROACHING":
            ts = 0.0
            desc = f"Vehicle {e['source']} approaching road {e['target']}"
        timeline.append(
            {
                "eventId": e["id"],
                "timestamp": ts,
                "type": e["type"],
                "description": desc,
            }
        )

    timeline.sort(key=lambda x: (x["timestamp"], x["eventId"]))

    return {
        "mode": mode,
        "generatedAt": _now(),
        "focusHazardId": hazard_id,
        "nodes": node_list,
        "edges": edge_list,
        "summary": summary,
        "timeline": timeline,
    }


# ---------------------------------------------------------------------------
# In-memory backend (fully isolated, no MongoDB, no server imports)
# ---------------------------------------------------------------------------

class _InMemoryGraphBackend:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._edges: Dict[str, Dict[str, Any]] = {}

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        pass

    def _merge_node_sync(
        self, node_id: str, node_type: str, label: str, properties: Dict[str, Any]
    ) -> None:
        if node_id in self._nodes:
            existing = self._nodes[node_id]
            if existing.get("type") != node_type:
                raise ValueError(
                    f"Node {node_id} already exists as type {existing['type']}; "
                    f"cannot merge as {node_type}"
                )
            existing["label"] = label
            existing["properties"].update(properties)
        else:
            self._nodes[node_id] = {
                "id": node_id,
                "type": node_type,
                "label": label,
                "scenarioId": SCENARIO_ID,
                "properties": dict(properties),
            }

    def _merge_edge_sync(
        self,
        edge_id: str,
        edge_type: str,
        source: str,
        target: str,
        properties: Dict[str, Any],
    ) -> None:
        if edge_id not in self._edges:
            self._edges[edge_id] = {
                "id": edge_id,
                "type": edge_type,
                "source": source,
                "target": target,
                "scenarioId": SCENARIO_ID,
                "properties": dict(properties),
            }

    def _update_hazard_stats(self, hazard_id: str) -> None:
        hz_node = self._nodes.get(hazard_id)
        if not hz_node or hz_node.get("type") != "Hazard" or hz_node.get("scenarioId") != SCENARIO_ID:
            return

        source_vehicles: Set[str] = set()
        for e in self._edges.values():
            if e["type"] != "SUPPORTS" or e["target"] != hazard_id or e.get("scenarioId") != SCENARIO_ID:
                continue
            obs_id = e["source"]
            obs_node = self._nodes.get(obs_id)
            if not obs_node or obs_node.get("type") != "Observation" or obs_node.get("scenarioId") != SCENARIO_ID:
                continue
            for e2 in self._edges.values():
                if e2["type"] != "OBSERVED" or e2["target"] != obs_id or e2.get("scenarioId") != SCENARIO_ID:
                    continue
                v_id = e2["source"]
                v_node = self._nodes.get(v_id)
                if not v_node or v_node.get("type") != "Vehicle" or v_node.get("scenarioId") != SCENARIO_ID:
                    continue
                source_vehicles.add(v_id)

        source_count = len(source_vehicles)
        if source_count == 1:
            confidence = 60
        elif source_count == 2:
            confidence = 80
        else:
            confidence = 100 if source_count >= 3 else 60

        self._nodes[hazard_id]["properties"]["sourceCount"] = source_count
        self._nodes[hazard_id]["properties"]["confidence"] = confidence

    async def record_observation(
        self,
        observation_id: str,
        vehicle_id: str,
        vehicle_label: str,
        hazard_id: str,
        hazard_type: str,
        hazard_label: str,
        road_segment_id: str,
        road_segment_name: str,
        timestamp: Optional[float] = None,
    ) -> None:
        ts = timestamp or 0.0

        async with self._lock:
            # Idempotency / validation before any mutation
            if observation_id in self._nodes:
                existing_obs_edges = [
                    e
                    for e in self._edges.values()
                    if e["type"] == "OBSERVED" and e["target"] == observation_id
                ]
                if existing_obs_edges:
                    existing_vehicle = existing_obs_edges[0]["source"]
                    if existing_vehicle != vehicle_id:
                        raise ValueError(
                            f"Observation {observation_id} already owned by vehicle "
                            f"{existing_vehicle}; cannot reassign to vehicle {vehicle_id}"
                        )
                    existing_sup_edges = [
                        e
                        for e in self._edges.values()
                        if e["type"] == "SUPPORTS" and e["source"] == observation_id
                    ]
                    if existing_sup_edges and existing_sup_edges[0]["target"] != hazard_id:
                        existing_hazard = existing_sup_edges[0]["target"]
                        raise ValueError(
                            f"Observation {observation_id} already supports hazard "
                            f"{existing_hazard}; cannot reassign to hazard {hazard_id}"
                        )
                return

            # Atomic mutation
            self._merge_node_sync(vehicle_id, "Vehicle", vehicle_label or vehicle_id, {})
            self._merge_node_sync(
                observation_id,
                "Observation",
                f"Observation {observation_id}",
                {"type": hazard_type, "timestamp": ts},
            )
            self._merge_node_sync(
                hazard_id, "Hazard", hazard_label or hazard_id, {"type": hazard_type}
            )
            self._merge_node_sync(
                road_segment_id,
                "RoadSegment",
                road_segment_name or road_segment_id,
                {},
            )

            self._merge_edge_sync(
                f"OBSERVED:{vehicle_id}:{observation_id}", "OBSERVED", vehicle_id, observation_id, {}
            )
            self._merge_edge_sync(
                f"SUPPORTS:{observation_id}:{hazard_id}", "SUPPORTS", observation_id, hazard_id, {}
            )
            self._merge_edge_sync(
                f"ON_ROAD:{hazard_id}:{road_segment_id}", "ON_ROAD", hazard_id, road_segment_id, {}
            )
            self._merge_edge_sync(
                f"APPROACHING:{vehicle_id}:{road_segment_id}",
                "APPROACHING",
                vehicle_id,
                road_segment_id,
                {},
            )

            self._update_hazard_stats(hazard_id)

    async def record_warning(
        self,
        warning_id: str,
        hazard_id: str,
        vehicle_id: str,
        warning_text: str,
        language: str,
        road_segment_id: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> None:
        ts = timestamp or 0.0

        async with self._lock:
            # 2. EXISTING-NODE-ONLY SEMANTICS
            hz_node = self._nodes.get(hazard_id)
            if not hz_node or hz_node.get("type") != "Hazard" or hz_node.get("scenarioId") != SCENARIO_ID:
                raise ValueError(f"Hazard node {hazard_id} does not exist or has incorrect type/scenario")

            v_node = self._nodes.get(vehicle_id)
            if not v_node or v_node.get("type") != "Vehicle" or v_node.get("scenarioId") != SCENARIO_ID:
                raise ValueError(f"Vehicle node {vehicle_id} does not exist or has incorrect type/scenario")

            if road_segment_id:
                r_node = self._nodes.get(road_segment_id)
                if not r_node or r_node.get("type") != "RoadSegment" or r_node.get("scenarioId") != SCENARIO_ID:
                    raise ValueError(f"RoadSegment node {road_segment_id} does not exist or has incorrect type/scenario")

            # 3. IMMUTABLE IDEMPOTENCY & RELATIONSHIP INTEGRITY
            existing_warn = self._nodes.get(warning_id)
            if existing_warn:
                if existing_warn.get("type") != "Warning" or existing_warn.get("scenarioId") != SCENARIO_ID:
                    raise ValueError(f"Warning node {warning_id} has incorrect type/scenario")

                props = existing_warn.get("properties", {})
                if props.get("text") != warning_text or props.get("language") != language:
                    raise ValueError("Warning properties conflict with existing warning node")

                if props.get("roadSegmentId") != road_segment_id:
                    raise ValueError("Warning road segment association conflicts with existing warning")

                if props.get("hazardId") != hazard_id or props.get("vehicleId") != vehicle_id:
                    raise ValueError("Warning ownership conflicts with existing warning node")

                # Verify exactly one TRIGGERED_WARNING relationship exists
                tw_edges = [
                    edge for edge in self._edges.values()
                    if edge["type"] == "TRIGGERED_WARNING" and edge["target"] == warning_id and edge.get("scenarioId") == SCENARIO_ID
                ]
                if len(tw_edges) != 1 or tw_edges[0]["source"] != hazard_id:
                    raise ValueError("Warning must have exactly one matching TRIGGERED_WARNING relationship")

                # Verify exactly one DELIVERED_TO relationship exists
                dt_edges = [
                    edge for edge in self._edges.values()
                    if edge["type"] == "DELIVERED_TO" and edge["source"] == warning_id and edge.get("scenarioId") == SCENARIO_ID
                ]
                if len(dt_edges) != 1 or dt_edges[0]["target"] != vehicle_id:
                    raise ValueError("Warning must have exactly one matching DELIVERED_TO relationship")

                # Verify exactly one APPROACHING relationship exists if roadSegmentId is present
                if road_segment_id:
                    app_edges = [
                        edge for edge in self._edges.values()
                        if edge["type"] == "APPROACHING" and edge["source"] == vehicle_id and edge["target"] == road_segment_id and edge.get("scenarioId") == SCENARIO_ID
                    ]
                    if len(app_edges) != 1:
                        raise ValueError("Warning must have exactly one matching APPROACHING relationship")

                return

            # Create Warning and relationships
            self._merge_node_sync(
                warning_id,
                "Warning",
                f"Warning {warning_id}",
                {
                    "text": warning_text,
                    "language": language,
                    "timestamp": ts,
                    "roadSegmentId": road_segment_id,
                    "hazardId": hazard_id,
                    "vehicleId": vehicle_id,
                },
            )
            self._merge_edge_sync(
                f"TRIGGERED_WARNING:{hazard_id}:{warning_id}",
                "TRIGGERED_WARNING",
                hazard_id,
                warning_id,
                {},
            )
            self._merge_edge_sync(
                f"DELIVERED_TO:{warning_id}:{vehicle_id}",
                "DELIVERED_TO",
                warning_id,
                vehicle_id,
                {},
            )
            if road_segment_id:
                self._merge_edge_sync(
                    f"APPROACHING:{vehicle_id}:{road_segment_id}",
                    "APPROACHING",
                    vehicle_id,
                    road_segment_id,
                    {},
                )

    async def upsert_vehicle_approach(
        self,
        vehicle_id: str,
        vehicle_label: str,
        road_segment_id: str,
        road_segment_name: str,
    ) -> None:
        async with self._lock:
            if vehicle_id in self._nodes:
                node = self._nodes[vehicle_id]
                if node.get("type") != "Vehicle" or node.get("scenarioId") != SCENARIO_ID:
                    raise ValueError(f"Node {vehicle_id} exists but is not a Vehicle of scenario {SCENARIO_ID}")
            if road_segment_id in self._nodes:
                node = self._nodes[road_segment_id]
                if node.get("type") != "RoadSegment" or node.get("scenarioId") != SCENARIO_ID:
                    raise ValueError(f"Node {road_segment_id} exists but is not a RoadSegment of scenario {SCENARIO_ID}")

            self._merge_node_sync(vehicle_id, "Vehicle", vehicle_label, {})
            self._merge_node_sync(road_segment_id, "RoadSegment", road_segment_name, {})
            self._merge_edge_sync(
                f"APPROACHING:{vehicle_id}:{road_segment_id}",
                "APPROACHING",
                vehicle_id,
                road_segment_id,
                {},
            )

    async def get_warning_recipient_vehicle_ids(
        self,
        hazard_id: str,
        source_vehicle_id: str,
    ) -> List[str]:
        async with self._lock:
            hz_node = self._nodes.get(hazard_id)
            if not hz_node or hz_node.get("type") != "Hazard" or hz_node.get("scenarioId") != SCENARIO_ID:
                raise ValueError(f"Hazard node {hazard_id} does not exist or has incorrect type/scenario")

            props = hz_node.get("properties", {})
            if props.get("status", "active") != "active":
                return []

            valid_roads = []
            for edge in self._edges.values():
                if edge["type"] == "ON_ROAD" and edge["source"] == hazard_id and edge.get("scenarioId") == SCENARIO_ID:
                    target_id = edge["target"]
                    target_node = self._nodes.get(target_id)
                    if target_node and target_node.get("type") == "RoadSegment" and target_node.get("scenarioId") == SCENARIO_ID:
                        valid_roads.append(target_id)

            if len(valid_roads) == 0:
                raise ValueError(f"Hazard {hazard_id} has zero valid ON_ROAD relationships")
            if len(valid_roads) > 1:
                raise ValueError(f"Hazard {hazard_id} has multiple valid ON_ROAD relationships")

            road_segment_id = valid_roads[0]

            peers = set()
            for edge in self._edges.values():
                if edge["type"] == "APPROACHING" and edge["target"] == road_segment_id and edge.get("scenarioId") == SCENARIO_ID:
                    veh_id = edge["source"]
                    if veh_id == source_vehicle_id:
                        continue
                    veh_node = self._nodes.get(veh_id)
                    if veh_node and veh_node.get("type") == "Vehicle" and veh_node.get("scenarioId") == SCENARIO_ID:
                        peers.add(veh_id)

            return sorted(list(peers))

    def _build_hazard_component(self, hazard_id: str) -> Dict[str, Dict[str, Any]]:
        nodes: Dict[str, Dict[str, Any]] = {}
        edges: Dict[str, Dict[str, Any]] = {}

        if hazard_id not in self._nodes or self._nodes[hazard_id].get("type") != "Hazard":
            return {"nodes": nodes, "edges": edges}
        nodes[hazard_id] = self._nodes[hazard_id]

        # Observations supporting this hazard
        observer_vehicle_ids: Set[str] = set()
        for eid, e in self._edges.items():
            if e["type"] == "SUPPORTS" and e["target"] == hazard_id:
                edges[eid] = e
                obs_id = e["source"]
                if obs_id in self._nodes:
                    nodes[obs_id] = self._nodes[obs_id]
                for e2id, e2 in self._edges.items():
                    if e2["type"] == "OBSERVED" and e2["target"] == obs_id:
                        edges[e2id] = e2
                        v_id = e2["source"]
                        observer_vehicle_ids.add(v_id)
                        if v_id in self._nodes:
                            nodes[v_id] = self._nodes[v_id]

        # Road segment from this hazard
        road_segment_ids: Set[str] = set()
        for eid, e in self._edges.items():
            if e["type"] == "ON_ROAD" and e["source"] == hazard_id:
                edges[eid] = e
                r_id = e["target"]
                road_segment_ids.add(r_id)
                if r_id in self._nodes:
                    nodes[r_id] = self._nodes[r_id]

        # Approaching edges for observer vehicles to hazard road segments
        for eid, e in self._edges.items():
            if (
                e["type"] == "APPROACHING"
                and e["source"] in observer_vehicle_ids
                and e["target"] in road_segment_ids
            ):
                edges[eid] = e

        # Warnings triggered by this hazard
        warning_ids: Set[str] = set()
        for eid, e in self._edges.items():
            if e["type"] == "TRIGGERED_WARNING" and e["source"] == hazard_id:
                edges[eid] = e
                w_id = e["target"]
                warning_ids.add(w_id)
                if w_id in self._nodes:
                    nodes[w_id] = self._nodes[w_id]

        # Recipient vehicles from warnings
        recipient_vehicle_ids: Set[str] = set()
        for eid, e in self._edges.items():
            if e["type"] == "DELIVERED_TO" and e["source"] in warning_ids:
                edges[eid] = e
                v_id = e["target"]
                recipient_vehicle_ids.add(v_id)
                if v_id in self._nodes:
                    nodes[v_id] = self._nodes[v_id]

        # Approaching edges for recipient vehicles to hazard road segments
        for eid, e in self._edges.items():
            if (
                e["type"] == "APPROACHING"
                and e["source"] in recipient_vehicle_ids
                and e["target"] in road_segment_ids
            ):
                edges[eid] = e

        return {"nodes": nodes, "edges": edges}

    async def build_graph(
        self, hazard_id: Optional[str] = None, limit: int = 25
    ) -> Dict[str, Any]:
        async with self._lock:
            if hazard_id:
                if (
                    hazard_id not in self._nodes
                    or self._nodes[hazard_id].get("type") != "Hazard"
                ):
                    return _normalize_response("memory", hazard_id, {}, {})
                comp = self._build_hazard_component(hazard_id)
                return _normalize_response(
                    "memory", hazard_id, comp["nodes"], comp["edges"]
                )
            else:
                all_hazards = sorted(
                    nid for nid, n in self._nodes.items() if n.get("type") == "Hazard"
                )[:limit]
                nodes: Dict[str, Dict[str, Any]] = {}
                edges: Dict[str, Dict[str, Any]] = {}
                for hz in all_hazards:
                    comp = self._build_hazard_component(hz)
                    for nid, n in comp["nodes"].items():
                        nodes[nid] = n
                    for eid, e in comp["edges"].items():
                        edges[eid] = e
                return _normalize_response("memory", None, nodes, edges)

    async def list_hazards(self, limit: int = 100) -> List[dict]:
        async with self._lock:
            hazards = [
                n for n in self._nodes.values()
                if n.get("type") == "Hazard" and n.get("scenarioId") == SCENARIO_ID
            ]
            def sort_key(n):
                props = n.get("properties", {})
                updated_at = float(props.get("updated_at", 0.0))
                return (-updated_at, n["id"])

            hazards.sort(key=sort_key)
            result = []
            for h in hazards[:limit]:
                result.append(self._normalize_hazard_record_sync(h["id"]))
            return result

    async def reset_demo_data(self) -> None:
        async with self._lock:
            to_delete_nodes = {
                nid
                for nid, n in self._nodes.items()
                if n.get("scenarioId") == SCENARIO_ID
            }
            to_delete_edges = {
                eid
                for eid, e in self._edges.items()
                if e.get("scenarioId") == SCENARIO_ID
            }
            for nid in to_delete_nodes:
                del self._nodes[nid]
            for eid in to_delete_edges:
                del self._edges[eid]

    def _normalize_hazard_record_sync(self, hazard_id: str) -> dict:
        node = self._nodes[hazard_id]
        props = node.get("properties", {})

        segment_id = ""
        for e in self._edges.values():
            if e["type"] == "ON_ROAD" and e["source"] == hazard_id and e.get("scenarioId") == SCENARIO_ID:
                target_id = e["target"]
                target_node = self._nodes.get(target_id)
                if target_node and target_node.get("type") == "RoadSegment" and target_node.get("scenarioId") == SCENARIO_ID:
                    segment_id = target_id
                    break

        source_vehicles = set()
        for e in self._edges.values():
            if e["type"] != "SUPPORTS" or e["target"] != hazard_id or e.get("scenarioId") != SCENARIO_ID:
                continue
            obs_id = e["source"]
            obs_node = self._nodes.get(obs_id)
            if not obs_node or obs_node.get("type") != "Observation" or obs_node.get("scenarioId") != SCENARIO_ID:
                continue
            for e2 in self._edges.values():
                if e2["type"] != "OBSERVED" or e2["target"] != obs_id or e2.get("scenarioId") != SCENARIO_ID:
                    continue
                v_id = e2["source"]
                v_node = self._nodes.get(v_id)
                if not v_node or v_node.get("type") != "Vehicle" or v_node.get("scenarioId") != SCENARIO_ID:
                    continue
                source_vehicles.add(v_id)

        sorted_vehicles = sorted(list(source_vehicles))
        source_count = len(sorted_vehicles)

        confidence = props.get("confidence")
        if confidence is None:
            if source_count == 1:
                confidence = 60
            elif source_count == 2:
                confidence = 80
            else:
                confidence = 100 if source_count >= 3 else 60
        else:
            confidence = int(confidence)

        polygon = props.get("polygon", None)
        if polygon is not None:
            polygon = [dict(p) for p in polygon]

        return {
            "id": node["id"],
            "type": props.get("type", ""),
            "label": node["label"],
            "location": {
                "latitude": float(props.get("latitude", 0.0)),
                "longitude": float(props.get("longitude", 0.0)),
            },
            "segment_id": segment_id,
            "status": props.get("status", "active"),
            "created_at": float(props.get("created_at", 0.0)),
            "updated_at": float(props.get("updated_at", 0.0)),
            "sources": source_count,
            "source_vehicles": sorted_vehicles,
            "confidence": confidence,
            "confirmed": int(props.get("confirmed", 0)),
            "reportedIncorrect": int(props.get("reportedIncorrect", 0)),
            "distanceMeters": props.get("distanceMeters") if props.get("distanceMeters") is None else float(props.get("distanceMeters")),
            "direction": props.get("direction"),
            "recommendedAction": props.get("recommendedAction"),
            "risk": props.get("risk"),
            "visibilityState": props.get("visibilityState"),
            "sourceType": props.get("sourceType"),
            "routeRelevance": props.get("routeRelevance"),
            "polygon": polygon,
            "model": props.get("model"),
            "inferenceMode": props.get("inferenceMode"),
            "sampleId": props.get("sampleId"),
            "lastInferenceId": props.get("lastInferenceId"),
            "replayConfidence": props.get("replayConfidence") if props.get("replayConfidence") is None else float(props.get("replayConfidence")),
        }

    async def get_observation_hazard(self, observation_id: str) -> Optional[dict]:
        async with self._lock:
            obs = self._nodes.get(observation_id)
            if not obs or obs.get("type") != "Observation" or obs.get("scenarioId") != SCENARIO_ID:
                return None

            # Verify OBSERVED relationship from a Vehicle
            vehicle_id = None
            for e in self._edges.values():
                if e["type"] == "OBSERVED" and e["target"] == observation_id and e.get("scenarioId") == SCENARIO_ID:
                    vehicle_id = e["source"]
                    break
            if not vehicle_id:
                return None

            veh_node = self._nodes.get(vehicle_id)
            if not veh_node or veh_node.get("type") != "Vehicle" or veh_node.get("scenarioId") != SCENARIO_ID:
                return None

            hazard_id = None
            for e in self._edges.values():
                if e["type"] == "SUPPORTS" and e["source"] == observation_id and e.get("scenarioId") == SCENARIO_ID:
                    hazard_id = e["target"]
                    break

            if not hazard_id:
                return None

            hazard_node = self._nodes.get(hazard_id)
            if not hazard_node or hazard_node.get("type") != "Hazard" or hazard_node.get("scenarioId") != SCENARIO_ID:
                return None

            return self._normalize_hazard_record_sync(hazard_id)

    async def find_similar_active_hazard(
        self,
        hazard_type: str,
        latitude: float,
        longitude: float,
        road_segment_id: str,
        radius_m: float,
        min_updated_at: float,
    ) -> Optional[dict]:
        import math
        async with self._lock:
            candidates = []
            for nid, node in self._nodes.items():
                if node.get("type") != "Hazard" or node.get("scenarioId") != SCENARIO_ID:
                    continue
                props = node.get("properties", {})

                status = props.get("status")
                if status not in ("active", "resolved"):
                    continue

                if props.get("type") != hazard_type or status != "active":
                    continue

                try:
                    lat_val = props.get("latitude")
                    lon_val = props.get("longitude")
                    if lat_val is None or lon_val is None or isinstance(lat_val, bool) or isinstance(lon_val, bool):
                        continue
                    lat_f = float(lat_val)
                    lon_f = float(lon_val)
                    if not math.isfinite(lat_f) or not math.isfinite(lon_f):
                        continue
                    if lat_f < -90.0 or lat_f > 90.0 or lon_f < -180.0 or lon_f > 180.0:
                        continue
                except (ValueError, TypeError):
                    continue

                try:
                    upd_val = props.get("updated_at")
                    if upd_val is None or isinstance(upd_val, bool):
                        continue
                    upd_f = float(upd_val)
                    if not math.isfinite(upd_f) or upd_f < 0.0:
                        continue
                except (ValueError, TypeError):
                    continue

                if upd_f < min_updated_at:
                    continue

                has_road_connection = False
                for e in self._edges.values():
                    if e["type"] == "ON_ROAD" and e["source"] == nid and e["target"] == road_segment_id and e.get("scenarioId") == SCENARIO_ID:
                        has_road_connection = True
                        break
                if not has_road_connection:
                    continue

                dist = haversine_meters(latitude, longitude, lat_f, lon_f)
                if dist <= radius_m:
                    candidates.append((dist, upd_f, nid))

            if not candidates:
                return None

            candidates.sort(key=lambda x: (x[0], -x[1], x[2]))
            best_match = candidates[0]
            best_nid = best_match[2]
            best_dist = best_match[0]
            return {
                "hazard": self._normalize_hazard_record_sync(best_nid),
                "matchDistanceMeters": best_dist,
            }

    async def upsert_observation_and_hazard(
        self,
        *,
        observation_id: str,
        vehicle_id: str,
        vehicle_label: str,
        hazard_id: str,
        hazard_type: str,
        hazard_label: str,
        latitude: float,
        longitude: float,
        road_segment_id: str,
        road_segment_name: str,
        timestamp: float,
        hazard_fields: Optional[dict] = None,
    ) -> dict:
        import copy
        async with self._lock:
            obs_exists = observation_id in self._nodes
            is_idempotent_retry = False

            if obs_exists:
                obs_node = self._nodes[observation_id]
                if obs_node.get("type") != "Observation" or obs_node.get("scenarioId") != SCENARIO_ID:
                    raise ValueError(f"Observation {observation_id} exists but has wrong type or scenario")

                observed_edges = [
                    e for e in self._edges.values()
                    if e["type"] == "OBSERVED" and e["target"] == observation_id and e.get("scenarioId") == SCENARIO_ID
                ]
                if len(observed_edges) != 1:
                    raise ValueError(f"Observation {observation_id} must have exactly one scenario-scoped OBSERVED relationship")

                obs_vehicle = observed_edges[0]["source"]
                if obs_vehicle != vehicle_id:
                    raise ValueError(f"Observation {observation_id} is already linked to vehicle {obs_vehicle}")

                veh_node = self._nodes.get(obs_vehicle)
                if not veh_node or veh_node.get("type") != "Vehicle" or veh_node.get("scenarioId") != SCENARIO_ID:
                    raise ValueError(f"Vehicle {obs_vehicle} has wrong type or scenario")

                supports_edges = [
                    e for e in self._edges.values()
                    if e["type"] == "SUPPORTS" and e["source"] == observation_id and e.get("scenarioId") == SCENARIO_ID
                ]
                if len(supports_edges) != 1:
                    raise ValueError(f"Observation {observation_id} must have exactly one scenario-scoped SUPPORTS relationship")

                obs_hazard = supports_edges[0]["target"]
                if obs_hazard != hazard_id:
                    raise ValueError(f"Observation {observation_id} is already linked to hazard {obs_hazard}")

                hz_node = self._nodes.get(obs_hazard)
                if not hz_node or hz_node.get("type") != "Hazard" or hz_node.get("scenarioId") != SCENARIO_ID:
                    raise ValueError(f"Hazard {obs_hazard} has wrong type or scenario")

                valid_road_ids = []
                for e in self._edges.values():
                    if e["type"] == "ON_ROAD" and e["source"] == hazard_id:
                        if e.get("scenarioId") != SCENARIO_ID:
                            continue
                        target_id = e["target"]
                        target_node = self._nodes.get(target_id)
                        if target_node:
                            if target_node.get("type") != "RoadSegment" or target_node.get("scenarioId") != SCENARIO_ID:
                                continue
                            valid_road_ids.append(target_id)

                if len(valid_road_ids) != 1:
                    raise ValueError(f"Hazard {hazard_id} must have exactly one scenario-scoped ON_ROAD relationship")

                hz_road = valid_road_ids[0]
                if hz_road != road_segment_id:
                    raise ValueError(f"Hazard {hazard_id} is connected to road segment {hz_road}; expected {road_segment_id}")

                # Verify hazard type
                props = hz_node.get("properties", {})
                if props.get("type") != hazard_type:
                    raise ValueError(f"Hazard {hazard_id} exists with type {props.get('type')}; expected {hazard_type}")

                is_idempotent_retry = True

            if is_idempotent_retry:
                return {
                    "hazard": self._normalize_hazard_record_sync(hazard_id),
                    "hazardCreated": False,
                    "observationCreated": False,
                }

            hazard_exists = hazard_id in self._nodes
            if hazard_exists:
                hz_node = self._nodes[hazard_id]
                props = hz_node.get("properties", {})
                if props.get("type") != hazard_type:
                    raise ValueError(f"Hazard {hazard_id} exists with type {props.get('type')}; cannot upsert as {hazard_type}")

                valid_road_ids = []
                for e in self._edges.values():
                    if e["type"] == "ON_ROAD" and e["source"] == hazard_id:
                        if e.get("scenarioId") != SCENARIO_ID:
                            continue
                        target_id = e["target"]
                        target_node = self._nodes.get(target_id)
                        if target_node:
                            if target_node.get("type") != "RoadSegment" or target_node.get("scenarioId") != SCENARIO_ID:
                                continue
                            valid_road_ids.append(target_id)

                if len(valid_road_ids) != 1:
                    raise ValueError(f"Hazard {hazard_id} must have exactly one scenario-scoped ON_ROAD relationship")

                hz_road = valid_road_ids[0]
                if hz_road != road_segment_id:
                    raise ValueError(f"Hazard {hazard_id} is already connected to road segment {hz_road}")

                if hazard_fields and "risk" in hazard_fields and hazard_fields["risk"] is not None:
                    existing_risk = props.get("risk")
                    new_risk = hazard_fields["risk"]
                    if existing_risk in RISK_LEVELS:
                        if RISK_LEVELS[new_risk] < RISK_LEVELS[existing_risk]:
                            raise ValueError(f"Cannot decrease risk level from {existing_risk} to {new_risk}")

                if hazard_fields and "status" in hazard_fields and hazard_fields["status"] == "active":
                    if props.get("status") == "resolved":
                        raise ValueError("Cannot change hazard status from resolved back to active")

            nodes_snapshot = copy.deepcopy(self._nodes)
            edges_snapshot = copy.deepcopy(self._edges)

            try:
                hazard_created = not hazard_exists
                observation_created = not obs_exists

                self._merge_node_sync(vehicle_id, "Vehicle", vehicle_label, {})
                self._merge_node_sync(road_segment_id, "RoadSegment", road_segment_name, {})

                if hazard_created:
                    hz_props = {
                        "type": hazard_type,
                        "latitude": latitude,
                        "longitude": longitude,
                        "status": "active",
                        "created_at": timestamp,
                        "updated_at": timestamp,
                        "confirmed": 0,
                        "reportedIncorrect": 0,
                    }
                else:
                    hz_props = dict(self._nodes[hazard_id].get("properties", {}))
                    hz_props["updated_at"] = timestamp

                if hazard_fields:
                    for k, v in hazard_fields.items():
                        if v is not None:
                            hz_props[k] = v

                self._merge_node_sync(hazard_id, "Hazard", hazard_label, hz_props)

                self._merge_node_sync(observation_id, "Observation", f"Observation {observation_id}", {
                    "type": hazard_type,
                    "timestamp": timestamp
                })

                self._merge_edge_sync(f"OBSERVED:{vehicle_id}:{observation_id}", "OBSERVED", vehicle_id, observation_id, {})
                self._merge_edge_sync(f"SUPPORTS:{observation_id}:{hazard_id}", "SUPPORTS", observation_id, hazard_id, {})
                self._merge_edge_sync(f"ON_ROAD:{hazard_id}:{road_segment_id}", "ON_ROAD", hazard_id, road_segment_id, {})
                self._merge_edge_sync(f"APPROACHING:{vehicle_id}:{road_segment_id}", "APPROACHING", vehicle_id, road_segment_id, {})

                self._update_hazard_stats(hazard_id)

                return {
                    "hazard": self._normalize_hazard_record_sync(hazard_id),
                    "hazardCreated": hazard_created,
                    "observationCreated": observation_created,
                }
            except Exception:
                self._nodes = nodes_snapshot
                self._edges = edges_snapshot
                raise


class _Neo4jGraphBackend:
    def __init__(self) -> None:
        self._driver: Any = None
        self._database = "neo4j"

    def _load_config(self) -> None:
        self._uri = os.environ.get("NEO4J_URI", "")
        self._user = os.environ.get("NEO4J_USERNAME", "")
        self._password = os.environ.get("NEO4J_PASSWORD", "")
        self._database = os.environ.get("NEO4J_DATABASE", "neo4j")

    async def initialize(self) -> None:
        if self._driver is not None:
            return
        self._load_config()
        if not self._uri or not self._user or not self._password:
            raise RuntimeError("Neo4j credentials incomplete")
        try:
            import neo4j
            self._driver = neo4j.AsyncGraphDatabase.driver(
                self._uri, auth=(self._user, self._password)
            )
            await self._driver.verify_connectivity()
            await self._create_constraints()
        except Exception as exc:
            logger.warning(
                "Neo4j initialization failed: %s",
                type(exc).__name__,
            )

            driver = self._driver
            self._driver = None

            if driver is not None:
                try:
                    await driver.close()
                except Exception:
                    pass

            raise RuntimeError(
                f"Neo4j initialization failed: {type(exc).__name__}"
            ) from None

    async def close(self) -> None:
        if self._driver is not None:
            try:
                await self._driver.close()
            except Exception:
                pass
            self._driver = None

    async def _create_constraints(self) -> None:
        if self._driver is None:
            return
        constraints = [
            ("sentinel_vehicle_identity", "Vehicle"),
            ("sentinel_observation_identity", "Observation"),
            ("sentinel_hazard_identity", "Hazard"),
            ("sentinel_roadsegment_identity", "RoadSegment"),
            ("sentinel_warning_identity", "Warning"),
        ]
        async with self._driver.session(database=self._database) as session:
            for name, label in constraints:
                cypher = (
                    f"CREATE CONSTRAINT {name} IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE (n.scenario_id, n.id) IS UNIQUE"
                )
                try:
                    await session.run(cypher)
                except Exception as exc:
                    logger.warning(
                        f"Constraint {name} creation failed: {type(exc).__name__}"
                    )
                    raise RuntimeError(
                        f"Neo4j constraint failed: {name}"
                    ) from None

    async def _run(self, query: str, **params) -> None:
        if self._driver is None:
            raise RuntimeError("Neo4j driver not initialized")
        async with self._driver.session(database=self._database) as session:
            try:
                await session.run(query, **params)
            except Exception as e:
                raise RuntimeError(f"Neo4j query failed: {type(e).__name__}") from None

    async def _run_read(self, query: str, **params) -> List[Dict[str, Any]]:
        if self._driver is None:
            raise RuntimeError("Neo4j driver not initialized")
        records: List[Dict[str, Any]] = []
        async with self._driver.session(database=self._database) as session:
            try:
                result = await session.run(query, **params)
                async for record in result:
                    records.append(record.data())
                return records
            except Exception as e:
                raise RuntimeError(f"Neo4j read failed: {type(e).__name__}") from None

    async def record_observation(
        self,
        observation_id: str,
        vehicle_id: str,
        vehicle_label: str,
        hazard_id: str,
        hazard_type: str,
        hazard_label: str,
        road_segment_id: str,
        road_segment_name: str,
        timestamp: Optional[float] = None,
    ) -> None:
        ts = timestamp or 0.0

        check_query = """
        MATCH (o:SentinelPerception:Observation {id: $obs_id, scenario_id: $scenario_id})
        OPTIONAL MATCH (v:SentinelPerception:Vehicle {scenario_id: $scenario_id})-[obs:OBSERVED {scenario_id: $scenario_id}]->(o)
        OPTIONAL MATCH (o)-[sup:SUPPORTS {scenario_id: $scenario_id}]->(h:SentinelPerception:Hazard {scenario_id: $scenario_id})
        RETURN obs IS NOT NULL as has_observed, v.id as existing_vehicle_id,
               sup IS NOT NULL as has_supports, h.id as existing_hazard_id
        """
        result = await self._run_read(
            check_query, obs_id=observation_id, scenario_id=SCENARIO_ID
        )
        if result:
            record = result[0]
            if record.get("has_observed"):
                existing_v = record.get("existing_vehicle_id")
                if existing_v and existing_v != vehicle_id:
                    raise ValueError(
                        f"Observation {observation_id} already owned by vehicle "
                        f"{existing_v}; cannot reassign to vehicle {vehicle_id}"
                    )
                if record.get("has_supports"):
                    existing_h = record.get("existing_hazard_id")
                    if existing_h and existing_h != hazard_id:
                        raise ValueError(
                            f"Observation {observation_id} already supports hazard "
                            f"{existing_h}; cannot reassign to hazard {hazard_id}"
                        )
                return

        query = """
        MERGE (v:SentinelPerception:Vehicle {id: $v_id, scenario_id: $scenario_id})
          ON CREATE SET v.label = $v_label
          ON MATCH SET v.label = $v_label
        MERGE (o:SentinelPerception:Observation {id: $obs_id, scenario_id: $scenario_id})
          ON CREATE SET o.type = $obs_type, o.label = $obs_label, o.observedSecondsAgo = 0, o.timestamp = $timestamp
          ON MATCH SET o.type = $obs_type, o.label = $obs_label, o.observedSecondsAgo = 0, o.timestamp = $timestamp
        MERGE (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
          ON CREATE SET h.type = $h_type, h.label = $h_label
          ON MATCH SET h.type = $h_type, h.label = $h_label
        MERGE (r:SentinelPerception:RoadSegment {id: $r_id, scenario_id: $scenario_id})
          ON CREATE SET r.name = $r_name
          ON MATCH SET r.name = $r_name
        MERGE (v)-[:OBSERVED {scenario_id: $scenario_id}]->(o)
        MERGE (o)-[:SUPPORTS {scenario_id: $scenario_id}]->(h)
        MERGE (h)-[:ON_ROAD {scenario_id: $scenario_id}]->(r)
        MERGE (v)-[:APPROACHING {scenario_id: $scenario_id}]->(r)
        """
        await self._run(
            query,
            v_id=vehicle_id,
            v_label=vehicle_label or vehicle_id,
            obs_id=observation_id,
            obs_type=hazard_type or "",
            obs_label=f"Observation {observation_id}",
            h_id=hazard_id,
            h_type=hazard_type or "",
            h_label=hazard_label or hazard_id,
            r_id=road_segment_id,
            r_name=road_segment_name or road_segment_id,
            timestamp=ts,
            scenario_id=SCENARIO_ID,
        )

        update_query = """
        MATCH (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
        OPTIONAL MATCH (obs:SentinelPerception:Observation {scenario_id: $scenario_id})-[:SUPPORTS {scenario_id: $scenario_id}]->(h)
        OPTIONAL MATCH (v:SentinelPerception:Vehicle {scenario_id: $scenario_id})-[:OBSERVED {scenario_id: $scenario_id}]->(obs)
        WITH h, count(DISTINCT v) as sourceCount
        SET h.sourceCount = sourceCount,
            h.confidence = CASE
                WHEN sourceCount = 1 THEN 60
                WHEN sourceCount = 2 THEN 80
                ELSE 100
            END
        """
        await self._run(update_query, h_id=hazard_id, scenario_id=SCENARIO_ID)

    async def record_warning(
        self,
        warning_id: str,
        hazard_id: str,
        vehicle_id: str,
        warning_text: str,
        language: str,
        road_segment_id: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> None:
        if self._driver is None:
            raise RuntimeError("Neo4j driver not initialized")

        async with self._driver.session(database=self._database) as session:
            await session.execute_write(
                self._record_warning_tx,
                warning_id=warning_id,
                hazard_id=hazard_id,
                vehicle_id=vehicle_id,
                warning_text=warning_text,
                language=language,
                road_segment_id=road_segment_id,
                timestamp=timestamp,
            )

    async def _record_warning_tx(
        self,
        tx,
        warning_id: str,
        hazard_id: str,
        vehicle_id: str,
        warning_text: str,
        language: str,
        road_segment_id: Optional[str],
        timestamp: Optional[float],
    ) -> None:
        ts = timestamp or 0.0

        # 1. Validate Hazard exists (directly labelled and scenario-scoped)
        hz_check = """
        MATCH (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
        RETURN count(h) > 0 as exists
        """
        res = await tx.run(hz_check, h_id=hazard_id, scenario_id=SCENARIO_ID)
        rec = await res.single()
        if not rec or not rec["exists"]:
            raise ValueError(f"Hazard node {hazard_id} does not exist or has incorrect type/scenario")

        # 2. Validate Vehicle exists (directly labelled and scenario-scoped)
        v_check = """
        MATCH (v:SentinelPerception:Vehicle {id: $v_id, scenario_id: $scenario_id})
        RETURN count(v) > 0 as exists
        """
        res = await tx.run(v_check, v_id=vehicle_id, scenario_id=SCENARIO_ID)
        rec = await res.single()
        if not rec or not rec["exists"]:
            raise ValueError(f"Vehicle node {vehicle_id} does not exist or has incorrect type/scenario")

        # 3. Validate RoadSegment exists (directly labelled and scenario-scoped)
        if road_segment_id:
            r_check = """
            MATCH (r:SentinelPerception:RoadSegment {id: $r_id, scenario_id: $scenario_id})
            RETURN count(r) > 0 as exists
            """
            res = await tx.run(r_check, r_id=road_segment_id, scenario_id=SCENARIO_ID)
            rec = await res.single()
            if not rec or not rec["exists"]:
                raise ValueError(f"RoadSegment node {road_segment_id} does not exist or has incorrect type/scenario")

        # 4. First check whether the warning existed before this transaction
        warn_check = """
        MATCH (w:SentinelPerception:Warning {id: $w_id, scenario_id: $scenario_id})
        RETURN w.text as text, w.language as language, w.roadSegmentId as roadSegmentId,
               w.hazardId as hazardId, w.vehicleId as vehicleId
        """
        res_warn = await tx.run(warn_check, w_id=warning_id, scenario_id=SCENARIO_ID)
        warn_rec = await res_warn.single()

        if warn_rec:
            # 5. Enforce property validation checks on retry
            if warn_rec["text"] != warning_text or warn_rec["language"] != language:
                raise ValueError("Warning properties conflict with existing warning node")
            if warn_rec["roadSegmentId"] != road_segment_id:
                raise ValueError("Warning road segment association conflicts with existing warning")
            if warn_rec["hazardId"] != hazard_id or warn_rec["vehicleId"] != vehicle_id:
                raise ValueError("Warning ownership conflicts with existing warning node")

            # 6. Verify exactly one TRIGGERED_WARNING relationship exists
            match_tw = """
            MATCH (w:SentinelPerception:Warning {id: $w_id, scenario_id: $scenario_id})
            OPTIONAL MATCH (h:SentinelPerception:Hazard {scenario_id: $scenario_id})-[r:TRIGGERED_WARNING {scenario_id: $scenario_id}]->(w)
            RETURN count(r) as rel_count, collect(h.id) as hazard_ids
            """
            res_tw = await tx.run(match_tw, w_id=warning_id, scenario_id=SCENARIO_ID)
            rec_tw = await res_tw.single()
            if not rec_tw or rec_tw["rel_count"] != 1 or rec_tw["hazard_ids"] != [hazard_id]:
                raise ValueError("Warning must have exactly one matching TRIGGERED_WARNING relationship")

            # Verify exactly one DELIVERED_TO relationship exists
            match_dt = """
            MATCH (w:SentinelPerception:Warning {id: $w_id, scenario_id: $scenario_id})
            OPTIONAL MATCH (w)-[r:DELIVERED_TO {scenario_id: $scenario_id}]->(v:SentinelPerception:Vehicle {scenario_id: $scenario_id})
            RETURN count(r) as rel_count, collect(v.id) as vehicle_ids
            """
            res_dt = await tx.run(match_dt, w_id=warning_id, scenario_id=SCENARIO_ID)
            rec_dt = await res_dt.single()
            if not rec_dt or rec_dt["rel_count"] != 1 or rec_dt["vehicle_ids"] != [vehicle_id]:
                raise ValueError("Warning must have exactly one matching DELIVERED_TO relationship")

            # Verify exactly one APPROACHING relationship exists if roadSegmentId is present
            if road_segment_id:
                match_app = """
                MATCH (v:SentinelPerception:Vehicle {id: $v_id, scenario_id: $scenario_id})
                MATCH (seg:SentinelPerception:RoadSegment {id: $r_id, scenario_id: $scenario_id})
                OPTIONAL MATCH (v)-[r:APPROACHING {scenario_id: $scenario_id}]->(seg)
                RETURN count(r) as rel_count
                """
                res_app = await tx.run(match_app, v_id=vehicle_id, r_id=road_segment_id, scenario_id=SCENARIO_ID)
                rec_app = await res_app.single()
                if not rec_app or rec_app["rel_count"] != 1:
                    raise ValueError("Warning must have exactly one matching APPROACHING relationship")

            return

        # 7. Merge the scenario-scoped Warning node
        merge_query = """
        MERGE (w:SentinelPerception:Warning {id: $w_id, scenario_id: $scenario_id})
          ON CREATE SET w.text = $text,
                        w.language = $lang,
                        w.timestamp = $timestamp,
                        w.roadSegmentId = $road_segment_id,
                        w.hazardId = $h_id,
                        w.vehicleId = $v_id
        RETURN w.text as text, w.language as language, w.roadSegmentId as roadSegmentId,
               w.hazardId as hazardId, w.vehicleId as vehicleId
        """
        res_warn = await tx.run(
            merge_query,
            w_id=warning_id,
            text=warning_text,
            lang=language,
            timestamp=ts,
            road_segment_id=road_segment_id,
            h_id=hazard_id,
            v_id=vehicle_id,
            scenario_id=SCENARIO_ID
        )
        warn_rec = await res_warn.single()
        if not warn_rec:
            raise RuntimeError("Warning merge failed to return the node properties")

        # Verify returned properties (concurrency verification)
        if warn_rec["text"] != warning_text or warn_rec["language"] != language:
            raise ValueError("Warning properties conflict with existing warning node")
        if warn_rec["roadSegmentId"] != road_segment_id:
            raise ValueError("Warning road segment association conflicts with existing warning")
        if warn_rec["hazardId"] != hazard_id or warn_rec["vehicleId"] != vehicle_id:
            raise ValueError("Warning ownership conflicts with existing warning node")

        # 8. Merge relationships within the same transaction
        rel_query = """
        MATCH (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
        MATCH (v:SentinelPerception:Vehicle {id: $v_id, scenario_id: $scenario_id})
        MATCH (w:SentinelPerception:Warning {id: $w_id, scenario_id: $scenario_id})
        MERGE (h)-[:TRIGGERED_WARNING {scenario_id: $scenario_id}]->(w)
        MERGE (w)-[:DELIVERED_TO {scenario_id: $scenario_id}]->(v)
        """
        await tx.run(
            rel_query,
            h_id=hazard_id,
            v_id=vehicle_id,
            w_id=warning_id,
            scenario_id=SCENARIO_ID
        )

        if road_segment_id:
            app_query = """
            MATCH (v:SentinelPerception:Vehicle {id: $v_id, scenario_id: $scenario_id})
            MATCH (r:SentinelPerception:RoadSegment {id: $r_id, scenario_id: $scenario_id})
            MERGE (v)-[:APPROACHING {scenario_id: $scenario_id}]->(r)
            """
            await tx.run(
                app_query,
                v_id=vehicle_id,
                r_id=road_segment_id,
                scenario_id=SCENARIO_ID,
            )

    async def upsert_vehicle_approach(
        self,
        vehicle_id: str,
        vehicle_label: str,
        road_segment_id: str,
        road_segment_name: str,
    ) -> None:
        if self._driver is None:
            raise RuntimeError("Neo4j driver not initialized")

        async with self._driver.session(database=self._database) as session:
            await session.execute_write(
                self._upsert_vehicle_approach_tx,
                vehicle_id=vehicle_id,
                vehicle_label=vehicle_label,
                road_segment_id=road_segment_id,
                road_segment_name=road_segment_name,
            )

    async def _upsert_vehicle_approach_tx(
        self,
        tx,
        vehicle_id: str,
        vehicle_label: str,
        road_segment_id: str,
        road_segment_name: str,
    ) -> None:
        # Check vehicle ID conflicts among SentinelPerception nodes
        v_conflict_query = """
        MATCH (n:SentinelPerception {id: $v_id})
        RETURN labels(n) as labels, n.scenario_id as scenario_id
        """
        res_v = await tx.run(v_conflict_query, v_id=vehicle_id)
        async for rec in res_v:
            labels = rec["labels"] or []
            scenario_id = rec["scenario_id"]
            if "Vehicle" not in labels or scenario_id != SCENARIO_ID:
                raise ValueError(f"Node {vehicle_id} exists as a conflicting SentinelPerception node")

        # Check road segment ID conflicts among SentinelPerception nodes
        r_conflict_query = """
        MATCH (n:SentinelPerception {id: $r_id})
        RETURN labels(n) as labels, n.scenario_id as scenario_id
        """
        res_r = await tx.run(r_conflict_query, r_id=road_segment_id)
        async for rec in res_r:
            labels = rec["labels"] or []
            scenario_id = rec["scenario_id"]
            if "RoadSegment" not in labels or scenario_id != SCENARIO_ID:
                raise ValueError(f"Node {road_segment_id} exists as a conflicting SentinelPerception node")

        query = """
        MERGE (v:SentinelPerception:Vehicle {id: $v_id, scenario_id: $scenario_id})
          ON CREATE SET v.label = $v_label
          ON MATCH SET v.label = $v_label
        MERGE (r:SentinelPerception:RoadSegment {id: $r_id, scenario_id: $scenario_id})
          ON CREATE SET r.name = $r_name
          ON MATCH SET r.name = $r_name
        MERGE (v)-[:APPROACHING {scenario_id: $scenario_id}]->(r)
        """
        await tx.run(
            query,
            v_id=vehicle_id,
            v_label=vehicle_label,
            r_id=road_segment_id,
            r_name=road_segment_name,
            scenario_id=SCENARIO_ID,
        )

    async def get_warning_recipient_vehicle_ids(
        self,
        hazard_id: str,
        source_vehicle_id: str,
    ) -> List[str]:
        hz_query = """
        MATCH (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
        RETURN coalesce(h.status, 'active') as status
        """
        records = await self._run_read(hz_query, h_id=hazard_id, scenario_id=SCENARIO_ID)
        if not records:
            raise ValueError(f"Hazard node {hazard_id} does not exist or has incorrect type/scenario")

        status = records[0]["status"]
        if status != "active":
            return []

        road_query = """
        MATCH (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
        MATCH (h)-[r:ON_ROAD {scenario_id: $scenario_id}]->(seg:SentinelPerception:RoadSegment {scenario_id: $scenario_id})
        RETURN seg.id as road_id
        """
        records_roads = await self._run_read(road_query, h_id=hazard_id, scenario_id=SCENARIO_ID)

        if len(records_roads) == 0:
            raise ValueError(f"Hazard {hazard_id} has zero valid ON_ROAD relationships")
        if len(records_roads) > 1:
            raise ValueError(f"Hazard {hazard_id} has multiple valid ON_ROAD relationships")

        road_segment_id = records_roads[0]["road_id"]

        vehicles_query = """
        MATCH (r:SentinelPerception:RoadSegment {id: $r_id, scenario_id: $scenario_id})
        MATCH (v:SentinelPerception:Vehicle {scenario_id: $scenario_id})-[a:APPROACHING {scenario_id: $scenario_id}]->(r)
        WHERE v.id <> $source_vehicle_id
        RETURN DISTINCT v.id as vehicle_id
        """
        records_v = await self._run_read(vehicles_query, r_id=road_segment_id, source_vehicle_id=source_vehicle_id, scenario_id=SCENARIO_ID)
        vehicle_ids = [rec["vehicle_id"] for rec in records_v]
        return sorted(vehicle_ids)

    async def build_graph(
        self, hazard_id: Optional[str] = None, limit: int = 25
    ) -> Dict[str, Any]:
        if self._driver is None:
            raise RuntimeError("Neo4j driver not initialized")

        if hazard_id:
            cypher = """
            MATCH (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
            OPTIONAL MATCH (obs:SentinelPerception:Observation)-[sup:SUPPORTS {scenario_id: $scenario_id}]->(h)
            OPTIONAL MATCH (v:SentinelPerception:Vehicle)-[obs_rel:OBSERVED {scenario_id: $scenario_id}]->(obs)
            OPTIONAL MATCH (h)-[onr:ON_ROAD {scenario_id: $scenario_id}]->(r:SentinelPerception:RoadSegment)
            OPTIONAL MATCH (v)-[app1:APPROACHING {scenario_id: $scenario_id}]->(r)
            OPTIONAL MATCH (h)-[tw:TRIGGERED_WARNING {scenario_id: $scenario_id}]->(w:SentinelPerception:Warning)
            OPTIONAL MATCH (w)-[dt:DELIVERED_TO {scenario_id: $scenario_id}]->(rv:SentinelPerception:Vehicle)
            OPTIONAL MATCH (rv)-[app2:APPROACHING {scenario_id: $scenario_id}]->(r)
            WITH h, obs, v, r, w, rv, sup, obs_rel, onr, app1, tw, dt, app2
            RETURN [node IN collect(DISTINCT h) + collect(DISTINCT obs) + collect(DISTINCT v) + collect(DISTINCT r) + collect(DISTINCT w) + collect(DISTINCT rv) WHERE node IS NOT NULL] AS nodes,
                   [rel IN collect(DISTINCT sup) + collect(DISTINCT obs_rel) + collect(DISTINCT onr) + collect(DISTINCT app1) + collect(DISTINCT tw) + collect(DISTINCT dt) + collect(DISTINCT app2) WHERE rel IS NOT NULL] AS edges
            """
            params: Dict[str, Any] = {"h_id": hazard_id, "scenario_id": SCENARIO_ID}
        else:
            cypher = """
            MATCH (h:SentinelPerception:Hazard {scenario_id: $scenario_id})
            WITH h ORDER BY h.id LIMIT $limit
            OPTIONAL MATCH (obs:SentinelPerception:Observation)-[sup:SUPPORTS {scenario_id: $scenario_id}]->(h)
            OPTIONAL MATCH (v:SentinelPerception:Vehicle)-[obs_rel:OBSERVED {scenario_id: $scenario_id}]->(obs)
            OPTIONAL MATCH (h)-[onr:ON_ROAD {scenario_id: $scenario_id}]->(r:SentinelPerception:RoadSegment)
            OPTIONAL MATCH (v)-[app1:APPROACHING {scenario_id: $scenario_id}]->(r)
            OPTIONAL MATCH (h)-[tw:TRIGGERED_WARNING {scenario_id: $scenario_id}]->(w:SentinelPerception:Warning)
            OPTIONAL MATCH (w)-[dt:DELIVERED_TO {scenario_id: $scenario_id}]->(rv:SentinelPerception:Vehicle)
            OPTIONAL MATCH (rv)-[app2:APPROACHING {scenario_id: $scenario_id}]->(r)
            WITH h, obs, v, r, w, rv, sup, obs_rel, onr, app1, tw, dt, app2
            RETURN [node IN collect(DISTINCT h) + collect(DISTINCT obs) + collect(DISTINCT v) + collect(DISTINCT r) + collect(DISTINCT w) + collect(DISTINCT rv) WHERE node IS NOT NULL] AS nodes,
                   [rel IN collect(DISTINCT sup) + collect(DISTINCT obs_rel) + collect(DISTINCT onr) + collect(DISTINCT app1) + collect(DISTINCT tw) + collect(DISTINCT dt) + collect(DISTINCT app2) WHERE rel IS NOT NULL] AS edges
            """
            params = {"scenario_id": SCENARIO_ID, "limit": limit}

        async with self._driver.session(database=self._database) as session:
            try:
                result = await session.run(cypher, **params)
                record = await result.single()
                if not record:
                    return _normalize_response("neo4j", hazard_id, {}, {})

                nodes: Dict[str, Dict[str, Any]] = {}
                edges: Dict[str, Dict[str, Any]] = {}
                for node in record["nodes"]:
                    self._add_neo4j_node(node, nodes)
                for rel in record["edges"]:
                    self._add_neo4j_rel(rel, edges)

                return _normalize_response("neo4j", hazard_id, nodes, edges)
            except Exception as e:
                raise RuntimeError(f"Neo4j graph query failed: {type(e).__name__}") from None

    @staticmethod
    def _add_neo4j_node(node: Any, nodes: Dict[str, Dict[str, Any]]) -> None:
        nid = node["id"]
        if nid in nodes:
            return
        labels = list(node.labels) if hasattr(node, "labels") else []
        node_type = None
        for label in labels:
            if label in ("Vehicle", "Observation", "Hazard", "RoadSegment", "Warning"):
                node_type = label
                break
        if not node_type:
            return

        props: Dict[str, Any] = {}
        for key in node.keys():
            props[key] = node[key]
        props.pop("id", None)
        props.pop("scenario_id", None)

        nodes[nid] = {
            "id": nid,
            "type": node_type,
            "label": props.pop("label", node_type),
            "scenarioId": SCENARIO_ID,
            "properties": props,
        }

    @staticmethod
    def _add_neo4j_rel(rel: Any, edges: Dict[str, Dict[str, Any]]) -> None:
        eid = f"{rel.type}:{rel.start_node['id']}:{rel.end_node['id']}"
        if eid in edges:
            return
        edges[eid] = {
            "id": eid,
            "type": rel.type,
            "source": rel.start_node["id"],
            "target": rel.end_node["id"],
            "scenarioId": SCENARIO_ID,
            "properties": {},
        }

    async def list_hazards(self, limit: int = 100) -> List[dict]:
        if self._driver is None:
            raise RuntimeError("Neo4j driver not initialized")

        cypher = """
        MATCH (h:SentinelPerception:Hazard {scenario_id: $scenario_id})
        OPTIONAL MATCH (h)-[:ON_ROAD {scenario_id: $scenario_id}]->(r:SentinelPerception:RoadSegment {scenario_id: $scenario_id})
        OPTIONAL MATCH (v:SentinelPerception:Vehicle {scenario_id: $scenario_id})-[:OBSERVED {scenario_id: $scenario_id}]->
                       (o:SentinelPerception:Observation {scenario_id: $scenario_id})-[:SUPPORTS {scenario_id: $scenario_id}]->(h)
        WITH h, coalesce(r.id, '') as segment_id, collect(DISTINCT v.id) as source_vehicles
        RETURN h, segment_id, source_vehicles
        ORDER BY h.updated_at DESC, h.id ASC
        LIMIT $limit
        """
        records = await self._run_read(cypher, scenario_id=SCENARIO_ID, limit=limit)

        result = []
        for record in records:
            h_node = record["h"]
            segment_id = record["segment_id"]
            source_vehicles = sorted(record["source_vehicles"])
            source_count = len(source_vehicles)

            props = dict(h_node)
            confidence = props.get("confidence")
            if confidence is None:
                if source_count == 1:
                    confidence = 60
                elif source_count == 2:
                    confidence = 80
                else:
                    confidence = 100 if source_count >= 3 else 60
            else:
                confidence = int(confidence)
            polygon = props.get("polygon")
            if isinstance(polygon, str):
                import json
                try:
                    polygon = json.loads(polygon)
                except Exception:
                    polygon = None
            elif polygon is None:
                polygon = props.get("polygon_json")
                if isinstance(polygon, str):
                    import json
                    try:
                        polygon = json.loads(polygon)
                    except Exception:
                        polygon = None

            result.append({
                "id": props.get("id", ""),
                "type": props.get("type", ""),
                "label": props.get("label", ""),
                "location": {
                    "latitude": float(props.get("latitude", 0.0)),
                    "longitude": float(props.get("longitude", 0.0)),
                },
                "segment_id": segment_id,
                "status": props.get("status", "active"),
                "created_at": float(props.get("created_at", 0.0)),
                "updated_at": float(props.get("updated_at", 0.0)),
                "sources": source_count,
                "source_vehicles": source_vehicles,
                "confidence": confidence,
                "confirmed": int(props.get("confirmed", 0)),
                "reportedIncorrect": int(props.get("reportedIncorrect", 0)),
                "distanceMeters": props.get("distanceMeters") if props.get("distanceMeters") is None else float(props.get("distanceMeters")),
                "direction": props.get("direction"),
                "recommendedAction": props.get("recommendedAction"),
                "risk": props.get("risk"),
                "visibilityState": props.get("visibilityState"),
                "sourceType": props.get("sourceType"),
                "routeRelevance": props.get("routeRelevance"),
                "polygon": polygon,
                "model": props.get("model"),
                "inferenceMode": props.get("inferenceMode"),
                "sampleId": props.get("sampleId"),
                "lastInferenceId": props.get("lastInferenceId"),
                "replayConfidence": props.get("replayConfidence") if props.get("replayConfidence") is None else float(props.get("replayConfidence")),
            })
        return result

    async def reset_demo_data(self) -> None:
        query = "MATCH (n:SentinelPerception {scenario_id: $scenario_id}) DETACH DELETE n"
        await self._run(query, scenario_id=SCENARIO_ID)

    async def _get_normalized_hazard_neo4j(self, hazard_id: str) -> Optional[dict]:
        cypher = """
        MATCH (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
        OPTIONAL MATCH (h)-[:ON_ROAD {scenario_id: $scenario_id}]->(r:SentinelPerception:RoadSegment {scenario_id: $scenario_id})
        RETURN h, r.id as segment_id
        """
        records = await self._run_read(cypher, h_id=hazard_id, scenario_id=SCENARIO_ID)
        if not records:
            return None

        record = records[0]
        h_node = record["h"]
        segment_id = record["segment_id"] or ""

        props = dict(h_node)

        vehicles_cypher = """
        MATCH (v:SentinelPerception:Vehicle {scenario_id: $scenario_id})-[:OBSERVED {scenario_id: $scenario_id}]->
              (o:SentinelPerception:Observation {scenario_id: $scenario_id})-[:SUPPORTS {scenario_id: $scenario_id}]->
              (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
        RETURN DISTINCT v.id as vehicle_id
        """
        v_records = await self._run_read(vehicles_cypher, h_id=hazard_id, scenario_id=SCENARIO_ID)
        source_vehicles = sorted([r["vehicle_id"] for r in v_records])
        source_count = len(source_vehicles)

        confidence = props.get("confidence")
        if confidence is None:
            if source_count == 1:
                confidence = 60
            elif source_count == 2:
                confidence = 80
            else:
                confidence = 100 if source_count >= 3 else 60
        else:
            confidence = int(confidence)

        polygon = props.get("polygon")
        if isinstance(polygon, str):
            import json
            try:
                polygon = json.loads(polygon)
            except Exception:
                polygon = None
        elif polygon is None:
            polygon = props.get("polygon_json")
            if isinstance(polygon, str):
                import json
                try:
                    polygon = json.loads(polygon)
                except Exception:
                    polygon = None

        return {
            "id": hazard_id,
            "type": props.get("type", ""),
            "label": props.get("label", hazard_id),
            "location": {
                "latitude": float(props.get("latitude", 0.0)),
                "longitude": float(props.get("longitude", 0.0)),
            },
            "segment_id": segment_id,
            "status": props.get("status", "active"),
            "created_at": float(props.get("created_at", 0.0)),
            "updated_at": float(props.get("updated_at", 0.0)),
            "sources": source_count,
            "source_vehicles": source_vehicles,
            "confidence": confidence,
            "confirmed": int(props.get("confirmed", 0)),
            "reportedIncorrect": int(props.get("reportedIncorrect", 0)),
            "distanceMeters": props.get("distanceMeters") if props.get("distanceMeters") is None else float(props.get("distanceMeters")),
            "direction": props.get("direction"),
            "recommendedAction": props.get("recommendedAction"),
            "risk": props.get("risk"),
            "visibilityState": props.get("visibilityState"),
            "sourceType": props.get("sourceType"),
            "routeRelevance": props.get("routeRelevance"),
            "polygon": polygon,
            "model": props.get("model"),
            "inferenceMode": props.get("inferenceMode"),
            "sampleId": props.get("sampleId"),
            "lastInferenceId": props.get("lastInferenceId"),
            "replayConfidence": props.get("replayConfidence") if props.get("replayConfidence") is None else float(props.get("replayConfidence")),
        }

    async def get_observation_hazard(self, observation_id: str) -> Optional[dict]:
        cypher = """
        MATCH (v:SentinelPerception:Vehicle {scenario_id: $scenario_id})
              -[:OBSERVED {scenario_id: $scenario_id}]->
              (o:SentinelPerception:Observation {id: $obs_id, scenario_id: $scenario_id})
              -[:SUPPORTS {scenario_id: $scenario_id}]->
              (h:SentinelPerception:Hazard {scenario_id: $scenario_id})
        RETURN h.id as hazard_id
        """
        records = await self._run_read(cypher, obs_id=observation_id, scenario_id=SCENARIO_ID)
        if not records:
            return None
        hazard_id = records[0]["hazard_id"]
        return await self._get_normalized_hazard_neo4j(hazard_id)

    async def find_similar_active_hazard(
        self,
        hazard_type: str,
        latitude: float,
        longitude: float,
        road_segment_id: str,
        radius_m: float,
        min_updated_at: float,
    ) -> Optional[dict]:
        cypher = """
        MATCH (h:SentinelPerception:Hazard {scenario_id: $scenario_id})-[:ON_ROAD {scenario_id: $scenario_id}]->(r:SentinelPerception:RoadSegment {id: $road_segment_id, scenario_id: $scenario_id})
        WHERE h.type = $hazard_type
          AND h.status = 'active'
        WITH h,
             toFloatOrNull(h.latitude) AS h_lat,
             toFloatOrNull(h.longitude) AS h_lon,
             toFloatOrNull(h.updated_at) AS h_updated
        WHERE h_lat IS NOT NULL
          AND h_lon IS NOT NULL
          AND h_updated IS NOT NULL
          AND h_lat = h_lat
          AND h_lon = h_lon
          AND h_updated = h_updated
          AND h_lat >= -90.0 AND h_lat <= 90.0
          AND h_lon >= -180.0 AND h_lon <= 180.0
          AND h_updated >= 0.0
          AND h_updated < 1.0e308
          AND h_updated >= $min_updated_at
        WITH h, h_updated,
             point({longitude: h_lon, latitude: h_lat}) as h_pt,
             point({longitude: $longitude, latitude: $latitude}) as ref_pt
        WITH h, h_updated, point.distance(h_pt, ref_pt) as dist
        WHERE dist <= $radius_m
        RETURN h.id as hazard_id, dist
        ORDER BY dist ASC, h_updated DESC, h.id ASC
        LIMIT 1
        """
        records = await self._run_read(
            cypher,
            scenario_id=SCENARIO_ID,
            road_segment_id=road_segment_id,
            hazard_type=hazard_type,
            min_updated_at=min_updated_at,
            latitude=latitude,
            longitude=longitude,
            radius_m=radius_m
        )
        if not records:
            return None
        record = records[0]
        hazard_id = record["hazard_id"]
        dist = float(record["dist"])

        normalized = await self._get_normalized_hazard_neo4j(hazard_id)
        if not normalized:
            return None
        return {
            "hazard": normalized,
            "matchDistanceMeters": dist,
        }

    async def _upsert_tx(
        self,
        tx,
        observation_id: str,
        vehicle_id: str,
        vehicle_label: str,
        hazard_id: str,
        hazard_type: str,
        hazard_label: str,
        latitude: float,
        longitude: float,
        road_segment_id: str,
        road_segment_name: str,
        timestamp: float,
        hazard_fields: Optional[dict],
    ) -> dict:
        obs_query = """
        MATCH (o:SentinelPerception:Observation {id: $obs_id, scenario_id: $scenario_id})
        OPTIONAL MATCH (v:SentinelPerception:Vehicle {scenario_id: $scenario_id})-[:OBSERVED {scenario_id: $scenario_id}]->(o)
        OPTIONAL MATCH (o)-[:SUPPORTS {scenario_id: $scenario_id}]->(h:SentinelPerception:Hazard {scenario_id: $scenario_id})
        OPTIONAL MATCH (h)-[:ON_ROAD {scenario_id: $scenario_id}]->(r:SentinelPerception:RoadSegment {scenario_id: $scenario_id})
        RETURN count(o) > 0 as exists, collect(DISTINCT v.id) as vehicle_ids, collect(DISTINCT h.id) as hazard_ids, collect(DISTINCT r.id) as road_ids
        """
        res = await tx.run(obs_query, obs_id=observation_id, scenario_id=SCENARIO_ID)
        obs_record = await res.single()
        obs_exists = False
        is_idempotent = False
        if obs_record:
            obs_exists = obs_record["exists"]
            if obs_exists:
                raw_vids = obs_record["vehicle_ids"] or []
                raw_hids = obs_record["hazard_ids"] or []
                raw_rids = obs_record["road_ids"] or []

                vehicle_ids = [vid for vid in raw_vids if vid is not None]
                hazard_ids = [hid for hid in raw_hids if hid is not None]
                road_ids = [rid for rid in raw_rids if rid is not None]

                if not vehicle_ids:
                    raise ValueError(f"Observation {observation_id} is missing an OBSERVED relationship")
                if not hazard_ids:
                    raise ValueError(f"Observation {observation_id} is missing a SUPPORTS relationship")
                if len(vehicle_ids) > 1:
                    raise ValueError(f"Observation {observation_id} is linked to multiple vehicles: {vehicle_ids}")
                if len(hazard_ids) > 1:
                    raise ValueError(f"Observation {observation_id} is linked to multiple hazards: {hazard_ids}")
                if vehicle_ids != [vehicle_id]:
                    raise ValueError(f"Observation {observation_id} is already linked to vehicle {vehicle_ids[0]}")
                if hazard_ids != [hazard_id]:
                    raise ValueError(f"Observation {observation_id} is already linked to hazard {hazard_ids[0]}")
                if not road_ids:
                    raise ValueError(f"Hazard {hazard_id} is missing an ON_ROAD relationship")
                if len(road_ids) > 1:
                    raise ValueError(f"Hazard {hazard_id} is connected to multiple road segments: {road_ids}")
                if road_ids != [road_segment_id]:
                    raise ValueError(f"Hazard {hazard_id} is connected to road segment {road_ids[0]}; expected {road_segment_id}")

                is_idempotent = True

        if is_idempotent:
            import json
            hz_query = """
            MATCH (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
            RETURN h
            """
            res_hz = await tx.run(hz_query, h_id=hazard_id, scenario_id=SCENARIO_ID)
            hz_rec = await res_hz.single()
            if not hz_rec:
                raise ValueError(f"Hazard {hazard_id} not found")
            h_node = hz_rec["h"]
            props = dict(h_node)

            if props.get("type") != hazard_type:
                raise ValueError(f"Hazard {hazard_id} exists with type {props.get('type')}; expected {hazard_type}")

            v_query = """
            MATCH (v:SentinelPerception:Vehicle {scenario_id: $scenario_id})-[:OBSERVED {scenario_id: $scenario_id}]->
                  (o:SentinelPerception:Observation {scenario_id: $scenario_id})-[:SUPPORTS {scenario_id: $scenario_id}]->
                  (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
            RETURN DISTINCT v.id as vehicle_id
            """
            res_v = await tx.run(v_query, h_id=hazard_id, scenario_id=SCENARIO_ID)
            source_vehicles = []
            async for r in res_v:
                source_vehicles.append(r["vehicle_id"])
            source_vehicles.sort()
            source_count = len(source_vehicles)

            if source_count == 1:
                confidence = 60
            elif source_count == 2:
                confidence = 80
            else:
                confidence = 100 if source_count >= 3 else 60

            polygon = props.get("polygon")
            if isinstance(polygon, str):
                try:
                    polygon = json.loads(polygon)
                except Exception:
                    polygon = None
            elif polygon is None:
                polygon = props.get("polygon_json")
                if isinstance(polygon, str):
                    try:
                        polygon = json.loads(polygon)
                    except Exception:
                        polygon = None

            hazard_record = {
                "id": hazard_id,
                "type": props.get("type", ""),
                "label": props.get("label", hazard_id),
                "location": {
                    "longitude": float(props.get("longitude", 0.0)),
                    "latitude": float(props.get("latitude", 0.0)),
                },
                "segment_id": road_segment_id,
                "status": props.get("status", "active"),
                "created_at": float(props.get("created_at", 0.0)),
                "updated_at": float(props.get("updated_at", 0.0)),
                "sources": source_count,
                "source_vehicles": source_vehicles,
                "confidence": confidence,
                "confirmed": int(props.get("confirmed", 0)),
                "reportedIncorrect": int(props.get("reportedIncorrect", 0)),
                "distanceMeters": props.get("distanceMeters") if props.get("distanceMeters") is None else float(props.get("distanceMeters")),
                "direction": props.get("direction"),
                "recommendedAction": props.get("recommendedAction"),
                "risk": props.get("risk"),
                "visibilityState": props.get("visibilityState"),
                "sourceType": props.get("sourceType"),
                "routeRelevance": props.get("routeRelevance"),
                "polygon": polygon,
                "model": props.get("model"),
                "inferenceMode": props.get("inferenceMode"),
                "sampleId": props.get("sampleId"),
                "lastInferenceId": props.get("lastInferenceId"),
                "replayConfidence": props.get("replayConfidence") if props.get("replayConfidence") is None else float(props.get("replayConfidence")),
            }

            return {
                "hazard": hazard_record,
                "hazardCreated": False,
                "observationCreated": False,
            }

        hz_query = """
        MATCH (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
        OPTIONAL MATCH (h)-[r_on:ON_ROAD {scenario_id: $scenario_id}]->(r:SentinelPerception:RoadSegment {scenario_id: $scenario_id})
        RETURN h as hazard_node, collect(DISTINCT r.id) as road_ids
        """
        res = await tx.run(hz_query, h_id=hazard_id, scenario_id=SCENARIO_ID)
        hz_record = await res.single()
        hazard_exists = hz_record is not None and hz_record["hazard_node"] is not None
        if hazard_exists:
            h_node = hz_record["hazard_node"]
            road_ids = [rid for rid in hz_record["road_ids"] if rid is not None]

            if len(road_ids) != 1:
                raise ValueError(f"Hazard {hazard_id} must have exactly one scenario-scoped ON_ROAD relationship")

            road_id = road_ids[0]
            if road_id != road_segment_id:
                raise ValueError(f"Hazard {hazard_id} is already connected to road segment {road_id}")

            props = dict(h_node)
            if props.get("type") != hazard_type:
                raise ValueError(f"Hazard {hazard_id} exists with type {props.get('type')}; cannot upsert as {hazard_type}")

            if hazard_fields and "risk" in hazard_fields and hazard_fields["risk"] is not None:
                existing_risk = props.get("risk")
                new_risk = hazard_fields["risk"]
                if existing_risk in RISK_LEVELS:
                    if RISK_LEVELS[new_risk] < RISK_LEVELS[existing_risk]:
                        raise ValueError(f"Cannot decrease risk level from {existing_risk} to {new_risk}")

            if hazard_fields and "status" in hazard_fields and hazard_fields["status"] == "active":
                if props.get("status") == "resolved":
                    raise ValueError("Cannot change hazard status from resolved back to active")

        hazard_created = not hazard_exists
        observation_created = not obs_exists

        await tx.run(
            """
            MERGE (v:SentinelPerception:Vehicle {id: $v_id, scenario_id: $scenario_id})
            ON CREATE SET v.label = $v_label
            ON MATCH SET v.label = $v_label
            """,
            v_id=vehicle_id,
            v_label=vehicle_label or vehicle_id,
            scenario_id=SCENARIO_ID
        )

        await tx.run(
            """
            MERGE (r:SentinelPerception:RoadSegment {id: $r_id, scenario_id: $scenario_id})
            ON CREATE SET r.name = $r_name
            ON MATCH SET r.name = $r_name
            """,
            r_id=road_segment_id,
            r_name=road_segment_name or road_segment_id,
            scenario_id=SCENARIO_ID
        )

        import json
        hz_props = {}
        if hazard_fields:
            for k, v in hazard_fields.items():
                if v is not None:
                    if k == "polygon":
                        hz_props["polygon_json"] = json.dumps(v)
                    else:
                        hz_props[k] = v

        if hazard_created:
            hz_props.update({
                "type": hazard_type,
                "latitude": latitude,
                "longitude": longitude,
                "status": hz_props.get("status", "active"),
                "created_at": timestamp,
                "updated_at": timestamp,
                "confirmed": 0,
                "reportedIncorrect": 0,
            })
            await tx.run(
                """
                CREATE (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
                SET h += $props, h.label = $h_label
                """,
                h_id=hazard_id,
                props=hz_props,
                h_label=hazard_label or hazard_id,
                scenario_id=SCENARIO_ID
            )
        else:
            hz_props["updated_at"] = timestamp
            await tx.run(
                """
                MATCH (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
                SET h += $props, h.label = $h_label
                """,
                h_id=hazard_id,
                props=hz_props,
                h_label=hazard_label or hazard_id,
                scenario_id=SCENARIO_ID
            )

        await tx.run(
            """
            MERGE (o:SentinelPerception:Observation {id: $obs_id, scenario_id: $scenario_id})
            ON CREATE SET o.type = $obs_type, o.label = $obs_label, o.observedSecondsAgo = 0, o.timestamp = $timestamp
            ON MATCH SET o.type = $obs_type, o.label = $obs_label, o.observedSecondsAgo = 0, o.timestamp = $timestamp
            """,
            obs_id=observation_id,
            obs_type=hazard_type or "",
            obs_label=f"Observation {observation_id}",
            timestamp=timestamp,
            scenario_id=SCENARIO_ID
        )

        await tx.run(
            """
            MATCH (v:SentinelPerception:Vehicle {id: $v_id, scenario_id: $scenario_id})
            MATCH (o:SentinelPerception:Observation {id: $obs_id, scenario_id: $scenario_id})
            MATCH (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
            MATCH (r:SentinelPerception:RoadSegment {id: $r_id, scenario_id: $scenario_id})
            MERGE (v)-[:OBSERVED {scenario_id: $scenario_id}]->(o)
            MERGE (o)-[:SUPPORTS {scenario_id: $scenario_id}]->(h)
            MERGE (h)-[:ON_ROAD {scenario_id: $scenario_id}]->(r)
            MERGE (v)-[:APPROACHING {scenario_id: $scenario_id}]->(r)
            """,
            v_id=vehicle_id,
            obs_id=observation_id,
            h_id=hazard_id,
            r_id=road_segment_id,
            scenario_id=SCENARIO_ID
        )

        stats_query = """
        MATCH (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
        OPTIONAL MATCH (obs:SentinelPerception:Observation {scenario_id: $scenario_id})-[:SUPPORTS {scenario_id: $scenario_id}]->(h)
        OPTIONAL MATCH (v:SentinelPerception:Vehicle {scenario_id: $scenario_id})-[:OBSERVED {scenario_id: $scenario_id}]->(obs)
        WITH h, count(DISTINCT v) as sourceCount
        SET h.sourceCount = sourceCount,
            h.confidence = CASE
                WHEN sourceCount = 1 THEN 60
                WHEN sourceCount = 2 THEN 80
                ELSE 100
            END
        """
        await tx.run(stats_query, h_id=hazard_id, scenario_id=SCENARIO_ID)

        norm_query = """
        MATCH (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
        OPTIONAL MATCH (h)-[:ON_ROAD {scenario_id: $scenario_id}]->(r:SentinelPerception:RoadSegment {scenario_id: $scenario_id})
        RETURN h, r.id as segment_id
        """
        res = await tx.run(norm_query, h_id=hazard_id, scenario_id=SCENARIO_ID)
        record = await res.single()
        h_node = record["h"]
        segment_id = record["segment_id"] or ""
        props = dict(h_node)

        v_query = """
        MATCH (v:SentinelPerception:Vehicle {scenario_id: $scenario_id})-[:OBSERVED {scenario_id: $scenario_id}]->
              (o:SentinelPerception:Observation {scenario_id: $scenario_id})-[:SUPPORTS {scenario_id: $scenario_id}]->
              (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
        RETURN DISTINCT v.id as vehicle_id
        """
        res_v = await tx.run(v_query, h_id=hazard_id, scenario_id=SCENARIO_ID)
        source_vehicles = []
        async for r in res_v:
            source_vehicles.append(r["vehicle_id"])
        source_vehicles.sort()
        source_count = len(source_vehicles)

        if source_count == 1:
            confidence = 60
        elif source_count == 2:
            confidence = 80
        else:
            confidence = 100 if source_count >= 3 else 60

        polygon = props.get("polygon")
        if isinstance(polygon, str):
            try:
                polygon = json.loads(polygon)
            except Exception:
                polygon = None
        elif polygon is None:
            polygon = props.get("polygon_json")
            if isinstance(polygon, str):
                try:
                    polygon = json.loads(polygon)
                except Exception:
                    polygon = None

        hazard_record = {
            "id": hazard_id,
            "type": props.get("type", ""),
            "label": props.get("label", hazard_id),
            "location": {
                "latitude": float(props.get("latitude", 0.0)),
                "longitude": float(props.get("longitude", 0.0)),
            },
            "segment_id": segment_id,
            "status": props.get("status", "active"),
            "created_at": float(props.get("created_at", 0.0)),
            "updated_at": float(props.get("updated_at", 0.0)),
            "sources": source_count,
            "source_vehicles": source_vehicles,
            "confidence": confidence,
            "confirmed": int(props.get("confirmed", 0)),
            "reportedIncorrect": int(props.get("reportedIncorrect", 0)),
            "distanceMeters": props.get("distanceMeters") if props.get("distanceMeters") is None else float(props.get("distanceMeters")),
            "direction": props.get("direction"),
            "recommendedAction": props.get("recommendedAction"),
            "risk": props.get("risk"),
            "visibilityState": props.get("visibilityState"),
            "sourceType": props.get("sourceType"),
            "routeRelevance": props.get("routeRelevance"),
            "polygon": polygon,
            "model": props.get("model"),
            "inferenceMode": props.get("inferenceMode"),
            "sampleId": props.get("sampleId"),
            "lastInferenceId": props.get("lastInferenceId"),
            "replayConfidence": props.get("replayConfidence") if props.get("replayConfidence") is None else float(props.get("replayConfidence")),
        }

        return {
            "hazard": hazard_record,
            "hazardCreated": hazard_created,
            "observationCreated": observation_created,
        }

    async def upsert_observation_and_hazard(
        self,
        *,
        observation_id: str,
        vehicle_id: str,
        vehicle_label: str,
        hazard_id: str,
        hazard_type: str,
        hazard_label: str,
        latitude: float,
        longitude: float,
        road_segment_id: str,
        road_segment_name: str,
        timestamp: float,
        hazard_fields: Optional[dict] = None,
    ) -> dict:
        if self._driver is None:
            raise RuntimeError("Neo4j driver not initialized")

        async with self._driver.session(database=self._database) as session:
            return await session.execute_write(
                self._upsert_tx,
                observation_id=observation_id,
                vehicle_id=vehicle_id,
                vehicle_label=vehicle_label,
                hazard_id=hazard_id,
                hazard_type=hazard_type,
                hazard_label=hazard_label,
                latitude=latitude,
                longitude=longitude,
                road_segment_id=road_segment_id,
                road_segment_name=road_segment_name,
                timestamp=timestamp,
                hazard_fields=hazard_fields,
            )


# ---------------------------------------------------------------------------
# Public service (unified facade)
# ---------------------------------------------------------------------------

def _parse_bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() == "true"


RISK_LEVELS = {"low": 1, "medium": 2, "high": 3}

def _validate_input_str(val: str, name: str) -> None:
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"{name} must be a non-empty string")
def _validate_finite_float(val: float, name: str, min_val: Optional[float] = None, max_val: Optional[float] = None) -> float:
    if isinstance(val, bool):
        raise ValueError(f"{name} must be a number, not a boolean")
    try:
        f = float(val)
    except (ValueError, TypeError):
        raise ValueError(f"{name} must be a number")
    import math
    if not math.isfinite(f):
        raise ValueError(f"{name} must be a finite number")
    if min_val is not None and f < min_val:
        raise ValueError(f"{name} must be >= {min_val}")
    if max_val is not None and f > max_val:
        raise ValueError(f"{name} must be <= {max_val}")
    return f

WHITELIST = {
    "distanceMeters",
    "direction",
    "recommendedAction",
    "risk",
    "visibilityState",
    "sourceType",
    "routeRelevance",
    "polygon",
    "status",
    "model",
    "inferenceMode",
    "sampleId",
    "lastInferenceId",
    "replayConfidence",
    "confirmed",
    "reportedIncorrect",
}

def _validate_hazard_fields(fields: Optional[dict]) -> Optional[dict]:
    if fields is None:
        return None
    if not isinstance(fields, dict):
        raise ValueError("hazard_fields must be a dictionary")

    normalized = {}
    for k, v in fields.items():
        if k not in WHITELIST:
            raise ValueError(f"Unknown key in hazard_fields: {k}")
        if k in ("sourceCount", "sources", "source_vehicles", "confidence"):
            raise ValueError(f"Prohibited key in hazard_fields: {k}")
        if v is None:
            continue

        if k in ("direction", "recommendedAction", "model", "inferenceMode", "sampleId", "lastInferenceId", "risk", "visibilityState", "sourceType", "routeRelevance", "status"):
            if not isinstance(v, str):
                raise ValueError(f"{k} must be a string")
            if k == "risk" and v not in ("low", "medium", "high"):
                raise ValueError("risk must be low, medium, or high")
            if k == "visibilityState" and v not in ("visible", "hidden", "uncertain"):
                raise ValueError("visibilityState must be visible, hidden, or uncertain")
            if k == "sourceType" and v not in ("local_sensor", "shared_vehicle", "demo"):
                raise ValueError("sourceType must be local_sensor, shared_vehicle, or demo")
            if k == "routeRelevance" and v not in ("none", "low", "medium", "high"):
                raise ValueError("routeRelevance must be none, low, medium, or high")
            if k == "status" and v not in ("active", "resolved"):
                raise ValueError("status must be active or resolved")
            normalized[k] = v

        elif k in ("distanceMeters", "replayConfidence"):
            val_f = _validate_finite_float(v, k)
            normalized[k] = val_f

        elif k == "polygon":
            if not isinstance(v, list):
                raise ValueError("polygon must be a list of dicts")
            normalized_poly = []
            for pt in v:
                if not isinstance(pt, dict) or "latitude" not in pt or "longitude" not in pt:
                    raise ValueError("polygon points must contain latitude and longitude")
                lat_f = _validate_finite_float(pt["latitude"], "polygon point latitude", -90, 90)
                lon_f = _validate_finite_float(pt["longitude"], "polygon point longitude", -180, 180)
                normalized_poly.append({
                    "latitude": lat_f,
                    "longitude": lon_f
                })
            normalized[k] = normalized_poly

        elif k in ("confirmed", "reportedIncorrect"):
            if isinstance(v, bool):
                raise ValueError(f"{k} must be an integer, not a boolean")
            try:
                val_i = int(v)
            except (ValueError, TypeError):
                raise ValueError(f"{k} must be an integer")
            normalized[k] = val_i

    return normalized
class PerceptionGraphService:
    def __init__(self) -> None:
        self._memory = _InMemoryGraphBackend()
        self._neo4j = _Neo4jGraphBackend()
        self._strict = _parse_bool_env("SENTINEL_NEO4J_STRICT", False)
        self._neo4j_enabled = _parse_bool_env("NEO4J_ENABLED", False)
        self._mode = "memory"
        self._neo4j_connected = False
        self._memory_connected = False

    async def initialize(self) -> None:
        self._strict = _parse_bool_env("SENTINEL_NEO4J_STRICT", False)
        self._neo4j_enabled = _parse_bool_env("NEO4J_ENABLED", False)

        self._neo4j_connected = False
        self._memory_connected = False

        if self._strict:
            if not self._neo4j_enabled:
                raise RuntimeError("Neo4j is disabled under strict mode configuration")
            try:
                await self._neo4j.initialize()
                self._mode = "neo4j"
                self._neo4j_connected = True
            except Exception:
                self._neo4j_connected = False
                raise RuntimeError(
                    "Neo4j initialization failed in strict mode"
                ) from None
        else:
            if self._neo4j_enabled:
                try:
                    await self._neo4j.initialize()
                    self._mode = "neo4j"
                    self._neo4j_connected = True
                    return
                except Exception:
                    self._neo4j_connected = False
            await self._memory.initialize()
            self._mode = "memory"
            self._memory_connected = True

    async def close(self) -> None:
        try:
            await self._neo4j.close()
        except Exception:
            pass
        try:
            await self._memory.close()
        except Exception:
            pass
        self._mode = "memory"
        self._neo4j_connected = False
        self._memory_connected = False

    def get_backend_status(self) -> dict:
        connected = False
        if self._mode == "neo4j":
            connected = self._neo4j_connected
        elif self._mode == "memory":
            connected = self._memory_connected
        return {
            "mode": self._mode,
            "strict": self._strict,
            "neo4jEnabled": self._neo4j_enabled,
            "connected": connected,
        }

    async def _execute(self, neo4j_method, memory_method, *args, **kwargs):
        if self._strict:
            if self._mode != "neo4j" or not self._neo4j_connected:
                raise RuntimeError("Active mode is not neo4j or neo4j is not connected under strict mode configuration")
            try:
                return await neo4j_method(*args, **kwargs)
            except ValueError:
                raise
            except Exception:
                self._neo4j_connected = False
                raise RuntimeError("Neo4j operation failed in strict mode") from None
        else:
            if self._mode == "neo4j":
                try:
                    return await neo4j_method(*args, **kwargs)
                except ValueError:
                    raise
                except Exception as e:
                    logger.warning(
                        f"Neo4j operation failed ({type(e).__name__}), falling back to memory"
                    )
                    self._neo4j_connected = False
                    await self._memory.initialize()
                    self._memory_connected = True
                    self._mode = "memory"
            return await memory_method(*args, **kwargs)

    async def record_observation(
        self,
        observation_id: str,
        vehicle_id: str,
        vehicle_label: str,
        hazard_id: str,
        hazard_type: str,
        hazard_label: str,
        road_segment_id: str,
        road_segment_name: str,
        timestamp: Optional[float] = None,
    ) -> None:
        return await self._execute(
            self._neo4j.record_observation,
            self._memory.record_observation,
            observation_id=observation_id,
            vehicle_id=vehicle_id,
            vehicle_label=vehicle_label,
            hazard_id=hazard_id,
            hazard_type=hazard_type,
            hazard_label=hazard_label,
            road_segment_id=road_segment_id,
            road_segment_name=road_segment_name,
            timestamp=timestamp,
        )

    async def record_warning(
        self,
        warning_id: str,
        hazard_id: str,
        vehicle_id: str,
        warning_text: str,
        language: str,
        road_segment_id: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> None:
        import math

        # 5. Public Input Validation
        if not warning_id or not isinstance(warning_id, str) or not warning_id.strip():
            raise ValueError("warning_id must be a non-empty string")
        if not hazard_id or not isinstance(hazard_id, str) or not hazard_id.strip():
            raise ValueError("hazard_id must be a non-empty string")
        if not vehicle_id or not isinstance(vehicle_id, str) or not vehicle_id.strip():
            raise ValueError("vehicle_id must be a non-empty string")
        if not warning_text or not isinstance(warning_text, str) or not warning_text.strip():
            raise ValueError("warning_text must be a non-empty string")
        if language not in ("en", "hi", "hinglish"):
            raise ValueError("language must be en, hi, or hinglish")
        if road_segment_id is not None and (not isinstance(road_segment_id, str) or not road_segment_id.strip()):
            raise ValueError("road_segment_id must be a non-empty string")
        if timestamp is not None:
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp < 0.0:
                raise ValueError("timestamp must be a finite non-boolean number >= 0")

        # 1. Same-backend warning write
        if self._mode == "neo4j":
            try:
                return await self._neo4j.record_warning(
                    warning_id=warning_id,
                    hazard_id=hazard_id,
                    vehicle_id=vehicle_id,
                    warning_text=warning_text,
                    language=language,
                    road_segment_id=road_segment_id,
                    timestamp=timestamp,
                )
            except ValueError:
                raise
            except Exception as e:
                if self._strict:
                    self._neo4j_connected = False
                raise RuntimeError("Neo4j warning write failed") from None
        else:
            return await self._memory.record_warning(
                warning_id=warning_id,
                hazard_id=hazard_id,
                vehicle_id=vehicle_id,
                warning_text=warning_text,
                language=language,
                road_segment_id=road_segment_id,
                timestamp=timestamp,
            )

    async def get_observation_hazard(
        self,
        observation_id: str,
    ) -> Optional[dict]:
        _validate_input_str(observation_id, "observation_id")
        return await self._execute(
            self._neo4j.get_observation_hazard,
            self._memory.get_observation_hazard,
            observation_id=observation_id,
        )

    async def list_hazards(self, limit: int = 100) -> List[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 100:
            raise ValueError("limit must be an integer in range 1 through 100")
        return await self._execute(
            self._neo4j.list_hazards,
            self._memory.list_hazards,
            limit=limit,
        )

    async def find_similar_active_hazard(
        self,
        hazard_type: str,
        latitude: float,
        longitude: float,
        road_segment_id: str,
        radius_m: float,
        min_updated_at: float,
    ) -> Optional[dict]:
        _validate_input_str(hazard_type, "hazard_type")
        _validate_input_str(road_segment_id, "road_segment_id")
        lat = _validate_finite_float(latitude, "latitude", -90, 90)
        lon = _validate_finite_float(longitude, "longitude", -180, 180)
        rad = _validate_finite_float(radius_m, "radius_m")
        if rad <= 0:
            raise ValueError("radius_m must be greater than zero")
        min_upd = _validate_finite_float(min_updated_at, "min_updated_at", min_val=0.0)

        return await self._execute(
            self._neo4j.find_similar_active_hazard,
            self._memory.find_similar_active_hazard,
            hazard_type=hazard_type,
            latitude=lat,
            longitude=lon,
            road_segment_id=road_segment_id,
            radius_m=rad,
            min_updated_at=min_upd,
        )

    async def upsert_observation_and_hazard(
        self,
        *,
        observation_id: str,
        vehicle_id: str,
        vehicle_label: str,
        hazard_id: str,
        hazard_type: str,
        hazard_label: str,
        latitude: float,
        longitude: float,
        road_segment_id: str,
        road_segment_name: str,
        timestamp: float,
        hazard_fields: Optional[dict] = None,
    ) -> dict:
        _validate_input_str(observation_id, "observation_id")
        _validate_input_str(vehicle_id, "vehicle_id")
        _validate_input_str(vehicle_label, "vehicle_label")
        _validate_input_str(hazard_id, "hazard_id")
        _validate_input_str(hazard_type, "hazard_type")
        _validate_input_str(hazard_label, "hazard_label")
        _validate_input_str(road_segment_id, "road_segment_id")
        _validate_input_str(road_segment_name, "road_segment_name")
        lat = _validate_finite_float(latitude, "latitude", -90, 90)
        lon = _validate_finite_float(longitude, "longitude", -180, 180)
        ts = _validate_finite_float(timestamp, "timestamp", min_val=0.0)
        normalized_fields = _validate_hazard_fields(hazard_fields)

        return await self._execute(
            self._neo4j.upsert_observation_and_hazard,
            self._memory.upsert_observation_and_hazard,
            observation_id=observation_id,
            vehicle_id=vehicle_id,
            vehicle_label=vehicle_label,
            hazard_id=hazard_id,
            hazard_type=hazard_type,
            hazard_label=hazard_label,
            latitude=lat,
            longitude=lon,
            road_segment_id=road_segment_id,
            road_segment_name=road_segment_name,
            timestamp=ts,
            hazard_fields=normalized_fields,
        )

    async def upsert_vehicle_approach(
        self,
        vehicle_id: str,
        vehicle_label: str,
        road_segment_id: str,
        road_segment_name: str,
    ) -> None:
        _validate_input_str(vehicle_id, "vehicle_id")
        _validate_input_str(vehicle_label, "vehicle_label")
        _validate_input_str(road_segment_id, "road_segment_id")
        _validate_input_str(road_segment_name, "road_segment_name")

        if self._mode == "neo4j":
            try:
                return await self._neo4j.upsert_vehicle_approach(
                    vehicle_id=vehicle_id,
                    vehicle_label=vehicle_label,
                    road_segment_id=road_segment_id,
                    road_segment_name=road_segment_name,
                )
            except ValueError:
                raise
            except Exception:
                if self._strict:
                    self._neo4j_connected = False
                raise RuntimeError("Neo4j upsert_vehicle_approach failed") from None
        else:
            return await self._memory.upsert_vehicle_approach(
                vehicle_id=vehicle_id,
                vehicle_label=vehicle_label,
                road_segment_id=road_segment_id,
                road_segment_name=road_segment_name,
            )

    async def get_warning_recipient_vehicle_ids(
        self,
        hazard_id: str,
        source_vehicle_id: str,
    ) -> List[str]:
        _validate_input_str(hazard_id, "hazard_id")
        _validate_input_str(source_vehicle_id, "source_vehicle_id")

        if self._mode == "neo4j":
            try:
                return await self._neo4j.get_warning_recipient_vehicle_ids(
                    hazard_id=hazard_id,
                    source_vehicle_id=source_vehicle_id,
                )
            except ValueError:
                raise
            except Exception:
                if self._strict:
                    self._neo4j_connected = False
                raise RuntimeError("Neo4j recipient lookup failed") from None
        else:
            return await self._memory.get_warning_recipient_vehicle_ids(
                hazard_id=hazard_id,
                source_vehicle_id=source_vehicle_id,
            )

    async def build_graph(
        self, hazard_id: Optional[str] = None, limit: int = 25
    ) -> Dict[str, Any]:
        self._validate_limit(limit)
        result = await self._execute(
            self._neo4j.build_graph,
            self._memory.build_graph,
            hazard_id=hazard_id,
            limit=limit,
        )
        if isinstance(result, dict):
            result["mode"] = self._mode
        return result

    async def reset_demo_data(self) -> None:
        return await self._execute(
            self._neo4j.reset_demo_data,
            self._memory.reset_demo_data,
        )

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if isinstance(limit, bool):
            raise ValueError("limit must be an integer, not a boolean")
        if not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100 inclusive")

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
        if hazard_id not in self._nodes:
            return
        if self._nodes[hazard_id].get("type") != "Hazard":
            return

        source_vehicles: Set[str] = set()
        for e in self._edges.values():
            if e["type"] == "SUPPORTS" and e["target"] == hazard_id:
                obs_id = e["source"]
                for e2 in self._edges.values():
                    if e2["type"] == "OBSERVED" and e2["target"] == obs_id:
                        source_vehicles.add(e2["source"])

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
            self._merge_node_sync(
                warning_id,
                "Warning",
                f"Warning {warning_id}",
                {"text": warning_text, "language": language, "timestamp": ts},
            )
            self._merge_node_sync(hazard_id, "Hazard", hazard_id, {})
            self._merge_node_sync(vehicle_id, "Vehicle", vehicle_id, {})

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
                self._merge_node_sync(
                    road_segment_id, "RoadSegment", road_segment_id, {}
                )
                self._merge_edge_sync(
                    f"APPROACHING:{vehicle_id}:{road_segment_id}",
                    "APPROACHING",
                    vehicle_id,
                    road_segment_id,
                    {},
                )

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


# ---------------------------------------------------------------------------
# Neo4j backend (async driver, lazy import, sanitized errors)
# ---------------------------------------------------------------------------

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
        OPTIONAL MATCH (v:SentinelPerception:Vehicle)-[obs:OBSERVED {scenario_id: $scenario_id}]->(o)
        OPTIONAL MATCH (o)-[sup:SUPPORTS {scenario_id: $scenario_id}]->(h:SentinelPerception:Hazard)
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
        OPTIONAL MATCH (obs:SentinelPerception:Observation)-[:SUPPORTS {scenario_id: $scenario_id}]->(h)
        OPTIONAL MATCH (v:SentinelPerception:Vehicle)-[:OBSERVED {scenario_id: $scenario_id}]->(obs)
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
        ts = timestamp or 0.0

        query = """
        MERGE (w:SentinelPerception:Warning {id: $w_id, scenario_id: $scenario_id})
          ON CREATE SET w.text = $text, w.language = $lang, w.timestamp = $timestamp
          ON MATCH SET w.text = $text, w.language = $lang, w.timestamp = $timestamp
        MERGE (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})
        MERGE (v:SentinelPerception:Vehicle {id: $v_id, scenario_id: $scenario_id})
        MERGE (h)-[:TRIGGERED_WARNING {scenario_id: $scenario_id}]->(w)
        MERGE (w)-[:DELIVERED_TO {scenario_id: $scenario_id}]->(v)
        """
        await self._run(
            query,
            w_id=warning_id,
            text=warning_text,
            lang=language,
            timestamp=ts,
            h_id=hazard_id,
            v_id=vehicle_id,
            scenario_id=SCENARIO_ID,
        )

        if road_segment_id:
            query2 = """
            MERGE (v:SentinelPerception:Vehicle {id: $v_id, scenario_id: $scenario_id})
            MERGE (r:SentinelPerception:RoadSegment {id: $r_id, scenario_id: $scenario_id})
            MERGE (v)-[:APPROACHING {scenario_id: $scenario_id}]->(r)
            """
            await self._run(
                query2,
                v_id=vehicle_id,
                r_id=road_segment_id,
                scenario_id=SCENARIO_ID,
            )

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

    async def reset_demo_data(self) -> None:
        query = "MATCH (n:SentinelPerception {scenario_id: $scenario_id}) DETACH DELETE n"
        await self._run(query, scenario_id=SCENARIO_ID)


# ---------------------------------------------------------------------------
# Public service (unified facade)
# ---------------------------------------------------------------------------

def _parse_bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() == "true"


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
                raise RuntimeError("Neo4j initialization failed in strict mode") from None
        else:
            if not self._neo4j_enabled:
                await self._memory.initialize()
                self._mode = "memory"
                self._memory_connected = True
            else:
                try:
                    await self._neo4j.initialize()
                    self._mode = "neo4j"
                    self._neo4j_connected = True
                except Exception:
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
        return await self._execute(
            self._neo4j.record_warning,
            self._memory.record_warning,
            warning_id=warning_id,
            hazard_id=hazard_id,
            vehicle_id=vehicle_id,
            warning_text=warning_text,
            language=language,
            road_segment_id=road_segment_id,
            timestamp=timestamp,
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

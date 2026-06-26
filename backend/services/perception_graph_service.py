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

    async def _merge_node(
        self, node_id: str, node_type: str, label: str, properties: Dict[str, Any]
    ) -> None:
        async with self._lock:
            existing = self._nodes.get(node_id)
            if existing:
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

    async def _merge_edge(
        self,
        edge_id: str,
        edge_type: str,
        source: str,
        target: str,
        properties: Dict[str, Any],
    ) -> None:
        async with self._lock:
            if edge_id not in self._edges:
                self._edges[edge_id] = {
                    "id": edge_id,
                    "type": edge_type,
                    "source": source,
                    "target": target,
                    "scenarioId": SCENARIO_ID,
                    "properties": dict(properties),
                }

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

        await self._merge_node(vehicle_id, "Vehicle", vehicle_label or vehicle_id, {})
        await self._merge_node(
            observation_id,
            "Observation",
            f"Observation {observation_id}",
            {"type": hazard_type, "timestamp": ts},
        )
        await self._merge_node(
            hazard_id, "Hazard", hazard_label or hazard_id, {"type": hazard_type}
        )
        await self._merge_node(
            road_segment_id,
            "RoadSegment",
            road_segment_name or road_segment_id,
            {},
        )

        async with self._lock:
            observed_edges = [
                e
                for e in self._edges.values()
                if e["type"] == "OBSERVED" and e["target"] == observation_id
            ]
            if observed_edges:
                existing_vehicle = observed_edges[0]["source"]
                if existing_vehicle != vehicle_id:
                    raise ValueError(
                        f"Observation {observation_id} already owned by vehicle "
                        f"{existing_vehicle}; cannot reassign to vehicle {vehicle_id}"
                    )
                supports_edges = [
                    e
                    for e in self._edges.values()
                    if e["type"] == "SUPPORTS" and e["source"] == observation_id
                ]
                if supports_edges and supports_edges[0]["target"] != hazard_id:
                    existing_hazard = supports_edges[0]["target"]
                    raise ValueError(
                        f"Observation {observation_id} already supports hazard "
                        f"{existing_hazard}; cannot reassign to hazard {hazard_id}"
                    )
                return

        await self._merge_edge(
            f"OBSERVED:{vehicle_id}:{observation_id}", "OBSERVED", vehicle_id, observation_id, {}
        )
        await self._merge_edge(
            f"SUPPORTS:{observation_id}:{hazard_id}", "SUPPORTS", observation_id, hazard_id, {}
        )
        await self._merge_edge(
            f"ON_ROAD:{hazard_id}:{road_segment_id}", "ON_ROAD", hazard_id, road_segment_id, {}
        )
        await self._merge_edge(
            f"APPROACHING:{vehicle_id}:{road_segment_id}",
            "APPROACHING",
            vehicle_id,
            road_segment_id,
            {},
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
        ts = timestamp or 0.0

        await self._merge_node(
            warning_id,
            "Warning",
            f"Warning {warning_id}",
            {"text": warning_text, "language": language, "timestamp": ts},
        )
        await self._merge_node(hazard_id, "Hazard", hazard_id, {})
        await self._merge_node(vehicle_id, "Vehicle", vehicle_id, {})

        await self._merge_edge(
            f"TRIGGERED_WARNING:{hazard_id}:{warning_id}",
            "TRIGGERED_WARNING",
            hazard_id,
            warning_id,
            {},
        )
        await self._merge_edge(
            f"DELIVERED_TO:{warning_id}:{vehicle_id}",
            "DELIVERED_TO",
            warning_id,
            vehicle_id,
            {},
        )

        if road_segment_id:
            await self._merge_node(road_segment_id, "RoadSegment", road_segment_id, {})
            await self._merge_edge(
                f"APPROACHING:{vehicle_id}:{road_segment_id}",
                "APPROACHING",
                vehicle_id,
                road_segment_id,
                {},
            )

    def _bfs_component(self, start_ids: List[str]) -> Tuple[Set[str], Set[str]]:
        nodes: Set[str] = set()
        edge_ids: Set[str] = set()
        queue = list(start_ids)
        visited: Set[str] = set()

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            if current in self._nodes:
                nodes.add(current)
            for eid, e in self._edges.items():
                if e["source"] == current or e["target"] == current:
                    edge_ids.add(eid)
                    other = e["source"] if e["target"] == current else e["target"]
                    if other not in visited:
                        queue.append(other)

        return nodes, edge_ids

    async def build_graph(
        self, hazard_id: Optional[str] = None, limit: int = 25
    ) -> Dict[str, Any]:
        async with self._lock:
            nodes: Dict[str, Dict[str, Any]] = {}
            edges: Dict[str, Dict[str, Any]] = {}

            if hazard_id:
                if (
                    hazard_id in self._nodes
                    and self._nodes[hazard_id].get("type") == "Hazard"
                ):
                    node_ids, edge_ids = self._bfs_component([hazard_id])
                    for nid in node_ids:
                        nodes[nid] = dict(self._nodes[nid])
                    for eid in edge_ids:
                        edges[eid] = dict(self._edges[eid])
            else:
                all_hazards = sorted(
                    nid for nid, n in self._nodes.items() if n.get("type") == "Hazard"
                )
                selected = all_hazards[:limit]
                for hz in selected:
                    node_ids, edge_ids = self._bfs_component([hz])
                    for nid in node_ids:
                        if nid not in nodes:
                            nodes[nid] = dict(self._nodes[nid])
                    for eid in edge_ids:
                        if eid not in edges:
                            edges[eid] = dict(self._edges[eid])

            node_list = list(nodes.values())
            edge_list = list(edges.values())

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
                source_vehicles: Set[str] = set()
                warning_count = 0
                for e in edge_list:
                    if e["type"] == "SUPPORTS" and e["target"] == hazard_id:
                        obs_id = e["source"]
                        for e2 in edge_list:
                            if e2["type"] == "OBSERVED" and e2["target"] == obs_id:
                                source_vehicles.add(e2["source"])
                    if e["type"] == "TRIGGERED_WARNING" and e["source"] == hazard_id:
                        warning_count += 1

                source_count = len(source_vehicles)
                if source_count == 1:
                    confidence = 60
                elif source_count == 2:
                    confidence = 80
                else:
                    confidence = 100 if source_count >= 3 else 60

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
                "focus": focus,
            }

            timeline: List[Dict[str, Any]] = []
            for e in edge_list:
                ts = 0.0
                desc = ""
                if e["type"] == "OBSERVED":
                    ts = (
                        nodes.get(e["source"], {})
                        .get("properties", {})
                        .get("timestamp", 0.0)
                    )
                    desc = f"Vehicle {e['source']} observed {e['target']}"
                elif e["type"] == "SUPPORTS":
                    ts = (
                        nodes.get(e["source"], {})
                        .get("properties", {})
                        .get("timestamp", 0.0)
                    )
                    desc = f"Observation {e['source']} supports hazard {e['target']}"
                elif e["type"] == "TRIGGERED_WARNING":
                    ts = (
                        nodes.get(e["target"], {})
                        .get("properties", {})
                        .get("timestamp", 0.0)
                    )
                    desc = f"Hazard {e['source']} triggered warning {e['target']}"
                elif e["type"] == "DELIVERED_TO":
                    ts = (
                        nodes.get(e["source"], {})
                        .get("properties", {})
                        .get("timestamp", 0.0)
                    )
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
                "mode": "memory",
                "generatedAt": datetime.now(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
                "focusHazardId": hazard_id,
                "nodes": node_list,
                "edges": edge_list,
                "summary": summary,
                "timeline": timeline,
            }

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
        except Exception as e:
            logger.warning(f"Neo4j initialization failed: {type(e).__name__}")
            raise RuntimeError(f"Neo4j connection failed: {type(e).__name__}") from None

    async def close(self) -> None:
        if self._driver is not None:
            try:
                await self._driver.close()
            except Exception:
                pass
            self._driver = None

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
            obs_label=hazard_label or "",
            h_id=hazard_id,
            h_type=hazard_type or "",
            h_label=hazard_label or "",
            r_id=road_segment_id,
            r_name=road_segment_name or road_segment_id,
            timestamp=ts,
            scenario_id=SCENARIO_ID,
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
            OPTIONAL MATCH path = (h)-[r:OBSERVED|SUPPORTS|ON_ROAD|APPROACHING|TRIGGERED_WARNING|DELIVERED_TO*1..10]-(n:SentinelPerception)
            WHERE ALL(rel IN r WHERE rel.scenario_id = $scenario_id)
            RETURN h, path
            """
            params: Dict[str, Any] = {"h_id": hazard_id, "scenario_id": SCENARIO_ID}
        else:
            cypher = """
            MATCH (h:SentinelPerception:Hazard {scenario_id: $scenario_id})
            WITH h ORDER BY h.id LIMIT $limit
            OPTIONAL MATCH path = (h)-[r:OBSERVED|SUPPORTS|ON_ROAD|APPROACHING|TRIGGERED_WARNING|DELIVERED_TO*1..10]-(n:SentinelPerception)
            WHERE ALL(rel IN r WHERE rel.scenario_id = $scenario_id)
            RETURN h, path
            """
            params = {"scenario_id": SCENARIO_ID, "limit": limit}

        nodes: Dict[str, Dict[str, Any]] = {}
        edges: Dict[str, Dict[str, Any]] = {}

        async with self._driver.session(database=self._database) as session:
            try:
                result = await session.run(cypher, **params)
                async for record in result:
                    h_node = record.get("h")
                    if h_node:
                        self._add_neo4j_node(h_node, nodes)
                    path = record.get("path")
                    if path:
                        for node in path.nodes:
                            self._add_neo4j_node(node, nodes)
                        for rel in path.relationships:
                            self._add_neo4j_rel(rel, edges)
            except Exception as e:
                raise RuntimeError(f"Neo4j graph query failed: {type(e).__name__}") from None

        node_list = list(nodes.values())
        edge_list = list(edges.values())

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
            source_vehicles: Set[str] = set()
            warning_count = 0
            for e in edge_list:
                if e["type"] == "SUPPORTS" and e["target"] == hazard_id:
                    obs_id = e["source"]
                    for e2 in edge_list:
                        if e2["type"] == "OBSERVED" and e2["target"] == obs_id:
                            source_vehicles.add(e2["source"])
                if e["type"] == "TRIGGERED_WARNING" and e["source"] == hazard_id:
                    warning_count += 1

            source_count = len(source_vehicles)
            if source_count == 1:
                confidence = 60
            elif source_count == 2:
                confidence = 80
            else:
                confidence = 100 if source_count >= 3 else 60

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
            "focus": focus,
        }

        timeline: List[Dict[str, Any]] = []
        for e in edge_list:
            ts = 0.0
            desc = ""
            if e["type"] == "OBSERVED":
                ts = (
                    nodes.get(e["source"], {})
                    .get("properties", {})
                    .get("timestamp", 0.0)
                )
                desc = f"Vehicle {e['source']} observed {e['target']}"
            elif e["type"] == "SUPPORTS":
                ts = (
                    nodes.get(e["source"], {})
                    .get("properties", {})
                    .get("timestamp", 0.0)
                )
                desc = f"Observation {e['source']} supports hazard {e['target']}"
            elif e["type"] == "TRIGGERED_WARNING":
                ts = (
                    nodes.get(e["target"], {})
                    .get("properties", {})
                    .get("timestamp", 0.0)
                )
                desc = f"Hazard {e['source']} triggered warning {e['target']}"
            elif e["type"] == "DELIVERED_TO":
                ts = (
                    nodes.get(e["source"], {})
                    .get("properties", {})
                    .get("timestamp", 0.0)
                )
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
            "mode": "neo4j",
            "generatedAt": datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "focusHazardId": hazard_id,
            "nodes": node_list,
            "edges": edge_list,
            "summary": summary,
            "timeline": timeline,
        }

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

        props = dict(node)
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

class PerceptionGraphService:
    def __init__(self) -> None:
        self._memory = _InMemoryGraphBackend()
        self._neo4j = _Neo4jGraphBackend()
        self._mode = "memory"

    async def initialize(self) -> None:
        if self._mode == "neo4j":
            return
        try:
            await self._neo4j.initialize()
            self._mode = "neo4j"
        except Exception:
            self._mode = "memory"

    async def close(self) -> None:
        try:
            await self._neo4j.close()
        except Exception:
            pass
        self._mode = "memory"

    async def _execute(self, neo4j_method, memory_method, *args, **kwargs):
        if self._mode == "neo4j":
            try:
                return await neo4j_method(*args, **kwargs)
            except Exception as e:
                logger.warning(
                    f"Neo4j operation failed ({type(e).__name__}), falling back to memory"
                )
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

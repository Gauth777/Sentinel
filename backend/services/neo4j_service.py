import os
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Load configuration from environment variables
NEO4J_ENABLED = os.environ.get("NEO4J_ENABLED", "false").lower() == "true"
NEO4J_URI = os.environ.get("NEO4J_URI", "")
NEO4J_USERNAME = os.environ.get("NEO4J_USERNAME", "")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

# Lazy driver initialization
_driver = None

def get_neo4j_driver():
    global _driver
    if not NEO4J_ENABLED:
        return None
    if _driver is not None:
        return _driver
    
    try:
        from neo4j import GraphDatabase
        if NEO4J_URI and NEO4J_USERNAME and NEO4J_PASSWORD:
            _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
            logger.info("Neo4j driver initialized successfully.")
            return _driver
    except Exception as e:
        logger.warning(f"Failed to initialize Neo4j driver: {e}. Falling back to MongoDB mock graph.")
    return None


class Neo4jService:
    @classmethod
    async def initialize_constraints(cls):
        """Initializes database constraints and indexes if Neo4j is enabled."""
        driver = get_neo4j_driver()
        if not driver:
            return
        
        queries = [
            "CREATE CONSTRAINT vehicle_id IF NOT EXISTS FOR (v:Vehicle) REQUIRE v.id IS UNIQUE",
            "CREATE CONSTRAINT hazard_id IF NOT EXISTS FOR (h:Hazard) REQUIRE h.id IS UNIQUE",
            "CREATE CONSTRAINT segment_id IF NOT EXISTS FOR (r:RoadSegment) REQUIRE r.id IS UNIQUE",
            "CREATE CONSTRAINT observation_id IF NOT EXISTS FOR (o:Observation) REQUIRE o.id IS UNIQUE",
            "CREATE CONSTRAINT warning_id IF NOT EXISTS FOR (w:WarningEvent) REQUIRE w.id IS UNIQUE"
        ]
        
        with driver.session(database=NEO4J_DATABASE) as session:
            for q in queries:
                try:
                    session.run(q)
                except Exception as e:
                    logger.warning(f"Error running constraint query '{q}': {e}")

    @classmethod
    async def record_vehicle(cls, vehicle_id: str, label: str):
        driver = get_neo4j_driver()
        if driver:
            query = "MERGE (v:Vehicle {id: $id}) ON CREATE SET v.label = $label ON MATCH SET v.label = $label"
            with driver.session(database=NEO4J_DATABASE) as session:
                session.run(query, id=vehicle_id, label=label)
        else:
            # Fallback to MongoDB
            from server import db
            await db.neo4j_vehicles.replace_one({"id": vehicle_id}, {"id": vehicle_id, "label": label}, upsert=True)

    @classmethod
    async def record_road_segment(cls, segment_id: str, name: str):
        driver = get_neo4j_driver()
        if driver:
            query = "MERGE (r:RoadSegment {id: $id}) ON CREATE SET r.name = $name ON MATCH SET r.name = $name"
            with driver.session(database=NEO4J_DATABASE) as session:
                session.run(query, id=segment_id, name=name)
        else:
            from server import db
            await db.neo4j_road_segments.replace_one({"id": segment_id}, {"id": segment_id, "name": name}, upsert=True)

    @classmethod
    async def record_vehicle_approaching(cls, vehicle_id: str, segment_id: str):
        driver = get_neo4j_driver()
        if driver:
            query = """
            MATCH (v:Vehicle {id: $v_id})
            MATCH (r:RoadSegment {id: $r_id})
            MERGE (v)-[:APPROACHING]->(r)
            """
            with driver.session(database=NEO4J_DATABASE) as session:
                session.run(query, v_id=vehicle_id, r_id=segment_id)
        else:
            from server import db
            await db.neo4j_approaching.replace_one(
                {"vehicle_id": vehicle_id},
                {"vehicle_id": vehicle_id, "segment_id": segment_id},
                upsert=True
            )

    @classmethod
    async def record_observation(cls, observation_id: str, vehicle_id: str, hazard_id: str, obs_data: dict):
        driver = get_neo4j_driver()
        if driver:
            query = """
            MERGE (o:Observation {id: $obs_id})
            ON CREATE SET o.type = $type, o.label = $label, o.observedSecondsAgo = $sec
            MATCH (v:Vehicle {id: $v_id})
            MATCH (h:Hazard {id: $h_id})
            MERGE (v)-[:MADE]->(o)
            MERGE (o)-[:DESCRIBES]->(h)
            """
            with driver.session(database=NEO4J_DATABASE) as session:
                session.run(
                    query,
                    obs_id=observation_id,
                    type=obs_data.get("type", ""),
                    label=obs_data.get("label", ""),
                    sec=obs_data.get("observedSecondsAgo", 0),
                    v_id=vehicle_id,
                    h_id=hazard_id
                )
        else:
            from server import db
            await db.neo4j_observations.replace_one(
                {"id": observation_id},
                {
                    "id": observation_id,
                    "vehicle_id": vehicle_id,
                    "hazard_id": hazard_id,
                    "type": obs_data.get("type", ""),
                    "label": obs_data.get("label", ""),
                    "observedSecondsAgo": obs_data.get("observedSecondsAgo", 0)
                },
                upsert=True
            )

    @classmethod
    async def record_hazard(cls, hazard_id: str, segment_id: str, hazard_data: dict):
        driver = get_neo4j_driver()
        if driver:
            query = """
            MERGE (h:Hazard {id: $h_id})
            ON CREATE SET h.type = $type, h.label = $label
            ON MATCH SET h.type = $type, h.label = $label
            MATCH (r:RoadSegment {id: $r_id})
            MERGE (h)-[:LOCATED_ON]->(r)
            """
            with driver.session(database=NEO4J_DATABASE) as session:
                session.run(
                    query,
                    h_id=hazard_id,
                    type=hazard_data.get("type", ""),
                    label=hazard_data.get("label", ""),
                    r_id=segment_id
                )
        else:
            from server import db
            await db.neo4j_hazards.replace_one(
                {"id": hazard_id},
                {
                    "id": hazard_id,
                    "segment_id": segment_id,
                    "type": hazard_data.get("type", ""),
                    "label": hazard_data.get("label", ""),
                },
                upsert=True
            )

    @classmethod
    async def record_confirmation(cls, vehicle_id: str, hazard_id: str):
        driver = get_neo4j_driver()
        if driver:
            query = """
            MERGE (v:Vehicle {id: $v_id})
            MERGE (h:Hazard {id: $h_id})
            MERGE (v)-[:CONFIRMED]->(h)
            """
            with driver.session(database=NEO4J_DATABASE) as session:
                session.run(query, v_id=vehicle_id, h_id=hazard_id)
        else:
            from server import db
            # Prevent duplicate confirmation votes
            await db.neo4j_confirmations.replace_one(
                {"vehicle_id": vehicle_id, "hazard_id": hazard_id},
                {"vehicle_id": vehicle_id, "hazard_id": hazard_id},
                upsert=True
            )

    @classmethod
    async def record_report_incorrect(cls, vehicle_id: str, hazard_id: str):
        driver = get_neo4j_driver()
        if driver:
            query = """
            MERGE (v:Vehicle {id: $v_id})
            MERGE (h:Hazard {id: $h_id})
            MERGE (v)-[:REPORTED_INCORRECT]->(h)
            """
            with driver.session(database=NEO4J_DATABASE) as session:
                session.run(query, v_id=vehicle_id, h_id=hazard_id)
        else:
            from server import db
            # Prevent duplicate report_incorrect votes
            await db.neo4j_reports.replace_one(
                {"vehicle_id": vehicle_id, "hazard_id": hazard_id},
                {"vehicle_id": vehicle_id, "hazard_id": hazard_id},
                upsert=True
            )

    @classmethod
    async def record_warning_event(cls, warning_id: str, vehicle_id: str, hazard_id: str, warning_text: str, language: str):
        driver = get_neo4j_driver()
        if driver:
            query = """
            MERGE (w:WarningEvent {id: $w_id})
            ON CREATE SET w.text = $text, w.language = $lang
            MATCH (v:Vehicle {id: $v_id})
            MATCH (h:Hazard {id: $h_id})
            MERGE (w)-[:WARNS]->(v)
            MERGE (w)-[:ABOUT]->(h)
            """
            with driver.session(database=NEO4J_DATABASE) as session:
                session.run(query, w_id=warning_id, text=warning_text, lang=language, v_id=vehicle_id, h_id=hazard_id)
        else:
            from server import db
            await db.neo4j_warnings.replace_one(
                {"id": warning_id},
                {
                    "id": warning_id,
                    "vehicle_id": vehicle_id,
                    "hazard_id": hazard_id,
                    "text": warning_text,
                    "language": language
                },
                upsert=True
            )

    @classmethod
    async def get_relevant_hazards(cls, vehicle_id: str) -> List[str]:
        """Returns the IDs of hazards relevant to an approaching vehicle."""
        driver = get_neo4j_driver()
        if driver:
            query = """
            MATCH (v:Vehicle {id: $v_id})-[:APPROACHING]->(r:RoadSegment)<-[:LOCATED_ON]-(h:Hazard)
            RETURN h.id as id
            """
            with driver.session(database=NEO4J_DATABASE) as session:
                result = session.run(query, v_id=vehicle_id)
                return [record["id"] for record in result]
        else:
            from server import db
            app = await db.neo4j_approaching.find_one({"vehicle_id": vehicle_id})
            if not app:
                return []
            seg_id = app["segment_id"]
            docs = await db.neo4j_hazards.find({"segment_id": seg_id}).to_list(100)
            return [doc["id"] for doc in docs]

    @classmethod
    async def get_hazard_provenance(cls, hazard_id: str) -> List[Dict]:
        """Returns observations and reporting vehicles that led to this hazard."""
        driver = get_neo4j_driver()
        if driver:
            query = """
            MATCH (h:Hazard {id: $h_id})<-[:DESCRIBES]-(o:Observation)<-[:MADE]-(v:Vehicle)
            RETURN o.id as obs_id, o.type as type, v.id as vehicle_id, v.label as label
            """
            with driver.session(database=NEO4J_DATABASE) as session:
                result = session.run(query, h_id=hazard_id)
                return [
                    {
                        "observation_id": record["obs_id"],
                        "type": record["type"],
                        "vehicle_id": record["vehicle_id"],
                        "vehicle_label": record["label"]
                    }
                    for record in result
                ]
        else:
            from server import db
            obs_docs = await db.neo4j_observations.find({"hazard_id": hazard_id}).to_list(100)
            provenance = []
            for obs in obs_docs:
                vehicle = await db.neo4j_vehicles.find_one({"id": obs["vehicle_id"]})
                provenance.append({
                    "observation_id": obs["id"],
                    "type": obs["type"],
                    "vehicle_id": obs["vehicle_id"],
                    "vehicle_label": vehicle["label"] if vehicle else "Unknown Vehicle"
                })
            return provenance

    @classmethod
    async def get_community_votes(cls, hazard_id: str) -> Dict[str, int]:
        """Returns count of unique confirmations and reports for a hazard."""
        driver = get_neo4j_driver()
        if driver:
            c_query = "MATCH (:Vehicle)-[r:CONFIRMED]->(h:Hazard {id: $h_id}) RETURN count(r) as count"
            r_query = "MATCH (:Vehicle)-[r:REPORTED_INCORRECT]->(h:Hazard {id: $h_id}) RETURN count(r) as count"
            with driver.session(database=NEO4J_DATABASE) as session:
                c_res = session.run(c_query, h_id=hazard_id).single()
                r_res = session.run(r_query, h_id=hazard_id).single()
                return {
                    "confirmed": c_res["count"] if c_res else 0,
                    "reportedIncorrect": r_res["count"] if r_res else 0
                }
        else:
            from server import db
            c_count = await db.neo4j_confirmations.count_documents({"hazard_id": hazard_id})
            r_count = await db.neo4j_reports.count_documents({"hazard_id": hazard_id})
            return {
                "confirmed": c_count,
                "reportedIncorrect": r_count
            }

    @classmethod
    async def get_hazards_by_others(cls, vehicle_id: str) -> List[str]:
        """Returns IDs of hazards observed by other vehicles."""
        driver = get_neo4j_driver()
        if driver:
            query = """
            MATCH (h:Hazard)<-[:DESCRIBES]-(o:Observation)<-[:MADE]-(other:Vehicle)
            WHERE other.id <> $v_id
            RETURN DISTINCT h.id as id
            """
            with driver.session(database=NEO4J_DATABASE) as session:
                result = session.run(query, v_id=vehicle_id)
                return [record["id"] for record in result]
        else:
            from server import db
            obs_docs = await db.neo4j_observations.find({"vehicle_id": {"$ne": vehicle_id}}).to_list(100)
            return list(set(doc["hazard_id"] for doc in obs_docs))

    @classmethod
    async def get_road_segment_hazard_history(cls, segment_id: str) -> List[str]:
        """Returns the IDs of all hazards located on a road segment."""
        driver = get_neo4j_driver()
        if driver:
            query = """
            MATCH (r:RoadSegment {id: $r_id})<-[:LOCATED_ON]-(h:Hazard)
            RETURN h.id as id
            """
            with driver.session(database=NEO4J_DATABASE) as session:
                result = session.run(query, r_id=segment_id)
                return [record["id"] for record in result]
        else:
            from server import db
            docs = await db.neo4j_hazards.find({"segment_id": segment_id}).to_list(100)
            return [doc["id"] for doc in docs]

    @classmethod
    async def reset_demo_data(cls):
        """Cleans up all demo data in Neo4j and MongoDB fallbacks."""
        driver = get_neo4j_driver()
        if driver:
            query = "MATCH (n) DETACH DELETE n"
            with driver.session(database=NEO4J_DATABASE) as session:
                session.run(query)
        else:
            from server import db
            await db.neo4j_vehicles.delete_many({})
            await db.neo4j_road_segments.delete_many({})
            await db.neo4j_approaching.delete_many({})
            await db.neo4j_observations.delete_many({})
            await db.neo4j_hazards.delete_many({})
            await db.neo4j_confirmations.delete_many({})
            await db.neo4j_reports.delete_many({})
            await db.neo4j_warnings.delete_many({})

import os
import sys
import asyncio
import logging

from dotenv import load_dotenv

# Ensure backend directory is in path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Load env variables from backend/.env
load_dotenv(os.path.join(backend_dir, ".env"))

from services.perception_graph_service import PerceptionGraphService, SCENARIO_ID
from workflows.hazard_workflow import LocalWorkflowRunner

logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger("verify_neo4j_replay")

async def run_verification():
    # 1. Validate environment
    neo4j_enabled = os.getenv("NEO4J_ENABLED", "false").lower() == "true"
    strict_mode = os.getenv("SENTINEL_NEO4J_STRICT", "false").lower() == "true"
    uri = os.getenv("NEO4J_URI")
    username = os.getenv("NEO4J_USERNAME")
    password = os.getenv("NEO4J_PASSWORD")

    if not neo4j_enabled:
        print("FAIL: Environment validation: NEO4J_ENABLED is not true")
        sys.exit(1)
    if not strict_mode:
        print("FAIL: Environment validation: SENTINEL_NEO4J_STRICT is not true")
        sys.exit(1)
    if not uri or not username or not password:
        print("FAIL: Environment validation: Missing Neo4j credentials")
        sys.exit(1)

    print("PASS: Environment validation successful (secrets masked)")

    # 2. Initialize service
    graph_service = PerceptionGraphService()
    try:
        await graph_service.initialize()
    except Exception as e:
        print(f"FAIL: Failed to initialize graph service: {type(e).__name__}")
        sys.exit(1)

    try:
        # 3. Assert config
        assert graph_service._mode == "neo4j", f"Expected mode neo4j, got {graph_service._mode}"
        assert graph_service._strict is True, "Expected strict mode to be True"
        assert graph_service._neo4j_connected is True, "Expected connected to be True"
        print("PASS: Configuration and connectivity verified")

        # 4. Reset Sentinel demo graph before verification
        await graph_service.reset_demo_data()
        print("PASS: Reset demo data successful")

        # 5. Seed four vehicles
        vehicles_to_seed = [
            ("v-1", "Sentinel-A8"),
            ("v-2", "Sentinel-C2"),
            ("v-3", "Sentinel-F4"),
            ("v-4", "Sentinel-K9"),
        ]
        for vid, vlabel in vehicles_to_seed:
            await graph_service.upsert_vehicle_approach(
                vehicle_id=vid,
                vehicle_label=vlabel,
                road_segment_id="gst",
                road_segment_name="GST Road Northbound"
            )
        print("PASS: Seeding of four vehicles successful")

        # Verify seeded vehicles in AuraDB
        driver = graph_service._neo4j._driver
        db_name = graph_service._neo4j._database

        async with driver.session(database=db_name) as session:
            res = await session.run(
                "MATCH (v:SentinelPerception:Vehicle {scenario_id: $scenario_id}) RETURN count(v) as count",
                scenario_id=SCENARIO_ID
            )
            rec = await res.single()
            assert rec and rec["count"] == 4, f"Expected 4 seeded vehicles, got {rec['count'] if rec else 0}"
        print("PASS: 4 vehicles verified in AuraDB")

        # 6-7. Process pothole observation from v-1
        runner = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9436, "longitude": 80.1502})
        obs = {
            "id": "obs-verify-999",
            "type": "pothole",
            "label": "Pothole Ahead",
            "location": {"latitude": 12.9450, "longitude": 80.1503},
            "sourceVehicleId": "v-1",
            "vehicleLabel": "Sentinel-A8",
        }
        res_wf = await runner.process_observation(obs)
        assert res_wf is not None
        hazard_id = res_wf["id"]
        assert hazard_id.startswith("hz-"), f"Invalid hazard ID: {hazard_id}"

        # 8. Assertions
        warning_events = res_wf.get("_warning_events", [])
        assert len(warning_events) == 4, f"Expected 4 warning events, got {len(warning_events)}"

        # Query all warning nodes and relationships
        async with driver.session(database=db_name) as session:
            # Check hazard count
            res = await session.run(
                "MATCH (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id}) RETURN count(h) as count",
                h_id=hazard_id, scenario_id=SCENARIO_ID
            )
            rec = await res.single()
            assert rec and rec["count"] == 1, "Expected 1 hazard node"

            # Check supports relationship count
            res = await session.run(
                "MATCH (o:SentinelPerception:Observation {id: $obs_id, scenario_id: $scenario_id})-[:SUPPORTS]->(h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id}) RETURN count(o) as count",
                obs_id="obs-verify-999", h_id=hazard_id, scenario_id=SCENARIO_ID
            )
            rec = await res.single()
            assert rec and rec["count"] == 1, "Expected 1 supports relationship"

            # Check warning details
            res = await session.run(
                "MATCH (w:SentinelPerception:Warning {scenario_id: $scenario_id}) "
                "OPTIONAL MATCH (h:SentinelPerception:Hazard {scenario_id: $scenario_id})-[r1:TRIGGERED_WARNING]->(w) "
                "OPTIONAL MATCH (w)-[r2:DELIVERED_TO]->(v:SentinelPerception:Vehicle {scenario_id: $scenario_id}) "
                "RETURN w.id as id, w.text as text, w.language as language, "
                "w.hazardId as hazardId, w.vehicleId as vehicleId, "
                "count(r1) as tw_count, count(r2) as dt_count, v.id as delivered_to_vehicle",
                scenario_id=SCENARIO_ID
            )
            records = []
            async for r in res:
                records.append(r.data())

            assert len(records) == 4, f"Expected 4 warning nodes, found {len(records)}"
            vehicles_delivered = set()
            for r in records:
                assert r["hazardId"] == hazard_id, "hazardId property mismatch on Warning"
                assert r["vehicleId"] is not None, "vehicleId property missing on Warning"
                assert r["vehicleId"] == r["delivered_to_vehicle"], "vehicleId property does not match delivered vehicle node"
                assert r["tw_count"] == 1, "Warning triggered count is not exactly 1"
                assert r["dt_count"] == 1, "Warning delivered count is not exactly 1"
                assert r["language"] == "en", "Expected Warning language 'en'"
                assert r["text"] == res_wf["warnings"]["en"], "Warning text mismatch"
                vehicles_delivered.add(r["vehicleId"])

            assert vehicles_delivered == {"v-1", "v-2", "v-3", "v-4"}, f"Incorrect delivery vehicles: {vehicles_delivered}"

        print("PASS: Observation process and database assertions verified")

        # 9. Process the same observation again and assert idempotency
        res_wf_dup = await runner.process_observation(obs)
        assert res_wf_dup["id"] == hazard_id
        assert res_wf_dup["_warning_events"] == warning_events

        async with driver.session(database=db_name) as session:
            # Check warning node count remains 4
            res = await session.run(
                "MATCH (w:SentinelPerception:Warning {scenario_id: $scenario_id}) RETURN count(w) as count",
                scenario_id=SCENARIO_ID
            )
            rec = await res.single()
            assert rec and rec["count"] == 4, f"Expected warning count to remain 4, got {rec['count'] if rec else 0}"

            # Check TRIGGERED_WARNING relationship count remains 4
            res = await session.run(
                "MATCH ()-[r:TRIGGERED_WARNING {scenario_id: $scenario_id}]->() RETURN count(r) as count",
                scenario_id=SCENARIO_ID
            )
            rec = await res.single()
            assert rec and rec["count"] == 4, f"Expected TRIGGERED_WARNING relationship count to remain 4, got {rec['count'] if rec else 0}"

            # Check hazard stats do not increase
            res = await session.run(
                "MATCH (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id}) RETURN h.sourceCount as sc, h.confidence as conf",
                h_id=hazard_id, scenario_id=SCENARIO_ID
            )
            rec = await res.single()
            assert rec and rec["sc"] == 1, f"Expected sourceCount to remain 1, got {rec['sc']}"

        print("PASS: Idempotency assertions verified")

        # 10. Real concurrent exact retry using asyncio.gather
        concurrent_warning_id = f"warn-concurrent-{hazard_id}"
        await asyncio.gather(
            graph_service.record_warning(concurrent_warning_id, hazard_id, "v-1", "Pothole ahead", "en"),
            graph_service.record_warning(concurrent_warning_id, hazard_id, "v-1", "Pothole ahead", "en")
        )

        async with driver.session(database=db_name) as session:
            res = await session.run(
                "MATCH (w:SentinelPerception:Warning {id: $w_id, scenario_id: $scenario_id}) RETURN count(w) as count",
                w_id=concurrent_warning_id, scenario_id=SCENARIO_ID
            )
            rec = await res.single()
            assert rec and rec["count"] == 1, "Expected exactly 1 warning node for concurrent exact retry"

            res = await session.run(
                "MATCH (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id})-[r:TRIGGERED_WARNING]->(w:SentinelPerception:Warning {id: $w_id, scenario_id: $scenario_id}) RETURN count(r) as count",
                h_id=hazard_id, w_id=concurrent_warning_id, scenario_id=SCENARIO_ID
            )
            rec = await res.single()
            assert rec and rec["count"] == 1, "Expected exactly 1 TRIGGERED_WARNING relationship for concurrent exact retry"

            res = await session.run(
                "MATCH (w:SentinelPerception:Warning {id: $w_id, scenario_id: $scenario_id})-[r:DELIVERED_TO]->(v:SentinelPerception:Vehicle {id: $v_id, scenario_id: $scenario_id}) RETURN count(r) as count",
                w_id=concurrent_warning_id, v_id="v-1", scenario_id=SCENARIO_ID
            )
            rec = await res.single()
            assert rec and rec["count"] == 1, "Expected exactly 1 DELIVERED_TO relationship for concurrent exact retry"

        print("PASS: Concurrent exact retry verified")

        # 11. Perform concurrent ownership conflict
        conflict_warning_id = f"warn-conflict-{hazard_id}"
        results_conflict = await asyncio.gather(
            graph_service.record_warning(conflict_warning_id, hazard_id, "v-2", "Pothole ahead", "en"),
            graph_service.record_warning(conflict_warning_id, hazard_id, "v-3", "Pothole ahead", "en"),
            return_exceptions=True
        )

        success_count = sum(1 for r in results_conflict if r is None)
        error_count = sum(1 for r in results_conflict if isinstance(r, ValueError))
        exception_types = [type(r).__name__ for r in results_conflict if isinstance(r, Exception)]

        assert success_count == 1, f"Expected 1 success, got {success_count}"
        assert error_count == 1, f"Expected 1 ValueError, got {error_count} (exceptions: {exception_types})"

        # Verify only 1 Warning and 1 relationship pair exist for conflict ID in Neo4j
        async with driver.session(database=db_name) as session:
            res = await session.run(
                "MATCH (w:SentinelPerception:Warning {id: $w_id, scenario_id: $scenario_id}) RETURN count(w) as count",
                w_id=conflict_warning_id, scenario_id=SCENARIO_ID
            )
            rec = await res.single()
            assert rec and rec["count"] == 1, "Expected exactly 1 warning node for concurrent ownership conflict"

            res = await session.run(
                "MATCH (w:SentinelPerception:Warning {id: $w_id, scenario_id: $scenario_id})-[r:DELIVERED_TO]->() RETURN count(r) as count",
                w_id=conflict_warning_id, scenario_id=SCENARIO_ID
            )
            rec = await res.single()
            assert rec and rec["count"] == 1, "Expected exactly 1 DELIVERED_TO relationship for concurrent ownership conflict"

        print("PASS: Concurrent ownership conflict verified")

        # 12. Perform same-ID/different-hazard conflict using two valid hazards
        obs_another = {
            "id": "obs-verify-888",
            "type": "pothole",
            "label": "Pothole Ahead",
            "location": {"latitude": 12.9836, "longitude": 80.2502},
            "sourceVehicleId": "v-2",
            "vehicleLabel": "Sentinel-C2",
        }
        res_wf_another = await runner.process_observation(obs_another)
        hz_another_id = res_wf_another["id"]

        hz_conflict_warning_id = f"warn-hz-conflict-{hazard_id}"
        results_hz_conflict = await asyncio.gather(
            graph_service.record_warning(hz_conflict_warning_id, hazard_id, "v-1", "Pothole ahead", "en"),
            graph_service.record_warning(hz_conflict_warning_id, hz_another_id, "v-1", "Pothole ahead", "en"),
            return_exceptions=True
        )

        success_count = sum(1 for r in results_hz_conflict if r is None)
        error_count = sum(1 for r in results_hz_conflict if isinstance(r, ValueError))
        hz_exception_types = [type(r).__name__ for r in results_hz_conflict if isinstance(r, Exception)]

        assert success_count == 1, f"Expected 1 success, got {success_count}"
        assert error_count == 1, f"Expected 1 ValueError, got {error_count} (exceptions: {hz_exception_types})"

        # Verify only 1 Warning node exists in Neo4j
        async with driver.session(database=db_name) as session:
            res = await session.run(
                "MATCH (w:SentinelPerception:Warning {id: $w_id, scenario_id: $scenario_id}) RETURN count(w) as count",
                w_id=hz_conflict_warning_id, scenario_id=SCENARIO_ID
            )
            rec = await res.single()
            assert rec and rec["count"] == 1, "Expected exactly 1 warning node for concurrent hazard conflict"

        print("PASS: Concurrent hazard conflict verified")
        print("\nALL STAGE B2B INTEGRITY VERIFICATIONS PASSED.")

    except Exception as e:
        print(f"FAIL: Verification failed: {type(e).__name__}")
        sys.exit(1)
    finally:
        try:
            await graph_service.reset_demo_data()
        except Exception as e:
            print(f"WARN: Failed to reset demo data in finally: {type(e).__name__}")
        await graph_service.close()

if __name__ == "__main__":
    asyncio.run(run_verification())

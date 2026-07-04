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

            # Check DELIVERED_TO relationship count remains 4
            res = await session.run(
                "MATCH ()-[r:DELIVERED_TO {scenario_id: $scenario_id}]->() RETURN count(r) as count",
                scenario_id=SCENARIO_ID
            )
            rec = await res.single()
            assert rec and rec["count"] == 4, f"Expected DELIVERED_TO relationship count to remain 4, got {rec['count'] if rec else 0}"

            # Check hazard stats do not increase
            res = await session.run(
                "MATCH (h:SentinelPerception:Hazard {id: $h_id, scenario_id: $scenario_id}) RETURN h.sourceCount as sc, h.confidence as conf",
                h_id=hazard_id, scenario_id=SCENARIO_ID
            )
            rec = await res.single()
            assert rec and rec["sc"] == 1, f"Expected sourceCount to remain 1, got {rec['sc']}"
            assert rec and rec["conf"] == 60, f"Expected confidence to remain 60, got {rec['conf']}"

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
            # Query Warning properties and connected relationships
            res = await session.run(
                "MATCH (w:SentinelPerception:Warning {id: $w_id, scenario_id: $scenario_id}) "
                "OPTIONAL MATCH (h:SentinelPerception:Hazard {scenario_id: $scenario_id})-[r1:TRIGGERED_WARNING]->(w) "
                "OPTIONAL MATCH (w)-[r2:DELIVERED_TO]->(v:SentinelPerception:Vehicle {scenario_id: $scenario_id}) "
                "RETURN count(w) as w_count, count(r1) as tw_count, count(r2) as dt_count, "
                "       h.id as actual_hazardId, v.id as actual_vehicleId, "
                "       w.hazardId as prop_hazardId, w.vehicleId as prop_vehicleId",
                w_id=conflict_warning_id, scenario_id=SCENARIO_ID
            )
            rec = await res.single()
            assert rec is not None
            assert rec["w_count"] == 1, f"Expected exactly 1 warning node, got {rec['w_count']}"
            assert rec["tw_count"] == 1, f"Expected exactly 1 TRIGGERED_WARNING, got {rec['tw_count']}"
            assert rec["dt_count"] == 1, f"Expected exactly 1 DELIVERED_TO, got {rec['dt_count']}"
            assert rec["prop_vehicleId"] == rec["actual_vehicleId"], "Warning.vehicleId property does not match actual DELIVERED_TO vehicle"
            assert rec["prop_hazardId"] == rec["actual_hazardId"], "Warning.hazardId property does not match actual TRIGGERED_WARNING hazard"
            assert rec["prop_hazardId"] == hazard_id, f"Expected Warning.hazardId == {hazard_id}, got {rec['prop_hazardId']}"
            assert rec["prop_vehicleId"] in ("v-2", "v-3"), f"Expected Warning.vehicleId to be v-2 or v-3, got {rec['prop_vehicleId']}"

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

        success_count_hz = sum(1 for r in results_hz_conflict if r is None)
        error_count_hz = sum(1 for r in results_hz_conflict if isinstance(r, ValueError))
        hz_exception_types = [type(r).__name__ for r in results_hz_conflict if isinstance(r, Exception)]

        assert success_count_hz == 1, f"Expected 1 success, got {success_count_hz}"
        assert error_count_hz == 1, f"Expected 1 ValueError, got {error_count_hz} (exceptions: {hz_exception_types})"

        # Verify only 1 Warning node exists in Neo4j
        async with driver.session(database=db_name) as session:
            res = await session.run(
                "MATCH (w:SentinelPerception:Warning {id: $w_id, scenario_id: $scenario_id}) "
                "OPTIONAL MATCH (h:SentinelPerception:Hazard {scenario_id: $scenario_id})-[r1:TRIGGERED_WARNING]->(w) "
                "OPTIONAL MATCH (w)-[r2:DELIVERED_TO]->(v:SentinelPerception:Vehicle {scenario_id: $scenario_id}) "
                "RETURN count(w) as w_count, count(r1) as tw_count, count(r2) as dt_count, "
                "       h.id as actual_hazardId, v.id as actual_vehicleId, "
                "       w.hazardId as prop_hazardId, w.vehicleId as prop_vehicleId",
                w_id=hz_conflict_warning_id, scenario_id=SCENARIO_ID
            )
            rec = await res.single()
            assert rec is not None
            assert rec["w_count"] == 1, f"Expected exactly 1 warning node, got {rec['w_count']}"
            assert rec["tw_count"] == 1, f"Expected exactly 1 TRIGGERED_WARNING, got {rec['tw_count']}"
            assert rec["dt_count"] == 1, f"Expected exactly 1 DELIVERED_TO, got {rec['dt_count']}"
            assert rec["prop_hazardId"] == rec["actual_hazardId"], "Warning.hazardId property does not match actual TRIGGERED_WARNING hazard"
            assert rec["prop_vehicleId"] == rec["actual_vehicleId"], "Warning.vehicleId property does not match actual DELIVERED_TO vehicle"
            assert rec["prop_vehicleId"] == "v-1", f"Expected Warning.vehicleId == v-1, got {rec['prop_vehicleId']}"
            assert rec["prop_hazardId"] in (hazard_id, hz_another_id), f"Expected Warning.hazardId in ({hazard_id}, {hz_another_id}), got {rec['prop_hazardId']}"

        print("PASS: Concurrent hazard conflict verified")

        import math

        # C2 Check 1: both feedback relationship constraints exist
        async with driver.session(database=db_name) as session:
            res_constraints = await session.run("SHOW CONSTRAINTS")
            records = await res_constraints.data()
            constraint_names = {r["name"] for r in records}
            assert "sentinel_confirmed_feedback_identity" in constraint_names, "Missing sentinel_confirmed_feedback_identity constraint"
            assert "sentinel_reported_feedback_identity" in constraint_names, "Missing sentinel_reported_feedback_identity constraint"
        print("PASS: Both feedback relationship constraints exist")

        # C2 Check 2: duplicate confirm/report, exact retry preserves created_at
        runner_c2 = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9100, "longitude": 80.1100})
        obs_c2 = {
            "id": "obs-c2-verify",
            "type": "pothole",
            "label": "Pothole C2",
            "location": {"latitude": 12.9100, "longitude": 80.1100},
            "sourceVehicleId": "v-1",
            "vehicleLabel": "Sentinel-A8",
        }
        res_wf_c2 = await runner_c2.process_observation(obs_c2)
        hz_c2_id = res_wf_c2["id"]

        res_c1 = await graph_service.record_hazard_feedback(
            hazard_id=hz_c2_id,
            vehicle_id="v-2",
            vehicle_label="Sentinel-C2",
            feedback_type="confirm",
            timestamp=12345.67
        )
        assert res_c1["feedbackCreated"] is True

        res_c2 = await graph_service.record_hazard_feedback(
            hazard_id=hz_c2_id,
            vehicle_id="v-2",
            vehicle_label="Sentinel-C2",
            feedback_type="confirm",
            timestamp=99999.99
        )
        assert res_c2["feedbackCreated"] is False

        async with driver.session(database=db_name) as session:
            res = await session.run(
                "MATCH (v:Vehicle {id: 'v-2'})-[r:CONFIRMED]->(h:Hazard {id: $h_id}) RETURN r.created_at as created_at, count(r) as r_count",
                h_id=hz_c2_id
            )
            rec = await res.single()
            assert rec is not None
            assert rec["r_count"] == 1, f"Expected 1 CONFIRMED relationship, got {rec['r_count']}"
            assert rec["created_at"] == 12345.67, f"Expected created_at to be preserved at 12345.67, got {rec['created_at']}"
            assert isinstance(rec["created_at"], float) and not isinstance(rec["created_at"], bool) and math.isfinite(rec["created_at"]) and rec["created_at"] > 0
        print("PASS: Duplicate confirm creates one relationship and retry preserves created_at")

        # C2 Check 3: feedback preserves Hazard.created_at and Hazard.updated_at
        async with driver.session(database=db_name) as session:
            res = await session.run(
                "MATCH (h:Hazard {id: $h_id}) RETURN h.created_at as created_at, h.updated_at as updated_at",
                h_id=hz_c2_id
            )
            rec = await res.single()
            assert rec is not None
            hz_created_at = rec["created_at"]
            hz_updated_at = rec["updated_at"]

            await graph_service.record_hazard_feedback(
                hazard_id=hz_c2_id,
                vehicle_id="v-3",
                vehicle_label="Sentinel-F4",
                feedback_type="confirm",
                timestamp=55555.55
            )

            res_after = await session.run(
                "MATCH (h:Hazard {id: $h_id}) RETURN h.created_at as created_at, h.updated_at as updated_at",
                h_id=hz_c2_id
            )
            rec_after = await res_after.single()
            assert rec_after is not None
            assert rec_after["created_at"] == hz_created_at, "Hazard.created_at was modified by feedback"
            assert rec_after["updated_at"] == hz_updated_at, "Hazard.updated_at was modified by feedback"
        print("PASS: Feedback preserves Hazard.created_at and Hazard.updated_at")

        # C2 Check 4: concurrent identical feedback & concurrent different voters
        await asyncio.gather(
            graph_service.record_hazard_feedback(hz_c2_id, "v-4", "Sentinel-K9", "confirm", 60000.0),
            graph_service.record_hazard_feedback(hz_c2_id, "v-4", "Sentinel-K9", "confirm", 70000.0)
        )

        async with driver.session(database=db_name) as session:
            res = await session.run(
                "MATCH (v:Vehicle {id: 'v-4'})-[r:CONFIRMED]->(h:Hazard {id: $h_id}) RETURN count(r) as r_count, r.created_at as created_at",
                h_id=hz_c2_id
            )
            rec = await res.single()
            assert rec is not None
            assert rec["r_count"] == 1, f"Expected 1 concurrent relationship, got {rec['r_count']}"
            assert rec["created_at"] in (60000.0, 70000.0), f"Expected one of the timestamps to be preserved, got {rec['created_at']}"

        await asyncio.gather(
            graph_service.record_hazard_feedback(hz_c2_id, "v-obs", "Sentinel-A8", "report_incorrect", 80000.0),
            graph_service.record_hazard_feedback(hz_c2_id, "v-1", "Sentinel-A8", "report_incorrect", 81000.0)
        )

        async with driver.session(database=db_name) as session:
            res = await session.run(
                "MATCH (h:Hazard {id: $h_id}) RETURN h.confirmed as confirmed, h.reportedIncorrect as reportedIncorrect, h.sourceCount as sourceCount",
                h_id=hz_c2_id
            )
            rec = await res.single()
            assert rec is not None
            assert rec["confirmed"] == 3, f"Expected 3 confirmed, got {rec['confirmed']}"
            assert rec["reportedIncorrect"] == 2, f"Expected 2 reportedIncorrect, got {rec['reportedIncorrect']}"
            assert rec["sourceCount"] == 1, f"Expected 1 sourceCount, got {rec['sourceCount']}"
        print("PASS: Concurrent identical/different voters produce exact counts")

        # C2 Check 5: the 4-report + concurrent-confirm race remains resolved
        runner_race = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9200, "longitude": 80.1200})
        obs_race = {
            "id": "obs-race-verify",
            "type": "pothole",
            "label": "Pothole Race",
            "location": {"latitude": 12.9200, "longitude": 80.1200},
            "sourceVehicleId": "v-1",
            "vehicleLabel": "Sentinel-A8",
        }
        res_race = await runner_race.process_observation(obs_race)
        hz_race_id = res_race["id"]

        for voter in ("v-2", "v-3", "v-4"):
            await graph_service.record_hazard_feedback(hz_race_id, voter, "Voter", "report_incorrect")

        async with driver.session(database=db_name) as session:
            res = await session.run("MATCH (h:Hazard {id: $h_id}) RETURN h.status as status", h_id=hz_race_id)
            assert (await res.single())["status"] == "active"

        await asyncio.gather(
            graph_service.record_hazard_feedback(hz_race_id, "v-1", "Voter", "report_incorrect"),
            graph_service.record_hazard_feedback(hz_race_id, "v-obs", "Voter", "confirm")
        )

        async with driver.session(database=db_name) as session:
            res = await session.run(
                "MATCH (h:Hazard {id: $h_id}) RETURN h.status as status, h.confirmed as confirmed, h.reportedIncorrect as reportedIncorrect, h.confidence as confidence",
                h_id=hz_race_id
            )
            rec = await res.single()
            assert rec is not None
            assert rec["status"] == "resolved", f"Expected resolved status, got {rec['status']}"
            assert rec["confirmed"] == 1, f"Expected confirmed == 1, got {rec['confirmed']}"
            assert rec["reportedIncorrect"] == 4, f"Expected reportedIncorrect == 4, got {rec['reportedIncorrect']}"
            assert rec["confidence"] == 10, f"Expected confidence == 10, got {rec['confidence']}"
        print("PASS: Concurrent 4-report and 1-confirm race remains resolved (post-lock monotonicity verified)")

        # C2 Check 6: five distinct reports resolve a separate hazard
        runner_five = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9300, "longitude": 80.1300})
        obs_five = {
            "id": "obs-five-verify",
            "type": "pothole",
            "label": "Pothole Five",
            "location": {"latitude": 12.9300, "longitude": 80.1300},
            "sourceVehicleId": "v-1",
            "vehicleLabel": "Sentinel-A8",
        }
        res_five = await runner_five.process_observation(obs_five)
        hz_five_id = res_five["id"]

        for i, voter in enumerate(["v-1", "v-2", "v-3", "v-4", "v-obs"]):
            await graph_service.record_hazard_feedback(hz_five_id, voter, "Voter", "report_incorrect")

        async with driver.session(database=db_name) as session:
            res = await session.run("MATCH (h:Hazard {id: $h_id}) RETURN h.status as status, h.reportedIncorrect as reportedIncorrect", h_id=hz_five_id)
            rec = await res.single()
            assert rec is not None
            assert rec["reportedIncorrect"] == 5, f"Expected 5 reports, got {rec['reportedIncorrect']}"
            assert rec["status"] == "resolved", f"Expected resolved status, got {rec['status']}"
        print("PASS: Five distinct reports resolve the hazard")

        # C2 Check 7: later corroborating observation preserves feedback and recalculates confidence
        runner_corrob = LocalWorkflowRunner(graph_service=graph_service, ego_location={"latitude": 12.9400, "longitude": 80.1400})
        obs_corrob = {
            "id": "obs-corrob-verify-1",
            "type": "pothole",
            "label": "Pothole Corrob",
            "location": {"latitude": 12.9400, "longitude": 80.1400},
            "sourceVehicleId": "v-1",
            "vehicleLabel": "Sentinel-A8",
        }
        res_corrob = await runner_corrob.process_observation(obs_corrob)
        hz_corrob_id = res_corrob["id"]

        await graph_service.record_hazard_feedback(hz_corrob_id, "v-2", "Voter", "confirm")

        async with driver.session(database=db_name) as session:
            res = await session.run("MATCH (h:Hazard {id: $h_id}) RETURN h.confirmed as confirmed, h.confidence as confidence, h.sourceCount as sourceCount", h_id=hz_corrob_id)
            rec = await res.single()
            assert rec is not None
            assert rec["confirmed"] == 1
            assert rec["confidence"] == 70
            assert rec["sourceCount"] == 1

        obs_corrob_2 = {
            "id": "obs-corrob-verify-2",
            "type": "pothole",
            "label": "Pothole Corrob 2",
            "location": {"latitude": 12.9400, "longitude": 80.1400},
            "sourceVehicleId": "v-3",
            "vehicleLabel": "Sentinel-F4",
        }
        await runner_corrob.process_observation(obs_corrob_2)

        async with driver.session(database=db_name) as session:
            res = await session.run("MATCH (h:Hazard {id: $h_id}) RETURN h.confirmed as confirmed, h.confidence as confidence, h.sourceCount as sourceCount", h_id=hz_corrob_id)
            rec = await res.single()
            assert rec is not None
            assert rec["confirmed"] == 1, "Expected confirmation count to be preserved"
            assert rec["confidence"] == 90, f"Expected recalculated confidence to be 90, got {rec['confidence']}"
            assert rec["sourceCount"] == 2, f"Expected sourceCount to be 2, got {rec['sourceCount']}"
        print("PASS: Later corroborating observation preserves feedback and recalculates confidence")

        # C2 Check 8: unknown-hazard feedback creates no Hazard or Vehicle
        async with driver.session(database=db_name) as session:
            await session.run("MATCH (v:Vehicle {id: 'v-c2-unknown-voter'}) DETACH DELETE v")

        res_unk = await graph_service.record_hazard_feedback(
            hazard_id="hz-c2-unknown",
            vehicle_id="v-c2-unknown-voter",
            vehicle_label="Unknown Voter",
            feedback_type="confirm"
        )
        assert res_unk is None

        async with driver.session(database=db_name) as session:
            res_hz = await session.run("MATCH (h:Hazard {id: 'hz-c2-unknown'}) RETURN count(h) as count")
            assert (await res_hz.single())["count"] == 0

            res_v = await session.run("MATCH (v:Vehicle {id: 'v-c2-unknown-voter'}) RETURN count(v) as count")
            assert (await res_v.single())["count"] == 0
        print("PASS: Unknown hazard feedback creates no Hazard or Vehicle")

        # C2 Check 9: build_graph includes feedback edges and voter nodes
        g = await graph_service.build_graph(hazard_id=hz_c2_id)
        node_ids = {n["id"] for n in g["nodes"]}
        edge_types = {e["type"] for e in g["edges"]}
        assert "v-2" in node_ids, "Voter node v-2 missing from build_graph"
        assert "CONFIRMED" in edge_types, "CONFIRMED feedback edge missing from build_graph"
        print("PASS: build_graph includes feedback edges and voter nodes")

        # 13. Print final successful counts
        async with driver.session(database=db_name) as session:
            res_h = await session.run("MATCH (h:SentinelPerception:Hazard {scenario_id: $scenario_id}) RETURN count(h) as count", scenario_id=SCENARIO_ID)
            count_h = (await res_h.single())["count"]

            res_o = await session.run("MATCH (o:SentinelPerception:Observation {scenario_id: $scenario_id}) RETURN count(o) as count", scenario_id=SCENARIO_ID)
            count_o = (await res_o.single())["count"]

            res_w = await session.run("MATCH (w:SentinelPerception:Warning {scenario_id: $scenario_id}) RETURN count(w) as count", scenario_id=SCENARIO_ID)
            count_w = (await res_w.single())["count"]

            res_tw = await session.run("MATCH ()-[r:TRIGGERED_WARNING {scenario_id: $scenario_id}]->() RETURN count(r) as count", scenario_id=SCENARIO_ID)
            count_tw = (await res_tw.single())["count"]

            res_dt = await session.run("MATCH ()-[r:DELIVERED_TO {scenario_id: $scenario_id}]->() RETURN count(r) as count", scenario_id=SCENARIO_ID)
            count_dt = (await res_dt.single())["count"]

            res_recip = await session.run("MATCH (w:SentinelPerception:Warning {scenario_id: $scenario_id})-[r:DELIVERED_TO]->(v:SentinelPerception:Vehicle) RETURN distinct v.id as vehicle_id", scenario_id=SCENARIO_ID)
            recip_ids = []
            async for r in res_recip:
                recip_ids.append(r["vehicle_id"])

        print("\n=== FINAL SUCCESSFUL COUNTS ===")
        print(f"Hazards: {count_h}")
        print(f"Observations: {count_o}")
        print(f"Warnings: {count_w}")
        print(f"TRIGGERED_WARNING relationships: {count_tw}")
        print(f"DELIVERED_TO relationships: {count_dt}")
        print(f"Recipient IDs: {sorted(recip_ids)}")
        print("Concurrent Conflict Success/Error Counts:")
        print(f"  - Ownership conflict: Success={success_count}, Errors={error_count}, Exception types={exception_types}")
        print(f"  - Hazard conflict: Success={success_count_hz}, Errors={error_count_hz}, Exception types={hz_exception_types}")
        print("================================\n")

        print("ALL STAGE B2B INTEGRITY VERIFICATIONS PASSED.")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"FAIL: Verification failed: {type(e).__name__}: {e}")
        sys.exit(1)
    finally:
        try:
            await graph_service.reset_demo_data()
        except Exception as e:
            print(f"WARN: Failed to reset demo data in finally: {type(e).__name__}")
        await graph_service.close()

if __name__ == "__main__":
    asyncio.run(run_verification())

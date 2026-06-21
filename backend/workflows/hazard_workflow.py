import os
import math
import uuid
import time
from typing import Dict, List, Optional
from services.neo4j_service import Neo4jService
from services.warning_service import WarningService

# Workflow Settings
MATCH_RADIUS_METERS = 50.0
MATCH_WINDOW_SECONDS = 600  # 10 minutes
EXPIRY_SECONDS = 3600       # 1 hour

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates flat distance in meters between two coordinates (approximated for Tambaram, Chennai)."""
    # 1 degree lat = ~111,111 m; 1 degree lon = ~111,111 * cos(lat)
    lat_m = 111111.0
    lon_m = 111111.0 * math.cos(math.radians(12.9436))
    
    dn = (lat1 - lat2) * lat_m
    de = (lon1 - lon2) * lon_m
    return math.sqrt(dn * dn + de * de)


class WorkflowRunner:
    async def process_observation(self, observation: dict) -> dict:
        raise NotImplementedError()


class LocalWorkflowRunner(WorkflowRunner):
    def __init__(self):
        pass

    async def process_observation(self, obs: dict) -> dict:
        """Processes a new raw observation idempotently.
        
        Returns the resulting hazard details.
        """
        from server import db, EGO

        # 1. Idempotency Check
        obs_id = obs.get("id")
        if not obs_id:
            raise ValueError("Observation must have a unique ID.")

        existing_obs = await db.observations.find_one({"id": obs_id})
        if existing_obs:
            # Already processed! Return the associated hazard.
            hazard_id = existing_obs["hazard_id"]
            existing_hz = await db.hazards.find_one({"id": hazard_id}, {"_id": 0})
            return existing_hz

        # 2. Validation
        obs_type = obs.get("type")
        location = obs.get("location")
        source_vehicle_id = obs.get("sourceVehicleId", "v-unknown")
        vehicle_label = obs.get("vehicleLabel", "Sentinel Vehicle")
        
        if not obs_type or not location:
            raise ValueError("Observation must contain type and location.")

        lat = location.get("latitude")
        lon = location.get("longitude")
        if lat is None or lon is None:
            raise ValueError("Location must contain latitude and longitude.")

        # 3. Resolve Road Segment
        # Find nearest road segment among GST, Side, Service
        roads = [
            {"id": "gst", "name": "GST Road Northbound", "coords": [
                {"latitude": 12.9436, "longitude": 80.1502},  # approximate centerline points
                {"latitude": 12.9474, "longitude": 80.1511}
            ]},
            {"id": "side", "name": "Velachery Link Rd", "coords": [
                {"latitude": 12.9441, "longitude": 80.1356},
                {"latitude": 12.9441, "longitude": 80.1664}
            ]},
            {"id": "service", "name": "Service Rd", "coords": [
                {"latitude": 12.9429, "longitude": 80.1574},
                {"latitude": 12.9465, "longitude": 80.1580}
            ]}
        ]
        
        # Simple closest-point-to-centerline resolver
        best_road_id = "gst"
        min_dist = 999999.0
        for road in roads:
            for coord in road["coords"]:
                dist = calculate_distance(lat, lon, coord["latitude"], coord["longitude"])
                if dist < min_dist:
                    min_dist = dist
                    best_road_id = road["id"]

        # 4. Find Similar Active Hazard
        matched_hazard = None
        hazards = await db.hazards.find({"status": "active"}).to_list(100)
        
        current_time = time.time()
        
        for hz in hazards:
            if hz["type"] == obs_type:
                # Check segment
                hz_segment = hz.get("segment_id", "gst")
                if hz_segment == best_road_id:
                    # Check distance
                    hz_lat = hz["location"]["latitude"]
                    hz_lon = hz["location"]["longitude"]
                    dist = calculate_distance(lat, lon, hz_lat, hz_lon)
                    if dist <= MATCH_RADIUS_METERS:
                        # Check match window (last updated or created within match window)
                        last_updated = hz.get("updated_at", hz.get("created_at", current_time))
                        if (current_time - last_updated) <= MATCH_WINDOW_SECONDS:
                            matched_hazard = hz
                            break

        # 5. Create/Update Hazard & Recalculate Confidence
        hazard_id = None
        is_new = False
        
        if matched_hazard:
            hazard_id = matched_hazard["id"]
            # Update matching hazard
            sources_vehicles = set(matched_hazard.get("source_vehicles", []))
            sources_vehicles.add(source_vehicle_id)
            sources_count = len(sources_vehicles)
            
            # Recalculate confidence
            # Base confidence = 60 + min(40, (sources - 1) * 20)
            base_confidence = 60 + min(40, (sources_count - 1) * 20)
            confirmed = matched_hazard.get("confirmed", 0)
            reported_incorrect = matched_hazard.get("reportedIncorrect", 0)
            
            # Decay: 0.1 points per second since last update
            elapsed = current_time - matched_hazard.get("updated_at", current_time)
            decay = elapsed * 0.1
            
            confidence = max(0, min(100, int(base_confidence + confirmed * 10 - reported_incorrect * 15 - decay)))
            
            # Update hazard in MongoDB
            matched_hazard["sources"] = sources_count
            matched_hazard["source_vehicles"] = list(sources_vehicles)
            matched_hazard["confidence"] = confidence
            matched_hazard["updated_at"] = current_time
            matched_hazard["observedSecondsAgo"] = 0
            
            # Determine action based on recommendedAction
            rec_action = matched_hazard.get("recommendedAction", "Exercise caution")
            
            # Add dynamic multilingual warnings
            warnings = WarningService.generate_warning_texts(obs_type, int(matched_hazard["distanceMeters"]), rec_action)
            matched_hazard["warnings"] = warnings
            
            # Status check
            if confidence <= 0 or reported_incorrect >= 5:
                matched_hazard["status"] = "resolved"
            else:
                matched_hazard["status"] = "active"

            await db.hazards.replace_one({"id": hazard_id}, matched_hazard)
            result_hazard = matched_hazard
        else:
            is_new = True
            hazard_id = f"hz-{uuid.uuid4().hex[:8]}"
            sources_vehicles = [source_vehicle_id]
            sources_count = 1
            confidence = 60 # Base confidence for 1 source
            
            # Estimate distance to ego
            ego_lat = EGO["latitude"]
            ego_lon = EGO["longitude"]
            distance_to_ego = int(calculate_distance(lat, lon, ego_lat, ego_lon))
            
            rec_action = "Reduce speed" if obs_type == "stationary_vehicle" else "Move left" if obs_type == "pothole" else "Exercise caution"
            warnings = WarningService.generate_warning_texts(obs_type, distance_to_ego, rec_action)
            
            new_hazard = {
                "id": hazard_id,
                "type": obs_type,
                "label": obs.get("label", obs_type.replace("_", " ").title()),
                "location": {"latitude": lat, "longitude": lon},
                "polygon": obs.get("polygon"),
                "distanceMeters": float(distance_to_ego),
                "confidence": confidence,
                "sources": sources_count,
                "observedSecondsAgo": 0,
                "direction": "Northbound lane",
                "recommendedAction": rec_action,
                "risk": "high" if obs_type == "stationary_vehicle" else "medium",
                "visibilityState": "hidden",
                "sourceType": "shared_vehicle",
                "routeRelevance": "high" if distance_to_ego < 250 else "medium",
                "confirmed": 0,
                "reportedIncorrect": 0,
                "source_vehicles": sources_vehicles,
                "segment_id": best_road_id,
                "status": "active",
                "created_at": current_time,
                "updated_at": current_time,
                "warnings": warnings
            }
            await db.hazards.replace_one({"id": hazard_id}, new_hazard, upsert=True)
            result_hazard = new_hazard

        # 6. Record in MongoDB observations for idempotency
        await db.observations.replace_one(
            {"id": obs_id},
            {
                "id": obs_id,
                "hazard_id": hazard_id,
                "vehicle_id": source_vehicle_id,
                "location": {"latitude": lat, "longitude": lon},
                "timestamp": current_time
            },
            upsert=True
        )

        # 7. Record Graph Relationships in Neo4j (and its fallback)
        try:
            # Merge vehicle
            await Neo4jService.record_vehicle(source_vehicle_id, vehicle_label)
            # Merge road segment
            road_name = "GST Road Northbound" if best_road_id == "gst" else "Velachery Link Rd" if best_road_id == "side" else "Service Rd"
            await Neo4jService.record_road_segment(best_road_id, road_name)
            # Link vehicle Approaching segment (for observer, we can set it to best_road_id)
            await Neo4jService.record_vehicle_approaching(source_vehicle_id, best_road_id)
            # Link hazard LocatedOn segment
            await Neo4jService.record_hazard(hazard_id, best_road_id, result_hazard)
            # Link vehicle Made observation, which describes hazard
            await Neo4jService.record_observation(
                obs_id,
                source_vehicle_id,
                hazard_id,
                {"type": obs_type, "label": result_hazard["label"], "observedSecondsAgo": 0}
            )
        except Exception as e:
            logger.warning(f"Error updating Neo4j relationship during workflow: {e}")

        # 8. Find Approaching Vehicles & Generate Warning Events
        # Let's say all nearby vehicles are approaching their respective segments
        try:
            nearby_vehicles = await db.nearby_vehicles.find({}).to_list(100)
            for vehicle in nearby_vehicles:
                # Link nearby vehicles to segment gst or side or service
                v_id = vehicle["id"]
                # In demo, vehicles are on GST
                await Neo4jService.record_vehicle(v_id, vehicle.get("label", v_id))
                await Neo4jService.record_vehicle_approaching(v_id, "gst")
                
                # Check if this vehicle is approaching the road segment of the hazard
                relevant_hazard_ids = await Neo4jService.get_relevant_hazards(v_id)
                if hazard_id in relevant_hazard_ids:
                    # Create WarningEvent
                    warning_text = result_hazard["warnings"]["en"]
                    warning_id = f"wrn-{obs_id}-{v_id}"
                    
                    # Store WarningEvent node & relationships
                    await Neo4jService.record_warning_event(
                        warning_id,
                        v_id,
                        hazard_id,
                        warning_text,
                        "en"
                    )
        except Exception as e:
            logger.warning(f"Error generating warning events in Neo4j during workflow: {e}")

        # Remove internal fields before returning
        return_dict = dict(result_hazard)
        return_dict.pop("_id", None)
        return return_dict

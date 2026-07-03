import hashlib
import logging
import math
import time
from typing import Optional, List

from services.warning_service import WarningService
from utils.geo import haversine_meters

logger = logging.getLogger(__name__)

# Workflow Settings
MATCH_RADIUS_METERS = 50.0
MATCH_WINDOW_SECONDS = 600  # 10 minutes

ROADS = [
    {
        "id": "gst",
        "name": "GST Road Northbound",
        "coords": [
            {"latitude": 12.9436, "longitude": 80.1502},
            {"latitude": 12.9474, "longitude": 80.1511},
        ],
    },
    {
        "id": "side",
        "name": "Velachery Link Rd",
        "coords": [
            {"latitude": 12.9441, "longitude": 80.1356},
            {"latitude": 12.9441, "longitude": 80.1664},
        ],
    },
    {
        "id": "service",
        "name": "Service Rd",
        "coords": [
            {"latitude": 12.9429, "longitude": 80.1574},
            {"latitude": 12.9465, "longitude": 80.1580},
        ],
    },
]

RISK_RANK = {
    "low": 0,
    "medium": 1,
    "high": 2,
}


def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates distance in metres between two coordinates."""
    return haversine_meters(lat1, lon1, lat2, lon2)


def resolve_road_segment(lat: float, lon: float) -> tuple[str, str]:
    """Resolves road segment ID and name based on closest centerline point."""
    best_road_id = "gst"
    best_road_name = "GST Road Northbound"
    min_dist = float("inf")
    for road in ROADS:
        for coord in road["coords"]:
            dist = calculate_distance(lat, lon, coord["latitude"], coord["longitude"])
            if dist < min_dist:
                min_dist = dist
                best_road_id = road["id"]
                best_road_name = road["name"]
    return best_road_id, best_road_name


def generate_deterministic_hazard_id(observation_id: str) -> str:
    """Generates deterministic hazard ID from observation ID."""
    hash_object = hashlib.sha256(observation_id.encode("utf-8"))
    hex_digest = hash_object.hexdigest()
    return f"hz-{hex_digest[:12]}"


def generate_deterministic_warning_id(hazard_id: str, observation_id: str, vehicle_id: str, language: str) -> str:
    """Generates a deterministic and stable warning ID."""
    return f"warn-{hazard_id}-{observation_id}-{vehicle_id}-{language}"


async def _record_warning_events(
    graph_service,
    hazard_id: str,
    obs_id: str,
    source_vehicle_id: str,
    road_segment_id: Optional[str],
    warnings: dict,
    current_time: float,
) -> List[str]:
    warning_ids = []

    # 1. Attempt the existing local source warning
    source_warning_id = generate_deterministic_warning_id(
        hazard_id=hazard_id,
        observation_id=obs_id,
        vehicle_id=source_vehicle_id,
        language="en"
    )
    try:
        await graph_service.record_warning(
            warning_id=source_warning_id,
            hazard_id=hazard_id,
            vehicle_id=source_vehicle_id,
            warning_text=warnings["en"],
            language="en",
            road_segment_id=road_segment_id,
            timestamp=current_time
        )
        warning_ids.append(source_warning_id)
    except Exception as e:
        logger.warning("Failed to record source warning event: %s", type(e).__name__)

    # 2. Call get_warning_recipient_vehicle_ids
    peer_vehicle_ids = []
    try:
        peer_vehicle_ids = await graph_service.get_warning_recipient_vehicle_ids(
            hazard_id=hazard_id,
            source_vehicle_id=source_vehicle_id
        )
    except Exception as e:
        logger.warning("Failed to look up peer warning recipients: %s", type(e).__name__)

    # 3. For each returned peer ID, record one warning
    for peer_id in peer_vehicle_ids:
        if peer_id == source_vehicle_id:
            continue
        peer_warning_id = generate_deterministic_warning_id(
            hazard_id=hazard_id,
            observation_id=obs_id,
            vehicle_id=peer_id,
            language="en"
        )
        try:
            await graph_service.record_warning(
                warning_id=peer_warning_id,
                hazard_id=hazard_id,
                vehicle_id=peer_id,
                warning_text=warnings["en"],
                language="en",
                road_segment_id=road_segment_id,
                timestamp=current_time
            )
            warning_ids.append(peer_warning_id)
        except Exception as e:
            logger.warning("Failed to record peer warning event for %s: %s", peer_id, type(e).__name__)

    return warning_ids


class WorkflowRunner:
    async def process_observation(self, observation: dict) -> dict:
        raise NotImplementedError()


class LocalWorkflowRunner(WorkflowRunner):
    def __init__(self, graph_service=None, ego_location=None):
        self.graph_service = graph_service
        self.ego_location = ego_location

    async def process_observation(self, obs: dict) -> dict:
        """Processes a new raw observation graph-authoritatively and idempotently."""
        # 1. Lazy dependency resolution
        graph_service = self.graph_service
        if graph_service is None:
            from server import _perception_graph
            graph_service = _perception_graph

        ego_location = self.ego_location
        if ego_location is None:
            from server import EGO
            ego_location = EGO

        # 2. Strict Input Validation
        if not isinstance(obs, dict):
            raise ValueError("Observation must be a dictionary.")

        obs_id = obs.get("id")
        if not obs_id or not isinstance(obs_id, str) or not obs_id.strip():
            raise ValueError("Observation id must be a non-empty string.")

        obs_type = obs.get("type")
        if not obs_type or not isinstance(obs_type, str) or not obs_type.strip():
            raise ValueError("Observation type must be a non-empty string.")

        label = obs.get("label")
        if label is None:
            label = obs_type.replace("_", " ").title()
        if not label or not isinstance(label, str) or not label.strip():
            raise ValueError("Observation label must resolve to a non-empty string.")

        source_vehicle_id = obs.get("sourceVehicleId")
        if source_vehicle_id is None:
            source_vehicle_id = "v-unknown"
        if not source_vehicle_id or not isinstance(source_vehicle_id, str) or not source_vehicle_id.strip():
            raise ValueError("Observation sourceVehicleId must resolve to a non-empty string.")

        vehicle_label = obs.get("vehicleLabel")
        if vehicle_label is None:
            vehicle_label = "Sentinel Vehicle"
        if not vehicle_label or not isinstance(vehicle_label, str) or not vehicle_label.strip():
            raise ValueError("Observation vehicleLabel must resolve to a non-empty string.")

        location = obs.get("location")
        if not isinstance(location, dict):
            raise ValueError("Observation location must be a dictionary.")

        lat = location.get("latitude")
        lon = location.get("longitude")

        if lat is None or isinstance(lat, bool) or not isinstance(lat, (int, float)):
            raise ValueError("Location latitude must be a finite number.")
        if not math.isfinite(lat) or lat < -90.0 or lat > 90.0:
            raise ValueError("Location latitude must be between -90 and 90.")

        if lon is None or isinstance(lon, bool) or not isinstance(lon, (int, float)):
            raise ValueError("Location longitude must be a finite number.")
        if not math.isfinite(lon) or lon < -180.0 or lon > 180.0:
            raise ValueError("Location longitude must be between -180 and 180.")

        polygon = obs.get("polygon")
        if polygon is not None:
            if not isinstance(polygon, list):
                raise ValueError("Polygon must be a list.")
            for pt in polygon:
                if not isinstance(pt, dict) or "latitude" not in pt or "longitude" not in pt:
                    raise ValueError("Polygon points must contain latitude and longitude.")
                p_lat = pt.get("latitude")
                p_lon = pt.get("longitude")
                if p_lat is None or isinstance(p_lat, bool) or not isinstance(p_lat, (int, float)) or not math.isfinite(p_lat) or p_lat < -90.0 or p_lat > 90.0:
                    raise ValueError("Polygon point latitude must be a finite number between -90 and 90.")
                if p_lon is None or isinstance(p_lon, bool) or not isinstance(p_lon, (int, float)) or not math.isfinite(p_lon) or p_lon < -180.0 or p_lon > 180.0:
                    raise ValueError("Polygon point longitude must be a finite number between -180 and 180.")

        # 3. Validate _replay_meta before any graph call
        replay_meta = obs.get("_replay_meta")
        if replay_meta is not None:
            if not isinstance(replay_meta, dict):
                raise ValueError("_replay_meta must be a dictionary or None.")

            # String fields when present and non-None
            str_fields = ["recommendedAction", "model", "inferenceMode", "sampleId", "lastInferenceId"]
            for field in str_fields:
                if field in replay_meta:
                    val = replay_meta[field]
                    if val is not None:
                        if not isinstance(val, str) or not val.strip():
                            raise ValueError(f"Replay metadata {field} must be a non-empty string.")

            # risk when present and non-None
            if "risk" in replay_meta:
                risk_val = replay_meta["risk"]
                if risk_val is not None:
                    if risk_val not in ("low", "medium", "high"):
                        raise ValueError("Replay metadata risk must be 'low', 'medium', or 'high'.")

            # confidence when present and non-None
            if "confidence" in replay_meta:
                conf_val = replay_meta["confidence"]
                if conf_val is not None:
                    if isinstance(conf_val, bool) or not isinstance(conf_val, (int, float)) or not math.isfinite(conf_val):
                        raise ValueError("Replay metadata confidence must be a finite number.")

        # 4. Idempotency Check via PerceptionGraphService
        existing_hz = await graph_service.get_observation_hazard(obs_id)
        current_time = time.time()
        if existing_hz:
            hz_copy = dict(existing_hz)
            updated_at = hz_copy.get("updated_at") or current_time
            observed_seconds_ago = max(0, int(current_time - updated_at))
            hz_copy["observedSecondsAgo"] = observed_seconds_ago

            distance_to_ego = hz_copy.get("distanceMeters")
            if distance_to_ego is None:
                hz_lat = hz_copy["location"]["latitude"]
                hz_lon = hz_copy["location"]["longitude"]
                distance_to_ego = calculate_distance(hz_lat, hz_lon, ego_location["latitude"], ego_location["longitude"])

            warnings = WarningService.generate_warning_texts(
                hz_copy["type"],
                int(distance_to_ego),
                hz_copy.get("recommendedAction", "Exercise caution")
            )
            hz_copy["warnings"] = warnings

            # Record warning events
            hz_copy["_warning_events"] = await _record_warning_events(
                graph_service=graph_service,
                hazard_id=hz_copy["id"],
                obs_id=obs_id,
                source_vehicle_id=source_vehicle_id,
                road_segment_id=hz_copy.get("segment_id"),
                warnings=warnings,
                current_time=current_time
            )

            return hz_copy

        # 4. Resolve Road Segment
        road_segment_id, road_segment_name = resolve_road_segment(lat, lon)

        # 5. Similar Active Hazard Lookup
        matched_hazard = None
        match_result = await graph_service.find_similar_active_hazard(
            hazard_type=obs_type,
            latitude=lat,
            longitude=lon,
            road_segment_id=road_segment_id,
            radius_m=MATCH_RADIUS_METERS,
            min_updated_at=current_time - MATCH_WINDOW_SECONDS,
        )
        if match_result:
            matched_hazard = match_result["hazard"]

        # 6. Selected Hazard ID
        if matched_hazard:
            selected_hazard_id = matched_hazard["id"]
        else:
            selected_hazard_id = generate_deterministic_hazard_id(obs_id)

        # 7. Hazard Field Construction
        if matched_hazard:
            hz_lat = matched_hazard["location"]["latitude"]
            hz_lon = matched_hazard["location"]["longitude"]
            distance_to_ego = calculate_distance(hz_lat, hz_lon, ego_location["latitude"], ego_location["longitude"])
        else:
            distance_to_ego = calculate_distance(lat, lon, ego_location["latitude"], ego_location["longitude"])

        if obs_type == "stationary_vehicle":
            default_action = "Reduce speed"
            default_risk = "high"
        elif obs_type == "pothole":
            default_action = "Move left"
            default_risk = "medium"
        else:
            default_action = "Exercise caution"
            default_risk = "medium"

        replay_meta = obs.get("_replay_meta")

        # 7a. Action resolution
        if replay_meta and replay_meta.get("recommendedAction"):
            final_action = replay_meta.get("recommendedAction")
        elif matched_hazard:
            final_action = matched_hazard.get("recommendedAction", default_action)
        else:
            final_action = default_action

        # 7b. Risk resolution (preserve or increase, never decrease)
        if replay_meta and replay_meta.get("risk") in RISK_RANK:
            replay_risk = replay_meta.get("risk")
            if matched_hazard:
                existing_risk = matched_hazard.get("risk")
                if existing_risk in RISK_RANK and RISK_RANK[replay_risk] < RISK_RANK[existing_risk]:
                    final_risk = existing_risk
                else:
                    final_risk = replay_risk
            else:
                final_risk = replay_risk
        elif matched_hazard:
            final_risk = matched_hazard.get("risk", default_risk)
        else:
            final_risk = default_risk

        # 7c. Polygon resolution
        if polygon is not None:
            final_polygon = polygon
        elif matched_hazard:
            final_polygon = matched_hazard.get("polygon")
        else:
            final_polygon = None

        hazard_fields = {
            "distanceMeters": float(distance_to_ego),
            "direction": "Northbound lane" if not matched_hazard else matched_hazard.get("direction", "Northbound lane"),
            "recommendedAction": final_action,
            "risk": final_risk,
            "visibilityState": "hidden" if not matched_hazard else matched_hazard.get("visibilityState", "hidden"),
            "sourceType": "shared_vehicle" if not matched_hazard else matched_hazard.get("sourceType", "shared_vehicle"),
            "routeRelevance": "high" if distance_to_ego < 250 else "medium",
            "polygon": final_polygon,
            "status": "active" if not matched_hazard else matched_hazard.get("status", "active"),
        }

        # Map replay provenance variables
        if replay_meta:
            if "model" in replay_meta:
                hazard_fields["model"] = replay_meta["model"]
            if "inferenceMode" in replay_meta:
                hazard_fields["inferenceMode"] = replay_meta["inferenceMode"]
            if "sampleId" in replay_meta:
                hazard_fields["sampleId"] = replay_meta["sampleId"]
            if "lastInferenceId" in replay_meta:
                hazard_fields["lastInferenceId"] = replay_meta["lastInferenceId"]
            if "confidence" in replay_meta and replay_meta["confidence"] is not None:
                hazard_fields["replayConfidence"] = float(replay_meta["confidence"])
        elif matched_hazard:
            for key in ("model", "inferenceMode", "sampleId", "lastInferenceId", "replayConfidence"):
                if key in matched_hazard and matched_hazard[key] is not None:
                    hazard_fields[key] = matched_hazard[key]

        # 8. Graph Authoritative Write
        if matched_hazard:
            existing_label = matched_hazard.get("label")
            if existing_label and isinstance(existing_label, str) and existing_label.strip():
                upsert_hazard_label = existing_label
            else:
                upsert_hazard_label = label
        else:
            upsert_hazard_label = label

        upsert_res = await graph_service.upsert_observation_and_hazard(
            observation_id=obs_id,
            vehicle_id=source_vehicle_id,
            vehicle_label=vehicle_label,
            hazard_id=selected_hazard_id,
            hazard_type=obs_type,
            hazard_label=upsert_hazard_label,
            latitude=lat,
            longitude=lon,
            road_segment_id=road_segment_id,
            road_segment_name=road_segment_name,
            timestamp=current_time,
            hazard_fields=hazard_fields,
        )

        result_hazard = upsert_res["hazard"]

        # 9. Presentation Response Construction
        response = dict(result_hazard)
        response.pop("_id", None)

        updated_at = response.get("updated_at") or current_time
        response["observedSecondsAgo"] = max(0, int(current_time - updated_at))

        dist_m = response.get("distanceMeters")
        if dist_m is None:
            dist_m = distance_to_ego

        warnings = WarningService.generate_warning_texts(
            response["type"],
            int(dist_m),
            response.get("recommendedAction", "Exercise caution")
        )
        response["warnings"] = warnings

        # Record warning events
        response["_warning_events"] = await _record_warning_events(
            graph_service=graph_service,
            hazard_id=response["id"],
            obs_id=obs_id,
            source_vehicle_id=source_vehicle_id,
            road_segment_id=response.get("segment_id") or road_segment_id,
            warnings=warnings,
            current_time=current_time
        )

        return response

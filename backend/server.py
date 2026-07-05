from fastapi import FastAPI, APIRouter, HTTPException, Query
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os
import logging
import math
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from pymongo import MongoClient
from utils.mongo_mock import MOCK_DB_STATE
from utils.geo import haversine_meters, offset_point

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
db_name = os.environ.get("DB_NAME", "test_database")

MONGO_REACHABLE = False
try:
    check_client = MongoClient(mongo_url, serverSelectionTimeoutMS=1000)
    check_client.admin.command('ping')
    MONGO_REACHABLE = True
    check_client.close()
except Exception:
    pass

if MONGO_REACHABLE:
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
else:
    db = MOCK_DB_STATE
    class DummyClient:
        def close(self):
            pass
    client = DummyClient()


from services.perception_graph_service import PerceptionGraphService, SCENARIO_ID
_perception_graph = PerceptionGraphService()


from services.training_sample_service import TrainingSampleService
_training_samples = TrainingSampleService(db, MONGO_REACHABLE)


from services.media_storage import LocalMediaStorage
from services.media_service import MediaService
_media_storage = LocalMediaStorage(db, MONGO_REACHABLE)
_media_service = MediaService(_media_storage)


from services.demo_replay_service import DemoReplayService
_demo_replay = DemoReplayService()


from services.vision_inference_service import VisionInferenceService
_vision_inference = VisionInferenceService()


# ======================= Models =======================
class GeoPoint(BaseModel):
    latitude: float
    longitude: float


class DemoObservationRequest(BaseModel):
    id: str
    type: str
    label: str
    location: GeoPoint
    polygon: Optional[List[GeoPoint]] = None
    sourceVehicleId: str
    vehicleLabel: str


class SentinelStatus(BaseModel):
    connected: bool
    gps_locked: bool
    network: str
    speed_kmh: int
    road_name: str
    heading: str
    sentinel_vehicles_nearby: int


class NearbyVehicle(BaseModel):
    id: str
    label: str
    location: GeoPoint
    heading_degrees: float


class OccupiedRegion(BaseModel):
    id: str
    sourceType: Literal["local_sensor", "shared_vehicle", "demo"]
    visibilityState: Literal["visible", "hidden", "uncertain"]
    objectType: Literal["vehicle", "pedestrian", "road_obstruction", "unknown"]
    polygon: List[GeoPoint]
    center: GeoPoint
    approximateDistanceMeters: float
    confidence: int
    motion: Literal["static", "moving", "unknown"]
    routeRelevance: Literal["none", "low", "medium", "high"]
    observedSecondsAgo: int
    sourceVehicleId: Optional[str] = None
    headingDegrees: Optional[float] = None
    label: Optional[str] = None


class Hazard(BaseModel):
    id: str
    type: str
    label: str
    location: GeoPoint
    polygon: Optional[List[GeoPoint]] = None
    distanceMeters: float
    confidence: int
    sources: int
    observedSecondsAgo: int
    direction: str
    recommendedAction: str
    risk: Literal["high", "medium", "low"]
    visibilityState: Literal["visible", "hidden", "uncertain"]
    sourceType: Literal["local_sensor", "shared_vehicle", "demo"]
    routeRelevance: Literal["none", "low", "medium", "high"]
    confirmed: int = 0
    reportedIncorrect: int = 0


class HazardActionResponse(BaseModel):
    id: str
    confirmed: int
    reportedIncorrect: int


class WorldModel(BaseModel):
    scenarioId: str
    telemetrySource: Literal["live", "cached", "demo"]
    ego: dict
    mapCenter: GeoPoint
    mapBounds: dict
    roadCorridor: List[GeoPoint]
    roads: list
    buildings: list
    occupiedRegions: List[OccupiedRegion]
    nearbyVehicles: List[NearbyVehicle]
    hazards: List[Hazard]


# ======================= Demo scenario (mirrors frontend demoScenario.ts) =======================
EGO = {"latitude": 12.9436, "longitude": 80.1502}
LAT_M = 1.0 / 111_111
LON_M = 1.0 / (111_111 * math.cos(math.radians(EGO["latitude"])))
DEFAULT_LIVE_RADIUS_M = 800.0
MAX_LIVE_RADIUS_M = 2_000.0


def off(dN: float, dE: float) -> dict:
    return {"latitude": EGO["latitude"] + dN * LAT_M, "longitude": EGO["longitude"] + dE * LON_M}


def rect(cN: float, cE: float, w: float, h: float) -> list:
    return [
        off(cN - h / 2, cE - w / 2),
        off(cN - h / 2, cE + w / 2),
        off(cN + h / 2, cE + w / 2),
        off(cN + h / 2, cE - w / 2),
    ]


def route_relevance_for_distance(distance_meters: float) -> str:
    if distance_meters <= 180:
        return "high"
    if distance_meters <= 400:
        return "medium"
    if distance_meters <= 800:
        return "low"
    return "none"


def normalize_heading_degrees(heading: Optional[float], fallback: float = 0) -> float:
    if heading is None or not math.isfinite(heading) or heading < 0:
        return fallback
    return heading % 360


def bounds_around(center: dict, radius_m: float) -> dict:
    d_lat = radius_m / 111_111
    d_lon = radius_m / (111_111 * max(0.01, math.cos(math.radians(center["latitude"]))))
    return {
        "southWest": {"latitude": center["latitude"] - d_lat, "longitude": center["longitude"] - d_lon},
        "northEast": {"latitude": center["latitude"] + d_lat, "longitude": center["longitude"] + d_lon},
    }


def live_context_origin(latitude: float, longitude: float) -> dict:
    # Stable roughly 550 m grid. Nearby GPS updates keep the same synthetic road geometry.
    tile_deg = 0.005
    return {
        "latitude": math.floor(latitude / tile_deg) * tile_deg + tile_deg / 2,
        "longitude": math.floor(longitude / tile_deg) * tile_deg + tile_deg / 2,
    }


def live_off(origin: dict, dN: float, dE: float) -> dict:
    return offset_point(origin["latitude"], origin["longitude"], dN, dE)


def live_rect(origin: dict, cN: float, cE: float, w: float, h: float) -> list:
    return [
        live_off(origin, cN - h / 2, cE - w / 2),
        live_off(origin, cN - h / 2, cE + w / 2),
        live_off(origin, cN + h / 2, cE + w / 2),
        live_off(origin, cN + h / 2, cE - w / 2),
    ]


def build_live_synthetic_context(origin: dict) -> dict:
    main_road = [
        live_off(origin, -420, -3),
        live_off(origin, -240, 1),
        live_off(origin, -80, -2),
        live_off(origin, 100, 2),
        live_off(origin, 280, -1),
        live_off(origin, 460, 4),
    ]
    cross_road = [
        live_off(origin, -10, -260),
        live_off(origin, -8, -80),
        live_off(origin, -4, 90),
        live_off(origin, 0, 260),
    ]
    service_road = [
        live_off(origin, -210, 78),
        live_off(origin, -40, 82),
        live_off(origin, 150, 78),
        live_off(origin, 340, 86),
    ]
    buildings = [
        {"id": f"live-b{i}", "polygon": live_rect(origin, n, e, w, h)}
        for i, (n, e, w, h) in enumerate(
            [
                (-210, -58, 32, 80),
                (-80, -62, 30, 72),
                (80, -62, 36, 96),
                (245, -58, 32, 84),
                (-230, 60, 34, 78),
                (-55, 62, 30, 86),
                (125, 60, 38, 72),
                (310, 62, 34, 82),
            ],
            start=1,
        )
    ]
    road_corridor = (
        [{"latitude": p["latitude"], "longitude": p["longitude"] - 6 / (111_111 * max(0.01, math.cos(math.radians(origin["latitude"]))))} for p in main_road]
        + [{"latitude": p["latitude"], "longitude": p["longitude"] + 6 / (111_111 * max(0.01, math.cos(math.radians(origin["latitude"]))))} for p in reversed(main_road)]
    )
    occupied = [
        {
            "id": "live-or-1",
            "sourceType": "demo",
            "visibilityState": "uncertain",
            "objectType": "unknown",
            "polygon": live_rect(origin, 55, -12, 3, 3),
            "center": live_off(origin, 55, -12),
            "approximateDistanceMeters": 55,
            "confidence": 48,
            "motion": "unknown",
            "routeRelevance": "low",
            "observedSecondsAgo": 5,
            "label": "Synthetic occupied region",
        }
    ]
    return {
        "mapCenter": origin,
        "mapBounds": bounds_around(origin, 520),
        "roadCorridor": road_corridor,
        "roads": [
            {"id": "live-main", "path": main_road, "name": "Synthetic local corridor", "lanes": 2},
            {"id": "live-cross", "path": cross_road, "name": "Synthetic cross street"},
            {"id": "live-service", "path": service_road, "name": "Synthetic service lane"},
        ],
        "buildings": buildings,
        "occupiedRegions": occupied,
    }


GST_ROAD = [off(-260, 0), off(-120, -4), off(0, 0), off(140, 6), off(280, 2), off(420, 10)]
SIDE_ROAD = [off(60, -160), off(60, -40), off(60, 40), off(60, 180)]
SERVICE_ROAD = [off(-80, 80), off(40, 78), off(180, 82), off(320, 86)]

BUILDINGS = [
    {"id": "b1", "polygon": rect(-50, -45, 32, 60)},
    {"id": "b2", "polygon": rect(40, -50, 28, 70)},
    {"id": "b3", "polygon": rect(150, -55, 40, 90)},
    {"id": "b4", "polygon": rect(280, -48, 30, 80)},
    {"id": "b5", "polygon": rect(-60, 55, 32, 70)},
    {"id": "b6", "polygon": rect(90, 60, 28, 80)},
    {"id": "b7", "polygon": rect(210, 60, 36, 60)},
    {"id": "b8", "polygon": rect(360, 55, 30, 70)},
]

SEED_HAZARDS = [
    {
        "id": "hz-002",
        "type": "pothole",
        "label": "Deep Pothole",
        "location": off(340, -3),
        "polygon": None,
        "distanceMeters": 340,
        "confidence": 76,
        "sources": 1,
        "observedSecondsAgo": 42,
        "direction": "Northbound lane",
        "recommendedAction": "Move left",
        "risk": "medium",
        "visibilityState": "hidden",
        "sourceType": "shared_vehicle",
        "routeRelevance": "medium",
        "confirmed": 0,
        "reportedIncorrect": 0,
    },
]

SEED_VEHICLES = [
    {"id": "v-1", "label": "Sentinel-A8", "location": off(110, -8), "heading_degrees": 8},
    {"id": "v-2", "label": "Sentinel-C2", "location": off(-40, 5), "heading_degrees": 8},
    {"id": "v-3", "label": "Sentinel-F4", "location": off(220, -2), "heading_degrees": 10},
    {"id": "v-4", "label": "Sentinel-K9", "location": off(80, 14), "heading_degrees": 6},
]

SEED_OCCUPIED = [
    {
        "id": "or-1",
        "sourceType": "local_sensor",
        "visibilityState": "visible",
        "objectType": "vehicle",
        "polygon": rect(30, -3, 4, 8),
        "center": off(30, -3),
        "approximateDistanceMeters": 30,
        "confidence": 88,
        "motion": "moving",
        "routeRelevance": "medium",
        "observedSecondsAgo": 1,
        "headingDegrees": 8,
        "label": "Vehicle ahead",
    },
    {
        "id": "or-2",
        "sourceType": "local_sensor",
        "visibilityState": "visible",
        "objectType": "vehicle",
        "polygon": rect(60, 4, 4, 8),
        "center": off(60, 4),
        "approximateDistanceMeters": 60,
        "confidence": 81,
        "motion": "moving",
        "routeRelevance": "low",
        "observedSecondsAgo": 1,
        "headingDegrees": 6,
        "label": "Vehicle ahead-right",
    },
    {
        "id": "or-3",
        "sourceType": "local_sensor",
        "visibilityState": "visible",
        "objectType": "unknown",
        "polygon": rect(22, -12, 2, 2),
        "center": off(22, -12),
        "approximateDistanceMeters": 24,
        "confidence": 55,
        "motion": "static",
        "routeRelevance": "low",
        "observedSecondsAgo": 2,
        "label": "Unknown occupied region",
    },
    {
        "id": "or-4",
        "sourceType": "local_sensor",
        "visibilityState": "uncertain",
        "objectType": "road_obstruction",
        "polygon": rect(95, 0, 5, 3),
        "center": off(95, 0),
        "approximateDistanceMeters": 95,
        "confidence": 64,
        "motion": "static",
        "routeRelevance": "medium",
        "observedSecondsAgo": 4,
        "label": "Possible debris",
    },
]

# corridor = strip along main road (very approximate)
ROAD_CORRIDOR = (
    [{"latitude": p["latitude"], "longitude": p["longitude"] - 6 * LON_M} for p in GST_ROAD]
    + [{"latitude": p["latitude"], "longitude": p["longitude"] + 6 * LON_M} for p in reversed(GST_ROAD)]
)


# ======================= Demo data migration (idempotent) =======================
# Bump SEED_VERSION when the demo data shape changes. ensure_seed() upserts each
# known demo document by `id`, replacing old-schema records in-place. It does NOT
# touch unrelated collections or user-generated records.
SEED_VERSION = 4


async def ensure_seed() -> None:
    """Idempotent demo-data migration for nearby vehicles.

    Tracks the applied seed version in db.sentinel_meta (single doc, id='seed').
    When the version is outdated:
    - performs one-time deletion of obsolete Mongo hazard/observation and old graph fallback collections.
    - migrates nearby vehicles.

    """
    meta = await db.sentinel_meta.find_one({"id": "seed"})
    if meta and meta.get("version") == SEED_VERSION:
        return  # already migrated to the current shape

    # One-time cleanup deletion of legacy/fallback collections:
    obsolete_collections = [
        "hazards",
        "observations",
    ] + [f"neo4j_{c}" for c in [
        "confirmations", "reports", "hazards", "observations",
        "warnings", "vehicles", "road_segments", "approaching"
    ]]

    for coll_name in obsolete_collections:
        await getattr(db, coll_name).delete_many({})


    # Migrate nearby vehicles.
    for v in SEED_VEHICLES:
        await db.nearby_vehicles.replace_one({"id": v["id"]}, dict(v), upsert=True)

    # Record applied seed version.
    await db.sentinel_meta.replace_one(
        {"id": "seed"},
        {"id": "seed", "version": SEED_VERSION},
        upsert=True,
    )



async def _seed_demo_graph_vehicles() -> None:
    for v in SEED_VEHICLES:
        try:
            await _perception_graph.upsert_vehicle_approach(
                vehicle_id=v["id"],
                vehicle_label=v["label"],
                road_segment_id="gst",
                road_segment_name="GST Road Northbound",
            )
        except Exception as e:
            logger.error(f"Demo graph vehicle seeding failed: {type(e).__name__}")
            raise RuntimeError("Demo graph vehicle seeding failed") from None


async def _seed_demo_graph_hazard() -> None:
    """Seed the baseline demo hazard into the perception graph.

    Uses only public PerceptionGraphService operations.
    Takes confirmed/reportedIncorrect from deterministic SEED_HAZARDS constants.
    Lets graph source-count rules derive confidence naturally (60 for 1 source).
    Creates no Warning nodes.  Idempotent.
    """
    import time
    hz = SEED_HAZARDS[0]

    lat = hz["location"]["latitude"]
    lon = hz["location"]["longitude"]
    current_time = time.time()
    timestamp = current_time - hz["observedSecondsAgo"]

    hazard_fields = {
        "distanceMeters": float(hz["distanceMeters"]),
        "direction": hz["direction"],
        "recommendedAction": hz["recommendedAction"],
        "risk": hz["risk"],
        "visibilityState": hz["visibilityState"],
        "sourceType": hz["sourceType"],
        "routeRelevance": hz["routeRelevance"],
        "polygon": hz["polygon"],
        "confirmed": int(hz.get("confirmed", 0)),
        "reportedIncorrect": int(hz.get("reportedIncorrect", 0)),
        "status": "active",
    }

    try:
        await _perception_graph.upsert_observation_and_hazard(
            observation_id="obs-seed-hz-002",
            vehicle_id="v-3",
            vehicle_label="Sentinel-F4",
            hazard_id="hz-002",
            hazard_type="pothole",
            hazard_label="Deep Pothole",
            latitude=lat,
            longitude=lon,
            road_segment_id="gst",
            road_segment_name="GST Road Northbound",
            timestamp=timestamp,
            hazard_fields=hazard_fields,
        )
    except Exception as e:
        logger.error(f"Demo graph hazard seeding failed: {type(e).__name__}")
        raise RuntimeError("Demo graph hazard seeding failed") from None


def _graph_hazard_to_api(hz: dict, current_time: float) -> dict:
    updated_at = hz.get("updated_at", 0.0)
    observed_seconds = max(0, int(current_time - updated_at))

    loc = hz.get("location") or {"latitude": 0.0, "longitude": 0.0}
    location = {
        "latitude": float(loc.get("latitude", 0.0)),
        "longitude": float(loc.get("longitude", 0.0))
    }

    poly = hz.get("polygon")
    polygon = None
    if poly:
        polygon = [{"latitude": float(p.get("latitude", 0.0)), "longitude": float(p.get("longitude", 0.0))} for p in poly]

    return {
        "id": hz.get("id", ""),
        "type": hz.get("type", ""),
        "label": hz.get("label", ""),
        "location": location,
        "polygon": polygon,
        "distanceMeters": float(hz.get("distanceMeters") if hz.get("distanceMeters") is not None else 0.0),
        "confidence": int(hz.get("confidence") if hz.get("confidence") is not None else 60),
        "sources": int(hz.get("sources") if hz.get("sources") is not None else 1),
        "observedSecondsAgo": observed_seconds,
        "direction": hz.get("direction") or "Northbound lane",
        "recommendedAction": hz.get("recommendedAction") or "Move left",
        "risk": hz.get("risk") or "medium",
        "visibilityState": hz.get("visibilityState") or "hidden",
        "sourceType": hz.get("sourceType") or "shared_vehicle",
        "routeRelevance": hz.get("routeRelevance") or "medium",
        "confirmed": int(hz.get("confirmed", 0)),
        "reportedIncorrect": int(hz.get("reportedIncorrect", 0)),
    }


# ======================= App =======================
@asynccontextmanager
async def lifespan(app: FastAPI):
    from services.replay_activation_service import ReplayActivationStore, set_store
    store = ReplayActivationStore(db)
    set_store(store)

    # 1. Initialize perception graph
    await _perception_graph.initialize()
    # 2. Mongo compatibility seed (hazards cache + nearby vehicles)
    await ensure_seed()
    # 3. Graph vehicle + hazard baseline seed
    await _seed_demo_graph_vehicles()
    await _seed_demo_graph_hazard()
    # 4. Remaining services
    await _training_samples.initialize()
    await _demo_replay.initialize()
    try:
        yield
    finally:
        await _perception_graph.close()
        await _demo_replay.close()
        client.close()


app = FastAPI(lifespan=lifespan)
api_router = APIRouter(prefix="/api")

# Attach service references for route dependency injection
app.state.training_sample_service = _training_samples
app.state.media_service = _media_service
app.state.demo_replay_service = _demo_replay
app.state.vision_inference_service = _vision_inference
app.state.perception_graph_service = _perception_graph


from routes.training_samples import router as training_samples_router
from routes.media import router as media_router
from routes.demo_replay import router as demo_replay_router
from routes.demo_replay_evidence import router as demo_replay_evidence_router
from routes.demo_replay_graph_verify import router as demo_replay_graph_verify_router

api_router.include_router(training_samples_router)
api_router.include_router(media_router)
api_router.include_router(demo_replay_router)
api_router.include_router(demo_replay_evidence_router)
api_router.include_router(demo_replay_graph_verify_router)


@api_router.get("/")
async def root():
    return {"message": "Sentinel API online"}


@api_router.get("/sentinel/status", response_model=SentinelStatus)
async def get_status():
    await ensure_seed()
    return SentinelStatus(
        connected=True,
        gps_locked=True,
        network="4G",
        speed_kmh=42,
        road_name="GST Road Northbound",
        heading="N",
        sentinel_vehicles_nearby=await db.nearby_vehicles.count_documents({}),
    )


@api_router.get("/sentinel/hazards", response_model=List[Hazard])
async def list_hazards():
    import time
    try:
        raw_hazards = await _perception_graph.list_hazards(limit=100)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail="Graph database error")
    except Exception as e:
        raise HTTPException(status_code=503, detail="Unexpected database error")

    current_time = time.time()
    api_hazards = [_graph_hazard_to_api(h, current_time) for h in raw_hazards]
    return [Hazard(**h) for h in api_hazards]


@api_router.get("/sentinel/nearby-vehicles", response_model=List[NearbyVehicle])
async def list_nearby_vehicles():
    await ensure_seed()
    docs = await db.nearby_vehicles.find({}, {"_id": 0}).to_list(100)
    return [NearbyVehicle(**d) for d in docs]


@api_router.get("/sentinel/world-model", response_model=WorldModel)
async def get_world_model(
    latitude: Optional[float] = Query(default=None),
    longitude: Optional[float] = Query(default=None),
    heading: Optional[float] = Query(default=None),
    radius_m: Optional[float] = Query(default=None, gt=0),
):
    """Return the structured local world model for Ghost Vision.

    With no coordinates this is the deterministic demo scenario. With live
    coordinates it uses real ego telemetry plus a clearly synthetic local road
    context; shared hazards remain stored at absolute coordinates.
    """
    import time
    try:
        raw_hazards = await _perception_graph.list_hazards(limit=100)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail="Graph database error")
    except Exception as e:
        raise HTTPException(status_code=503, detail="Unexpected database error")

    current_time = time.time()
    vehicles_docs = await db.nearby_vehicles.find({}, {"_id": 0}).to_list(100)

    if (latitude is None) != (longitude is None):
        raise HTTPException(status_code=400, detail="latitude and longitude must be supplied together")

    if latitude is not None and longitude is not None:
        live_radius = min(radius_m or DEFAULT_LIVE_RADIUS_M, MAX_LIVE_RADIUS_M)
        ego_location = {"latitude": latitude, "longitude": longitude}
        live_hazards = []
        # Filter on raw graph status BEFORE API conversion
        for raw_hz in raw_hazards:
            if raw_hz.get("status", "active") != "active":
                continue
            h_location = raw_hz.get("location")
            if not h_location:
                continue
            distance = haversine_meters(
                latitude,
                longitude,
                float(h_location.get("latitude", 0.0)),
                float(h_location.get("longitude", 0.0)),
            )
            if distance > live_radius:
                continue
            api_hz = _graph_hazard_to_api(raw_hz, current_time)
            api_hz["distanceMeters"] = round(distance, 1)
            api_hz["routeRelevance"] = route_relevance_for_distance(distance)
            live_hazards.append(api_hz)

        live_hazards.sort(key=lambda h: h["distanceMeters"])
        origin = live_context_origin(latitude, longitude)
        context = build_live_synthetic_context(origin)
        return {
            "scenarioId": "live-gps-synthetic-context-v1",
            "telemetrySource": "live",
            "ego": {
                "location": ego_location,
                "headingDegrees": normalize_heading_degrees(heading),
                "speedKmh": 0,
            },
            **context,
            "nearbyVehicles": vehicles_docs,
            "hazards": live_hazards,
        }

    # Demo mode: include all graph hazards with deterministic ordering
    hazards_docs = [_graph_hazard_to_api(h, current_time) for h in raw_hazards]
    return {
        "scenarioId": "gst-northbound-blind-turn-v1",
        "telemetrySource": "demo",
        "ego": {"location": EGO, "headingDegrees": 8, "speedKmh": 42},
        "mapCenter": off(80, 0),
        "mapBounds": {"southWest": off(-300, -180), "northEast": off(460, 180)},
        "roadCorridor": ROAD_CORRIDOR,
        "roads": [
            {"id": "gst", "path": GST_ROAD, "name": "GST Road Northbound", "lanes": 2},
            {"id": "side", "path": SIDE_ROAD, "name": "Velachery Link Rd"},
            {"id": "service", "path": SERVICE_ROAD, "name": "Service Rd"},
        ],
        "buildings": BUILDINGS,
        "occupiedRegions": SEED_OCCUPIED,
        "nearbyVehicles": vehicles_docs,
        "hazards": hazards_docs,
    }


@api_router.post("/sentinel/hazards/{hazard_id}/confirm", response_model=HazardActionResponse)
async def confirm_hazard(hazard_id: str):
    try:
        res = await _perception_graph.record_hazard_feedback(
            hazard_id=hazard_id,
            vehicle_id="v-ego",
            vehicle_label="Ego Vehicle",
            feedback_type="confirm",
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail="Invalid feedback request")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail="Graph database error")
    except Exception as e:
        raise HTTPException(status_code=503, detail="Unexpected database error")

    if not res:
        raise HTTPException(status_code=404, detail="Hazard not found")

    return HazardActionResponse(
        id=res["id"], confirmed=res.get("confirmed", 0), reportedIncorrect=res.get("reportedIncorrect", 0)
    )


@api_router.post("/sentinel/hazards/{hazard_id}/report-incorrect", response_model=HazardActionResponse)
async def report_incorrect(hazard_id: str):
    try:
        res = await _perception_graph.record_hazard_feedback(
            hazard_id=hazard_id,
            vehicle_id="v-ego",
            vehicle_label="Ego Vehicle",
            feedback_type="report_incorrect",
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail="Invalid feedback request")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail="Graph database error")
    except Exception as e:
        raise HTTPException(status_code=503, detail="Unexpected database error")

    if not res:
        raise HTTPException(status_code=404, detail="Hazard not found")

    return HazardActionResponse(
        id=res["id"], confirmed=res.get("confirmed", 0), reportedIncorrect=res.get("reportedIncorrect", 0)
    )


@api_router.post("/sentinel/demo/observation")
async def demo_observation(req: DemoObservationRequest):
    from workflows.hazard_workflow import LocalWorkflowRunner
    runner = LocalWorkflowRunner(
        graph_service=_perception_graph,
        ego_location=EGO,
    )
    res = await runner.process_observation(req.model_dump())
    return res


@api_router.post("/sentinel/demo/reset")
async def demo_reset():
    # 1. Reset the graph
    try:
        await _perception_graph.reset_demo_data()
    except Exception as e:
        logger.error(f"PerceptionGraphService reset failed: {type(e).__name__}")
        raise HTTPException(
            status_code=503,
            detail="Demo reset failed due to graph database error"
        )
    # 2. Clear temporary Mongo compatibility data and obsolete collections
    obsolete_collections = [
        "hazards",
        "observations",
    ] + [f"neo4j_{c}" for c in [
        "confirmations", "reports", "hazards", "observations",
        "warnings", "vehicles", "road_segments", "approaching"
    ]]

    for coll_name in obsolete_collections:
        await getattr(db, coll_name).delete_many({})

    await db.sentinel_meta.delete_many({})
    # 3. Restore Mongo compatibility + nearby vehicle telemetry
    await ensure_seed()
    # 4-5. Seed graph vehicles and baseline hazard (exactly once)
    try:
        await _seed_demo_graph_vehicles()
        await _seed_demo_graph_hazard()
    except Exception as e:
        logger.error(f"Graph seeding failed: {type(e).__name__}")
        raise HTTPException(
            status_code=503,
            detail="Demo reset failed due to graph database error"
        )
    return {"message": "Demo data reset successfully"}


@api_router.get("/sentinel/perception-graph")
async def get_perception_graph(
    hazard_id: Optional[str] = Query(default=None),
    limit: int = Query(default=25),
):
    """Return the perception provenance graph for Sentinel hazards.

    Query parameters:
      - hazard_id: optional focused hazard ID
      - limit: maximum number of hazard roots (1-100, default 25)
    """
    try:
        graph = await _perception_graph.build_graph(hazard_id=hazard_id, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return graph


@api_router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "graphMode": _perception_graph._mode,
        "mongoReachable": MONGO_REACHABLE
    }


app.include_router(api_router)

cors_origins_env = os.environ.get("CORS_ORIGINS", "").strip()
if not cors_origins_env:
    allow_origins = ["*"]
else:
    allow_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]

# Wildcard "*" blocks credentials
if "*" in allow_origins:
    allow_credentials = False
else:
    allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_credentials=allow_credentials,
    allow_origins=allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

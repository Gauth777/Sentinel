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


from services.perception_graph_service import PerceptionGraphService
_perception_graph = PerceptionGraphService()


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
SEED_VERSION = 3
DEPRECATED_SEED_HAZARD_IDS = ("hz-001",)


async def ensure_seed() -> None:
    """Idempotent demo-data migration.

    - Tracks the applied seed version in db.sentinel_meta (single doc, id='seed').
    - When the persisted version differs from SEED_VERSION (or is missing) the
      known demo documents (hz-*, v-*) are upserted by `id`, replacing any
      legacy-shape records left over from earlier app versions.
    - Repeated startup with the same SEED_VERSION is a no-op fast path.
    - Confirmed/reportedIncorrect counters are preserved across migrations.
    """
    meta = await db.sentinel_meta.find_one({"id": "seed"})
    if meta and meta.get("version") == SEED_VERSION:
        return  # already migrated to the current shape
    # Remove demo hazards retired from the baseline scenario.
    for hazard_id in DEPRECATED_SEED_HAZARD_IDS:
        await db.hazards.delete_many({"id": hazard_id})
        await db.observations.delete_many({"hazard_id": hazard_id})

    from services.warning_service import WarningService
    # Migrate hazards: upsert each known demo hazard, keep counters if present.
    for hz in SEED_HAZARDS:
        existing = await db.hazards.find_one({"id": hz["id"]}, {"_id": 0}) or {}
        merged = dict(hz)
        # Preserve counters from prior records when present.
        merged["confirmed"] = int(existing.get("confirmed", hz.get("confirmed", 0)) or 0)
        merged["reportedIncorrect"] = int(existing.get("reportedIncorrect", hz.get("reportedIncorrect", 0)) or 0)
        merged["status"] = "active"
        merged["warnings"] = WarningService.generate_warning_texts(
            hz["type"],
            int(hz["distanceMeters"]),
            hz["recommendedAction"]
        )
        await db.hazards.replace_one({"id": hz["id"]}, merged, upsert=True)

    # Migrate nearby vehicles.
    for v in SEED_VEHICLES:
        await db.nearby_vehicles.replace_one({"id": v["id"]}, dict(v), upsert=True)

    # Record applied seed version.
    await db.sentinel_meta.replace_one(
        {"id": "seed"},
        {"id": "seed", "version": SEED_VERSION},
        upsert=True,
    )


# ======================= App =======================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await _perception_graph.initialize()
    try:
        yield
    finally:
        await _perception_graph.close()
        client.close()


app = FastAPI(lifespan=lifespan)
api_router = APIRouter(prefix="/api")


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
    await ensure_seed()
    docs = await db.hazards.find({}, {"_id": 0}).to_list(100)
    return [Hazard(**d) for d in docs]


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
    await ensure_seed()
    hazards_docs = await db.hazards.find({}, {"_id": 0}).to_list(100)
    vehicles_docs = await db.nearby_vehicles.find({}, {"_id": 0}).to_list(100)

    if (latitude is None) != (longitude is None):
        raise HTTPException(status_code=400, detail="latitude and longitude must be supplied together")

    if latitude is not None and longitude is not None:
        live_radius = min(radius_m or DEFAULT_LIVE_RADIUS_M, MAX_LIVE_RADIUS_M)
        ego_location = {"latitude": latitude, "longitude": longitude}
        live_hazards = []
        for hazard in hazards_docs:
            if hazard.get("status", "active") != "active":
                continue
            h_location = hazard.get("location")
            if not h_location:
                continue
            distance = haversine_meters(
                latitude,
                longitude,
                h_location["latitude"],
                h_location["longitude"],
            )
            if distance > live_radius:
                continue
            updated_hazard = dict(hazard)
            updated_hazard["distanceMeters"] = round(distance, 1)
            updated_hazard["routeRelevance"] = route_relevance_for_distance(distance)
            live_hazards.append(updated_hazard)

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
    from services.neo4j_service import Neo4jService
    vehicle_id = "v-ego"
    await Neo4jService.record_confirmation(vehicle_id, hazard_id)
    votes = await Neo4jService.get_community_votes(hazard_id)
    res = await db.hazards.find_one_and_update(
        {"id": hazard_id},
        {"$set": {"confirmed": votes["confirmed"], "reportedIncorrect": votes["reportedIncorrect"]}},
        projection={"_id": 0},
        return_document=True,
    )
    if res:
        sources_count = res.get("sources", 1)
        base_confidence = 60 + min(40, (sources_count - 1) * 20)
        confirmed = res.get("confirmed", 0)
        reported_incorrect = res.get("reportedIncorrect", 0)
        confidence = max(0, min(100, int(base_confidence + confirmed * 10 - reported_incorrect * 15)))
        status = "active"
        if confidence <= 0 or reported_incorrect >= 5:
            status = "resolved"
        res = await db.hazards.find_one_and_update(
            {"id": hazard_id},
            {"$set": {"confidence": confidence, "status": status}},
            projection={"_id": 0},
            return_document=True,
        )
    if not res:
        raise HTTPException(status_code=404, detail="Hazard not found")
    return HazardActionResponse(
        id=res["id"], confirmed=res.get("confirmed", 0), reportedIncorrect=res.get("reportedIncorrect", 0)
    )


@api_router.post("/sentinel/hazards/{hazard_id}/report-incorrect", response_model=HazardActionResponse)
async def report_incorrect(hazard_id: str):
    from services.neo4j_service import Neo4jService
    vehicle_id = "v-ego"
    await Neo4jService.record_report_incorrect(vehicle_id, hazard_id)
    votes = await Neo4jService.get_community_votes(hazard_id)
    res = await db.hazards.find_one_and_update(
        {"id": hazard_id},
        {"$set": {"confirmed": votes["confirmed"], "reportedIncorrect": votes["reportedIncorrect"]}},
        projection={"_id": 0},
        return_document=True,
    )
    if res:
        sources_count = res.get("sources", 1)
        base_confidence = 60 + min(40, (sources_count - 1) * 20)
        confirmed = res.get("confirmed", 0)
        reported_incorrect = res.get("reportedIncorrect", 0)
        confidence = max(0, min(100, int(base_confidence + confirmed * 10 - reported_incorrect * 15)))
        status = "active"
        if confidence <= 0 or reported_incorrect >= 5:
            status = "resolved"
        res = await db.hazards.find_one_and_update(
            {"id": hazard_id},
            {"$set": {"confidence": confidence, "status": status}},
            projection={"_id": 0},
            return_document=True,
        )
    if not res:
        raise HTTPException(status_code=404, detail="Hazard not found")
    return HazardActionResponse(
        id=res["id"], confirmed=res.get("confirmed", 0), reportedIncorrect=res.get("reportedIncorrect", 0)
    )


@api_router.post("/sentinel/demo/observation")
async def demo_observation(req: DemoObservationRequest):
    from workflows.hazard_workflow import LocalWorkflowRunner
    runner = LocalWorkflowRunner()
    res = await runner.process_observation(req.model_dump())
    return res


@api_router.post("/sentinel/demo/reset")
async def demo_reset():
    try:
        await _perception_graph.reset_demo_data()
    except Exception as e:
        logger.warning(f"PerceptionGraphService reset failed: {type(e).__name__}")
    await db.hazards.delete_many({})
    await db.observations.delete_many({})
    await db.sentinel_meta.delete_many({})
    await ensure_seed()
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


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from contextlib import asynccontextmanager
import os
import logging
import math
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Literal


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]


# ======================= Models =======================
class GeoPoint(BaseModel):
    latitude: float
    longitude: float


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


def off(dN: float, dE: float) -> dict:
    return {"latitude": EGO["latitude"] + dN * LAT_M, "longitude": EGO["longitude"] + dE * LON_M}


def rect(cN: float, cE: float, w: float, h: float) -> list:
    return [
        off(cN - h / 2, cE - w / 2),
        off(cN - h / 2, cE + w / 2),
        off(cN + h / 2, cE + w / 2),
        off(cN + h / 2, cE - w / 2),
    ]


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
        "id": "hz-001",
        "type": "stationary_vehicle",
        "label": "Stationary Vehicle Ahead",
        "location": off(180, 4),
        "polygon": rect(180, 4, 5, 9),
        "distanceMeters": 180,
        "confidence": 91,
        "sources": 2,
        "observedSecondsAgo": 8,
        "direction": "Northbound lane",
        "recommendedAction": "Reduce speed",
        "risk": "high",
        "visibilityState": "hidden",
        "sourceType": "shared_vehicle",
        "routeRelevance": "high",
        "confirmed": 0,
        "reportedIncorrect": 0,
    },
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


async def ensure_seed() -> None:
    if await db.hazards.count_documents({}) == 0:
        await db.hazards.insert_many([dict(h) for h in SEED_HAZARDS])
    if await db.nearby_vehicles.count_documents({}) == 0:
        await db.nearby_vehicles.insert_many([dict(v) for v in SEED_VEHICLES])


# ======================= App =======================
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
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


@api_router.get("/sentinel/world-model")
async def get_world_model():
    """Return the structured local world model for Ghost Vision.
    telemetrySource is currently 'demo' (deterministic mock); future builds
    will switch to 'live' once a real perception pipeline is connected."""
    await ensure_seed()
    hazards_docs = await db.hazards.find({}, {"_id": 0}).to_list(100)
    vehicles_docs = await db.nearby_vehicles.find({}, {"_id": 0}).to_list(100)

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
    res = await db.hazards.find_one_and_update(
        {"id": hazard_id},
        {"$inc": {"confirmed": 1}},
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
    res = await db.hazards.find_one_and_update(
        {"id": hazard_id},
        {"$inc": {"reportedIncorrect": 1}},
        projection={"_id": 0},
        return_document=True,
    )
    if not res:
        raise HTTPException(status_code=404, detail="Hazard not found")
    return HazardActionResponse(
        id=res["id"], confirmed=res.get("confirmed", 0), reportedIncorrect=res.get("reportedIncorrect", 0)
    )


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

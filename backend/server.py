from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime, timezone, timedelta


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")


# ========= Models =========
class SentinelStatus(BaseModel):
    connected: bool
    gps_locked: bool
    network: str  # "5G" | "4G" | "3G" | "OFFLINE"
    speed_kmh: int
    road_name: str
    heading: str  # "N" | "NE" | ...
    sentinel_vehicles_nearby: int


class NearbyVehicle(BaseModel):
    id: str
    # normalised coords on the tactical map (0..1), origin at top-left
    x: float
    y: float
    heading_deg: float
    label: str  # e.g., "Sentinel-A8"


class Hazard(BaseModel):
    id: str
    type: str  # "stationary_vehicle" | "pothole" | "debris" | "accident" | "pedestrian"
    label: str
    distance_m: int
    confidence: int  # 0-100
    sources: int
    observed_seconds_ago: int
    direction: str  # "Northbound lane" etc.
    recommended_action: str
    risk: str  # "high" | "medium" | "low"
    # normalised tactical map coords (0..1)
    x: float
    y: float
    confirmed: int = 0
    reported_incorrect: int = 0


class HazardActionResponse(BaseModel):
    id: str
    confirmed: int
    reported_incorrect: int


# ========= Seed data =========
SEED_HAZARDS = [
    {
        "id": "hz-001",
        "type": "stationary_vehicle",
        "label": "Stationary Vehicle Ahead",
        "distance_m": 180,
        "confidence": 91,
        "sources": 2,
        "observed_seconds_ago": 8,
        "direction": "Northbound lane",
        "recommended_action": "Reduce speed",
        "risk": "high",
        "x": 0.52,
        "y": 0.22,
        "confirmed": 0,
        "reported_incorrect": 0,
    },
    {
        "id": "hz-002",
        "type": "pothole",
        "label": "Deep Pothole",
        "distance_m": 340,
        "confidence": 76,
        "sources": 1,
        "observed_seconds_ago": 42,
        "direction": "Northbound lane",
        "recommended_action": "Move left",
        "risk": "medium",
        "x": 0.46,
        "y": 0.10,
        "confirmed": 0,
        "reported_incorrect": 0,
    },
]

SEED_VEHICLES = [
    {"id": "v-1", "x": 0.34, "y": 0.55, "heading_deg": 0, "label": "Sentinel-A8"},
    {"id": "v-2", "x": 0.66, "y": 0.62, "heading_deg": 0, "label": "Sentinel-C2"},
    {"id": "v-3", "x": 0.58, "y": 0.40, "heading_deg": 350, "label": "Sentinel-F4"},
    {"id": "v-4", "x": 0.42, "y": 0.32, "heading_deg": 10, "label": "Sentinel-K9"},
]


async def ensure_seed():
    if await db.hazards.count_documents({}) == 0:
        await db.hazards.insert_many([dict(h) for h in SEED_HAZARDS])
    if await db.nearby_vehicles.count_documents({}) == 0:
        await db.nearby_vehicles.insert_many([dict(v) for v in SEED_VEHICLES])


# ========= Routes =========
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
        id=res["id"], confirmed=res.get("confirmed", 0), reported_incorrect=res.get("reported_incorrect", 0)
    )


@api_router.post("/sentinel/hazards/{hazard_id}/report-incorrect", response_model=HazardActionResponse)
async def report_incorrect(hazard_id: str):
    res = await db.hazards.find_one_and_update(
        {"id": hazard_id},
        {"$inc": {"reported_incorrect": 1}},
        projection={"_id": 0},
        return_document=True,
    )
    if not res:
        raise HTTPException(status_code=404, detail="Hazard not found")
    return HazardActionResponse(
        id=res["id"], confirmed=res.get("confirmed", 0), reported_incorrect=res.get("reported_incorrect", 0)
    )


# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

import type { GeoPoint, RouteRelevance, WorldModel, WorldPolygon } from "../types/sentinel";
import { boundsAround, haversineMeters } from "../components/ghost-vision/projection";

export const LIVE_WORLD_RADIUS_M = 800;
export const LIVE_REFRESH_DISTANCE_M = 12;
export const LIVE_REFRESH_INTERVAL_MS = 4500;
export const LIVE_OBSERVATION_DISTANCE_M = 120;

export type LiveTelemetryInput = {
  location: GeoPoint;
  headingDegrees?: number | null;
};

export type RefreshSnapshot = {
  location: GeoPoint;
  timestampMs: number;
};

export function normalizeHeadingDegrees(
  headingDegrees: number | null | undefined,
  fallback = 0
): number {
  if (typeof headingDegrees !== "number" || !Number.isFinite(headingDegrees) || headingDegrees < 0) {
    return fallback;
  }
  return ((headingDegrees % 360) + 360) % 360;
}

export function routeRelevanceForDistance(distanceMeters: number): RouteRelevance {
  if (distanceMeters <= 180) return "high";
  if (distanceMeters <= 400) return "medium";
  if (distanceMeters <= 800) return "low";
  return "none";
}

export function destinationPoint(
  origin: GeoPoint,
  bearingDegrees: number,
  distanceMeters: number
): GeoPoint {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const toDeg = (r: number) => (r * 180) / Math.PI;
  const radius = 6_371_000;
  const bearing = toRad(bearingDegrees);
  const lat1 = toRad(origin.latitude);
  const lon1 = toRad(origin.longitude);
  const angularDistance = distanceMeters / radius;

  const lat2 = Math.asin(
    Math.sin(lat1) * Math.cos(angularDistance) +
      Math.cos(lat1) * Math.sin(angularDistance) * Math.cos(bearing)
  );
  const lon2 =
    lon1 +
    Math.atan2(
      Math.sin(bearing) * Math.sin(angularDistance) * Math.cos(lat1),
      Math.cos(angularDistance) - Math.sin(lat1) * Math.sin(lat2)
    );

  return {
    latitude: toDeg(lat2),
    longitude: ((toDeg(lon2) + 540) % 360) - 180,
  };
}

export function localRect(center: GeoPoint, bearingDegrees: number, widthMeters: number, heightMeters: number): WorldPolygon {
  const forward = heightMeters / 2;
  const side = widthMeters / 2;
  const front = destinationPoint(center, bearingDegrees, forward);
  const back = destinationPoint(center, bearingDegrees + 180, forward);
  return [
    destinationPoint(back, bearingDegrees - 90, side),
    destinationPoint(back, bearingDegrees + 90, side),
    destinationPoint(front, bearingDegrees + 90, side),
    destinationPoint(front, bearingDegrees - 90, side),
  ];
}

export function withLiveTelemetry(worldModel: WorldModel, telemetry: LiveTelemetryInput): WorldModel {
  const heading = normalizeHeadingDegrees(telemetry.headingDegrees, worldModel.ego.headingDegrees);
  const hazards = worldModel.hazards.map((hazard) => {
    const distanceMeters = Math.round(haversineMeters(telemetry.location, hazard.location) * 10) / 10;
    return {
      ...hazard,
      distanceMeters,
      routeRelevance: routeRelevanceForDistance(distanceMeters),
    };
  });
  return {
    ...worldModel,
    telemetrySource: telemetry.location ? "live" : worldModel.telemetrySource,
    ego: {
      ...worldModel.ego,
      location: telemetry.location,
      headingDegrees: heading,
    },
    mapBounds: boundsAround(telemetry.location, 280),
    hazards,
  };
}

export function shouldRefreshWorldModel(
  last: RefreshSnapshot | null,
  next: RefreshSnapshot,
  movementThresholdMeters = LIVE_REFRESH_DISTANCE_M,
  intervalMs = LIVE_REFRESH_INTERVAL_MS
): boolean {
  if (!last) return true;
  const moved = haversineMeters(last.location, next.location);
  const elapsed = next.timestampMs - last.timestampMs;
  return moved >= movementThresholdMeters || elapsed >= intervalMs;
}

export function buildLiveObservation(
  telemetry: LiveTelemetryInput,
  id = "obs-live-demo-stationary-ahead"
) {
  const heading = normalizeHeadingDegrees(telemetry.headingDegrees, 0);
  const location = destinationPoint(telemetry.location, heading, LIVE_OBSERVATION_DISTANCE_M);
  return {
    id,
    type: "stationary_vehicle",
    label: "Stationary Vehicle Ahead",
    location,
    polygon: localRect(location, heading, 4.5, 8),
    sourceVehicleId: "v-live-observer",
    vehicleLabel: "Live Sentinel Observer",
  };
}

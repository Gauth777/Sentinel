// Deterministic Demo Scenario centered on GST Road, Tambaram (Chennai).
// Used when GPS / network / map provider are unavailable. Coords are real
// geographic positions so the same data renders correctly in Live Geo Mode.

import type { WorldModel } from "@/src/types/sentinel";

const C = { latitude: 12.9436, longitude: 80.1502 }; // ego start

// Offset helper: ~111_111 m per degree lat; lon shrinks by cos(lat).
const LAT_M = 1 / 111_111;
const LON_M = 1 / (111_111 * Math.cos((C.latitude * Math.PI) / 180));
const off = (dN_m: number, dE_m: number) => ({
  latitude: C.latitude + dN_m * LAT_M,
  longitude: C.longitude + dE_m * LON_M,
});

// Generate a small road network: main GST Road (N-S) + a side road (E-W).
const gstRoad = [
  off(-260, 0),
  off(-120, -4),
  off(0, 0),
  off(140, 6),
  off(280, 2),
  off(420, 10),
];
const sideRoad = [off(60, -160), off(60, -40), off(60, 40), off(60, 180)];
const serviceRoad = [off(-80, 80), off(40, 78), off(180, 82), off(320, 86)];

// Building footprints (very rough rectangles) on both sides of the main road.
const rect = (cN: number, cE: number, w: number, h: number) => [
  off(cN - h / 2, cE - w / 2),
  off(cN - h / 2, cE + w / 2),
  off(cN + h / 2, cE + w / 2),
  off(cN + h / 2, cE - w / 2),
];
const buildings = [
  { id: "b1", polygon: rect(-50, -45, 32, 60) },
  { id: "b2", polygon: rect(40, -50, 28, 70) },
  { id: "b3", polygon: rect(150, -55, 40, 90) },
  { id: "b4", polygon: rect(280, -48, 30, 80) },
  { id: "b5", polygon: rect(-60, 55, 32, 70) },
  { id: "b6", polygon: rect(90, 60, 28, 80) },
  { id: "b7", polygon: rect(210, 60, 36, 60) },
  { id: "b8", polygon: rect(360, 55, 30, 70) },
];

// Safe driving corridor — a strip following the main road.
const corridor: { latitude: number; longitude: number }[] = [];
gstRoad.forEach((p) => corridor.push({ latitude: p.latitude, longitude: p.longitude - 6 * LON_M * 111_111 / 111_111 }));
const gstRoadReverse = [...gstRoad].reverse();
gstRoadReverse.forEach((p) => corridor.push({ latitude: p.latitude, longitude: p.longitude + 6 * LON_M * 111_111 / 111_111 }));

export const demoScenario: WorldModel = {
  scenarioId: "gst-northbound-blind-turn-v1",
  telemetrySource: "demo",
  ego: { location: C, headingDegrees: 8, speedKmh: 42 },
  mapCenter: off(80, 0),
  mapBounds: {
    southWest: off(-300, -180),
    northEast: off(460, 180),
  },
  roadCorridor: corridor,
  roads: [
    { id: "gst", path: gstRoad, name: "GST Road Northbound", lanes: 2 },
    { id: "side", path: sideRoad, name: "Velachery Link Rd" },
    { id: "service", path: serviceRoad, name: "Service Rd" },
  ],
  buildings,
  occupiedRegions: [
    {
      id: "or-1",
      sourceType: "local_sensor",
      visibilityState: "visible",
      objectType: "vehicle",
      polygon: rect(30, -3, 4, 8),
      center: off(30, -3),
      approximateDistanceMeters: 30,
      confidence: 88,
      motion: "moving",
      routeRelevance: "medium",
      observedSecondsAgo: 1,
      headingDegrees: 8,
      label: "Vehicle ahead",
    },
    {
      id: "or-2",
      sourceType: "local_sensor",
      visibilityState: "visible",
      objectType: "vehicle",
      polygon: rect(60, 4, 4, 8),
      center: off(60, 4),
      approximateDistanceMeters: 60,
      confidence: 81,
      motion: "moving",
      routeRelevance: "low",
      observedSecondsAgo: 1,
      headingDegrees: 6,
      label: "Vehicle ahead-right",
    },
    {
      id: "or-3",
      sourceType: "local_sensor",
      visibilityState: "visible",
      objectType: "unknown",
      polygon: rect(22, -12, 2, 2),
      center: off(22, -12),
      approximateDistanceMeters: 24,
      confidence: 55,
      motion: "static",
      routeRelevance: "low",
      observedSecondsAgo: 2,
      label: "Unknown occupied region",
    },
    {
      id: "or-4",
      sourceType: "local_sensor",
      visibilityState: "uncertain",
      objectType: "road_obstruction",
      polygon: rect(95, 0, 5, 3),
      center: off(95, 0),
      approximateDistanceMeters: 95,
      confidence: 64,
      motion: "static",
      routeRelevance: "medium",
      observedSecondsAgo: 4,
      label: "Possible debris",
    },
  ],
  nearbyVehicles: [
    { id: "v-1", label: "Sentinel-A8", location: off(110, -8), heading_degrees: 8 },
    { id: "v-2", label: "Sentinel-C2", location: off(-40, 5), heading_degrees: 8 },
    { id: "v-3", label: "Sentinel-F4", location: off(220, -2), heading_degrees: 10 },
    { id: "v-4", label: "Sentinel-K9", location: off(80, 14), heading_degrees: 6 },
  ],
  hazards: [
    {
      id: "hz-001",
      type: "stationary_vehicle",
      label: "Stationary Vehicle Ahead",
      location: off(180, 4),
      polygon: rect(180, 4, 5, 9),
      distanceMeters: 180,
      confidence: 91,
      sources: 2,
      observedSecondsAgo: 8,
      direction: "Northbound lane",
      recommendedAction: "Reduce speed",
      risk: "high",
      visibilityState: "hidden",
      sourceType: "shared_vehicle",
      routeRelevance: "high",
      confirmed: 0,
      reportedIncorrect: 0,
    },
    {
      id: "hz-002",
      type: "pothole",
      label: "Deep Pothole",
      location: off(340, -3),
      distanceMeters: 340,
      confidence: 76,
      sources: 1,
      observedSecondsAgo: 42,
      direction: "Northbound lane",
      recommendedAction: "Move left",
      risk: "medium",
      visibilityState: "hidden",
      sourceType: "shared_vehicle",
      routeRelevance: "medium",
      confirmed: 0,
      reportedIncorrect: 0,
    },
  ],
};

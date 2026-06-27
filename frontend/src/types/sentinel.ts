// Sentinel structured world-model types.
// Schema is graph-friendly so it can migrate cleanly to Neo4j later
// (nodes: Vehicle, Hazard, OccupiedRegion, Observation; edges: OBSERVED_BY, ON_ROUTE, NEAR).

export type GeoPoint = { latitude: number; longitude: number };
export type WorldPolygon = GeoPoint[];

export type VisibilityState = "visible" | "hidden" | "uncertain";
export type SourceType = "local_sensor" | "shared_vehicle" | "demo";
export type Motion = "static" | "moving" | "unknown";
export type RouteRelevance = "none" | "low" | "medium" | "high";
export type Risk = "high" | "medium" | "low";
export type TelemetrySource = "live" | "cached" | "demo";

export type SentinelStatus = {
  connected: boolean;
  gps_locked: boolean;
  network: string;
  speed_kmh: number;
  road_name: string;
  heading: string;
  sentinel_vehicles_nearby: number;
};

export type NearbyVehicle = {
  id: string;
  label: string;
  location: GeoPoint;
  heading_degrees: number;
};

export type OccupiedRegion = {
  id: string;
  sourceType: SourceType;
  visibilityState: VisibilityState;
  objectType: "vehicle" | "pedestrian" | "road_obstruction" | "unknown";
  polygon: WorldPolygon;
  center: GeoPoint;
  approximateDistanceMeters: number;
  confidence: number;
  motion: Motion;
  routeRelevance: RouteRelevance;
  observedSecondsAgo: number;
  sourceVehicleId?: string;
  headingDegrees?: number;
  label?: string;
};

export type Hazard = {
  id: string;
  type: string;
  label: string;
  location: GeoPoint;
  polygon?: WorldPolygon;
  distanceMeters: number;
  confidence: number;
  sources: number;
  observedSecondsAgo: number;
  direction: string;
  recommendedAction: string;
  risk: Risk;
  visibilityState: VisibilityState;
  sourceType: SourceType;
  routeRelevance: RouteRelevance;
  confirmed: number;
  reportedIncorrect: number;
};

export type GraphNode = {
  id: string;
  type: "Vehicle" | "Observation" | "Hazard" | "RoadSegment" | "Warning";
  label: string;
  scenarioId: string;
  properties: Record<string, any>;
};

export type GraphEdge = {
  id: string;
  type: "OBSERVED" | "SUPPORTS" | "ON_ROAD" | "APPROACHING" | "TRIGGERED_WARNING" | "DELIVERED_TO";
  source: string;
  target: string;
  scenarioId: string;
  properties: Record<string, any>;
};

export type GraphSummaryFocus = {
  hazardId: string;
  sourceCount: number;
  confidence: number;
  warningCount: number;
} | null;

export type GraphSummary = {
  nodeCount: number;
  edgeCount: number;
  vehicleCount: number;
  observationCount: number;
  hazardCount: number;
  roadSegmentCount: number;
  warningCount: number;
  focus: GraphSummaryFocus;
};

export type GraphTimelineEvent = {
  eventId: string;
  timestamp: number;
  type: string;
  description: string;
};

export type PerceptionGraphResponse = {
  mode: "memory" | "neo4j";
  generatedAt: string;
  focusHazardId: string | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
  summary: GraphSummary;
  timeline: GraphTimelineEvent[];
};

export type WorldModel = {
  scenarioId: string;
  telemetrySource: TelemetrySource;
  ego: { location: GeoPoint; headingDegrees: number; speedKmh: number };
  mapCenter: GeoPoint;
  mapBounds: { southWest: GeoPoint; northEast: GeoPoint };
  roadCorridor: WorldPolygon; // safe driving corridor polygon
  roads: { id: string; path: WorldPolygon; name?: string; lanes?: number }[];
  buildings: { id: string; polygon: WorldPolygon }[];
  occupiedRegions: OccupiedRegion[];
  nearbyVehicles: NearbyVehicle[];
  hazards: Hazard[];
};

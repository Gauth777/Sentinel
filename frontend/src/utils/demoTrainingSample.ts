/** Convert an observer observation and returned hazard into a synthetic
 *  TrainingSampleCreate payload for the demo dataset.
 *
 *  All predictions are explicitly synthetic/demo-generated.
 *  No real VLM inference is performed.
 *  The sample is always synthetic; live telemetry context is preserved
 *  but never presented as live captured evidence.
 */
import type { GeoPoint } from "@/src/types/sentinel";
import type {
  TrainingSampleCreate,
  TrainingContext,
  TrainingMedia,
  TrainingModelInfo,
  PredictionLabels,
  TrainingProvenance,
  RoadType,
  TrafficDensity,
  RoadComplexity,
  HazardPresence,
  AnticipatedRisk,
  RecommendedAction,
  TelemetrySource,
} from "@/src/types/training";

export type DemoObservationInput = {
  observationId: string;
  hazardId: string;
  sourceVehicleId: string;
  location?: GeoPoint;
  headingDegrees?: number | null;
  speedKmh?: number | null;
  roadName?: string;
  routeDirection?: string;
  telemetryMode?: TelemetrySource;
  capturedAt?: string;
};

const DEMO_LABELS: PredictionLabels = {
  roadType: "urban_arterial" as RoadType,
  trafficDensity: "medium" as TrafficDensity,
  roadComplexity: "moderate" as RoadComplexity,
  hazardPresence: "yes" as HazardPresence,
  anticipatedRisk: "high" as AnticipatedRisk,
  recommendedAction: "slow_down" as RecommendedAction,
  confidence: 0.72,
};

const DEMO_MEDIA: TrainingMedia = {
  type: "image",
  uri: "demo://sentinel/gst-northbound/stationary-vehicle-frame",
  storageMode: "demo_uri",
};

const DEMO_MODEL: TrainingModelInfo = {
  provider: "demo",
  name: "sentinel-demo-baseline",
  version: "1",
  promptVersion: "demo-structured-v1",
  inferenceMode: "demo",
};

export function buildDemoTrainingSample(input: DemoObservationInput): TrainingSampleCreate {
  const now = input.capturedAt ?? new Date().toISOString();
  const telemetryMode = input.telemetryMode ?? "demo";

  const context: TrainingContext = {
    location: input.location ?? { latitude: 12.9452, longitude: 80.1506 },
    headingDegrees: input.headingDegrees ?? 8,
    speedKmh: input.speedKmh ?? 42,
    roadName: input.roadName ?? "GST Road",
    routeDirection: input.routeDirection ?? "Northbound",
    telemetrySource: telemetryMode,
  };

  // Provenance is always demo because the sample itself is synthetic.
  // Live telemetry coordinates may be in context, but the evidence is not live.
  const provenance: TrainingProvenance = {
    source: "demo",
    graphHazardId: input.hazardId,
    graphObservationId: input.observationId,
  };

  const rawId = `training-${input.observationId}`;
  const sampleId = sanitiseSampleId(rawId) || "training-observation";

  return {
    schemaVersion: "sentinel.training.v1",
    sampleId,
    observationId: input.observationId,
    hazardId: input.hazardId,
    sourceVehicleId: input.sourceVehicleId,
    capturedAt: now,
    context,
    media: DEMO_MEDIA,
    model: DEMO_MODEL,
    prediction: DEMO_LABELS,
    provenance,
  };
}

/** Sanitise a sampleId so it is URL-safe and deterministic. */
export function sanitiseSampleId(raw: string): string {
  return raw.replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 64);
}

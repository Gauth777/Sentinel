export type DemoReplayStatus = {
  mode: string;
  status: "ready" | "unconfigured" | "invalid";
  sampleCount: number;
  currentIndex: number;
  currentSampleId: string | null;
  loop: boolean;
};

export type DemoReplayLocation = {
  latitude: number;
  longitude: number;
};

export type DemoReplaySample = {
  sampleId: string;
  sequenceIndex: number;
  title: string;
  description: string;
  dashcamUrl: string;
  topviewUrl: string;
  location: DemoReplayLocation | null;
  headingDegrees: number | null;
  tags: string[];
};

export type DemoReplayCurrentResponse = {
  mode: string;
  sample: DemoReplaySample;
  sampleCount: number;
  currentIndex: number;
  hasNext: boolean;
};

export type DemoReplayAdvanceResponse = {
  previousSampleId: string;
  sample: DemoReplaySample;
  currentIndex: number;
  looped: boolean;
  sampleCount: number;
};

export type DemoReplayResetResponse = {
  sample: DemoReplaySample;
  currentIndex: number;
  sampleCount: number;
};

export type StructuredRoadPrediction = {
  roadType: "urban_arterial" | "residential" | "highway" | "junction";
  trafficDensity: "low" | "medium" | "high";
  roadComplexity: "simple" | "moderate" | "complex";
  hazardPresence: "yes" | "no";
  anticipatedRisk: "low" | "medium" | "high";
  recommendedAction:
    | "slow_down"
    | "maintain_speed"
    | "increase_attention"
    | "yield"
    | "prepare_to_stop"
    | "change_lane";
};

export type RuntimeHazardPrediction = {
  hazardType: string;
  hazardDescription: string;
  confidence?: number | null;
  warningText?: string | null;
};

export type DemoReplayInferenceResponse = {
  sampleId: string;
  inferenceId: string;
  model: string;
  inferenceMode: "live_qwen" | "cached_qwen";
  prediction: StructuredRoadPrediction;
  runtimeHazard: RuntimeHazardPrediction | null;
  latencyMs: number;
  activation: {
    activated: boolean;
    reason?: string | null;
    observationId: string | null;
    hazardId: string | null;
    warningTextGenerated: boolean;
    warningEventCreated: boolean;
  };
  evidence?: DemoReplayEvidenceResponse | null;
};

export type DemoReplayEvidenceResponse = {
  localSampleId: string;
  sourceSampleId: string | null;
  expectedLabels: StructuredRoadPrediction | null;
  actualPrediction: StructuredRoadPrediction | null;
  fieldMatches: Record<string, boolean> | null;
  correctFieldCount: number;
  totalFieldCount: number;
  inferenceMode: string;
  model: string;
  // compatibility
  sampleId?: string;
  sourceMapAvailable?: boolean;
};

export type DemoReplayGraphVerifyResponse = {
  graphBackend: "neo4j" | "memory" | "unknown";
  hazardId: string;
  observationId: string;
  exactHazardFound: boolean;
  exactObservationFound: boolean;
  exactSupportsRelationshipFound: boolean;
  nodeCount: number;
  edgeCount: number;
  relationshipTypes: string[];
  warningNodeFound: boolean;
  warningCount: number;
  verified: boolean;
  error?: string | null;
  // compatibility
  hazardNodeFound?: boolean;
  observationNodeFound?: boolean;
  relationshipFound?: boolean;
  summary?: string;
};

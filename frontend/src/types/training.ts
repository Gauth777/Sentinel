/** Canonical training-sample types matching backend camelCase API contract.
 *
 * JSONL export uses snake_case, but application JSON is always camelCase.
 */

export type DatasetStatus = "pending" | "verified" | "rejected";
export type FeedbackStatus = "pending" | "confirmed" | "corrected" | "rejected";
export type FeedbackStatusInput = "confirmed" | "corrected" | "rejected";

export type RoadType =
  | "urban_arterial"
  | "residential"
  | "highway"
  | "junction";

export type TrafficDensity = "low" | "medium" | "high";
export type RoadComplexity = "simple" | "moderate" | "complex";
export type HazardPresence = "yes" | "no";
export type AnticipatedRisk = "low" | "medium" | "high";
export type RecommendedAction =
  | "slow_down"
  | "maintain_speed"
  | "increase_attention"
  | "yield"
  | "prepare_to_stop"
  | "change_lane";

export type TelemetrySource = "demo" | "live" | "imported";
export type MediaType = "image" | "video";
export type StorageMode = "demo_uri" | "local_uri" | "remote_uri" | "managed_upload";
export type InferenceMode = "demo" | "remote" | "local" | "imported";
export type ProvenanceSource = "demo" | "live" | "imported" | "api";
export type PrivacyStatus = "not_reviewed" | "cleared" | "blocked";

export type GeoLocation = {
  latitude: number;
  longitude: number;
};

export type TrainingContext = {
  location: GeoLocation;
  headingDegrees?: number | null;
  speedKmh?: number | null;
  roadName?: string | null;
  routeDirection?: string | null;
  telemetrySource: TelemetrySource;
};

export type TrainingMedia = {
  type: MediaType;
  uri: string;
  mimeType?: string | null;
  width?: number | null;
  height?: number | null;
  durationMs?: number | null;
  sha256?: string | null;
  storageMode: StorageMode;
};

export type TrainingModelInfo = {
  provider: string;
  name: string;
  version: string;
  promptVersion?: string | null;
  inferenceId?: string | null;
  inferenceMode: InferenceMode;
};

export type PredictionLabels = {
  roadType: RoadType;
  trafficDensity: TrafficDensity;
  roadComplexity: RoadComplexity;
  hazardPresence: HazardPresence;
  anticipatedRisk: AnticipatedRisk;
  recommendedAction: RecommendedAction;
  confidence?: number | null;
  perLabelConfidence?: Record<string, number> | null;
  rawResponse?: string | null;
};

export type PartialPredictionLabels = {
  roadType?: RoadType;
  trafficDensity?: TrafficDensity;
  roadComplexity?: RoadComplexity;
  hazardPresence?: HazardPresence;
  anticipatedRisk?: AnticipatedRisk;
  recommendedAction?: RecommendedAction;
};

export type TrainingProvenance = {
  source: ProvenanceSource;
  graphHazardId?: string | null;
  graphObservationId?: string | null;
  sessionId?: string | null;
  deviceId?: string | null;
};

export type TrainingQuality = {
  privacyStatus: PrivacyStatus;
  unusableReason?: string | null;
  notes?: string[] | null;
};

export type FeedbackEvent = {
  status: FeedbackStatus;
  correctedLabels?: Record<string, unknown> | null;
  submittedBy?: string | null;
  submittedAt: string;
  note?: string | null;
};

export type TrainingSample = {
  schemaVersion: string;
  sampleId: string;
  observationId?: string | null;
  hazardId?: string | null;
  sourceVehicleId: string;
  capturedAt: string;
  context: TrainingContext;
  media: TrainingMedia;
  model: TrainingModelInfo;
  prediction: PredictionLabels;
  originalPrediction: PredictionLabels;
  finalVerifiedLabels?: PredictionLabels | null;
  provenance: TrainingProvenance;
  quality?: TrainingQuality | null;
  datasetStatus: DatasetStatus;
  feedbackStatus: FeedbackStatus;
  feedbackHistory: FeedbackEvent[];
  createdAt: string;
  updatedAt: string;
  revision: number;
};

export type TrainingSampleCreate = {
  schemaVersion?: string;
  sampleId: string;
  observationId?: string | null;
  hazardId?: string | null;
  sourceVehicleId: string;
  capturedAt: string;
  context: TrainingContext;
  media: TrainingMedia;
  model: TrainingModelInfo;
  prediction: PredictionLabels;
  provenance?: TrainingProvenance;
  quality?: TrainingQuality | null;
};

export type TrainingFeedbackCreate = {
  status: FeedbackStatusInput;
  correctedLabels?: PartialPredictionLabels;
  submittedBy?: string | null;
  submittedAt?: string | null;
  note?: string | null;
};

export type TrainingSampleListResponse = {
  items: TrainingSample[];
  count: number;
  limit: number;
  skip: number;
  mode: "mongo" | "memory";
};

export type TrainingStats = {
  mode: "mongo" | "memory";
  total: number;
  pending: number;
  verified: number;
  rejected: number;
  confirmed: number;
  corrected: number;
  exportable: number;
  byRoadType: Record<string, number>;
  byHazardPresence: Record<string, number>;
  byRecommendedAction: Record<string, number>;
};

export type TrainingListFilters = {
  status?: DatasetStatus;
  feedbackStatus?: FeedbackStatus;
  hazardId?: string;
  sourceVehicleId?: string;
  modelName?: string;
  limit?: number;
  skip?: number;
};

export type TrainingExportFilters = {
  modelName?: string;
  roadType?: string;
  hazardPresence?: string;
  limit?: number;
};

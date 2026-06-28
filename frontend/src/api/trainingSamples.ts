/** Training sample API client.
 *
 * Reuses the existing backend base URL strategy and ApiError model.
 */
import { ApiError } from "@/src/api/sentinel";
import type {
  TrainingSample,
  TrainingSampleCreate,
  TrainingFeedbackCreate,
  TrainingSampleListResponse,
  TrainingStats,
  TrainingListFilters,
  TrainingExportFilters,
} from "@/src/types/training";

function backendBase() {
  return (process.env.EXPO_PUBLIC_BACKEND_URL || "").replace(/\/$/, "");
}

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const base = backendBase();
  if (!base) {
    throw new ApiError("EXPO_PUBLIC_BACKEND_URL is not configured");
  }
  let res: Response;
  try {
    res = await fetch(`${base}/api${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch (err: any) {
    throw new ApiError(`Network error calling ${path}: ${err?.message ?? err}`);
  }
  if (!res.ok) {
    throw new ApiError(`API ${path} responded ${res.status}`, res.status);
  }
  return (await res.json()) as T;
}

export const trainingApi = {
  hasBackend: () => Boolean(backendBase()),

  createTrainingSample: (payload: TrainingSampleCreate) =>
    j<TrainingSample>("/sentinel/training-samples", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  listTrainingSamples: (filters?: TrainingListFilters) => {
    const query = new URLSearchParams();
    if (filters?.status) query.set("status", filters.status);
    if (filters?.feedbackStatus) query.set("feedback_status", filters.feedbackStatus);
    if (filters?.hazardId) query.set("hazard_id", filters.hazardId);
    if (filters?.sourceVehicleId) query.set("source_vehicle_id", filters.sourceVehicleId);
    if (filters?.modelName) query.set("model_name", filters.modelName);
    if (typeof filters?.limit === "number") query.set("limit", String(filters.limit));
    if (typeof filters?.skip === "number") query.set("skip", String(filters.skip));
    const qs = query.toString();
    return j<TrainingSampleListResponse>(
      `/sentinel/training-samples${qs ? `?${qs}` : ""}`
    );
  },

  getTrainingSample: (sampleId: string) =>
    j<TrainingSample>(`/sentinel/training-samples/${encodeURIComponent(sampleId)}`),

  submitTrainingFeedback: (sampleId: string, payload: TrainingFeedbackCreate) =>
    j<TrainingSample>(
      `/sentinel/training-samples/${encodeURIComponent(sampleId)}/feedback`,
      { method: "POST", body: JSON.stringify(payload) }
    ),

  getTrainingStats: () => j<TrainingStats>("/sentinel/training-samples/stats"),

  getTrainingExportUrl: (filters?: TrainingExportFilters) => {
    const base = backendBase();
    if (!base) return null;
    const query = new URLSearchParams();
    if (filters?.modelName) query.set("model_name", filters.modelName);
    if (filters?.roadType) query.set("road_type", filters.roadType);
    if (filters?.hazardPresence) query.set("hazard_presence", filters.hazardPresence);
    if (typeof filters?.limit === "number") query.set("limit", String(filters.limit));
    const qs = query.toString();
    return `${base}/api/sentinel/training-samples/export${qs ? `?${qs}` : ""}`;
  },
};

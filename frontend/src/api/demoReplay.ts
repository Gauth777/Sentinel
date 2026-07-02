import { ApiError } from "@/src/api/sentinel";
import type {
  DemoReplayStatus,
  DemoReplaySample,
  DemoReplayCurrentResponse,
  DemoReplayAdvanceResponse,
  DemoReplayResetResponse,
  DemoReplayInferenceResponse,
  DemoReplayEvidenceResponse,
  DemoReplayGraphVerifyResponse,
} from "@/src/types/demoReplay";

function backendBase() {
  return (process.env.EXPO_PUBLIC_BACKEND_URL || "").replace(/\/$/, "");
}

function getErrorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const base = backendBase();
  if (!base) throw new ApiError("EXPO_PUBLIC_BACKEND_URL is not configured");

  let res: Response;
  try {
    res = await fetch(`${base}${path}`, init);
  } catch (err: unknown) {
    throw new ApiError(`Network error: ${getErrorMessage(err)}`);
  }

  if (!res.ok) {
    let detail: string | undefined;
    try {
      const body = await res.json();
      detail = body?.detail;
    } catch {
      // ignore
    }
    const msg = detail || `${path} responded ${res.status}`;
    throw new ApiError(msg, res.status);
  }
  return (await res.json()) as T;
}

export const demoReplayApi = {
  hasBackend: () => Boolean(backendBase()),

  getStatus: () => j<DemoReplayStatus>("/api/sentinel/demo-replay"),

  listSamples: () => j<DemoReplaySample[]>("/api/sentinel/demo-replay/samples"),

  getCurrent: () => j<DemoReplayCurrentResponse>("/api/sentinel/demo-replay/current"),

  getSample: (sampleId: string) =>
    j<DemoReplaySample>(`/api/sentinel/demo-replay/samples/${encodeURIComponent(sampleId)}`),

  advance: () =>
    j<DemoReplayAdvanceResponse>("/api/sentinel/demo-replay/advance", { method: "POST" }),

  reset: () =>
    j<DemoReplayResetResponse>("/api/sentinel/demo-replay/reset", { method: "POST" }),

  reload: () =>
    j<DemoReplayStatus>("/api/sentinel/demo-replay/reload", { method: "POST" }),

  selectSample: (sampleId: string) =>
    j<{ sample: DemoReplaySample; currentIndex: number; sampleCount: number }>(
      `/api/sentinel/demo-replay/samples/${encodeURIComponent(sampleId)}/select`,
      { method: "POST" }
    ),

  infer: (sampleId: string, activate: boolean = true) =>
    j<DemoReplayInferenceResponse>(
      `/api/sentinel/demo-replay/samples/${encodeURIComponent(sampleId)}/infer`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ activate }),
      }
    ),

  getEvidence: (sampleId: string) =>
    j<DemoReplayEvidenceResponse>(
      `/api/sentinel/demo-replay/samples/${encodeURIComponent(sampleId)}/evidence`
    ),

  getGraphVerification: (hazardId: string, observationId: string) =>
    j<DemoReplayGraphVerifyResponse>(
      `/api/sentinel/demo-replay/graph-verify?hazardId=${encodeURIComponent(hazardId)}&observationId=${encodeURIComponent(observationId)}`
    ),

  getDashcamUrl: (sampleId: string) => {
    const base = backendBase();
    if (!base) return null;
    return `${base}/api/sentinel/demo-replay/samples/${encodeURIComponent(sampleId)}/dashcam`;
  },

  getTopviewUrl: (sampleId: string) => {
    const base = backendBase();
    if (!base) return null;
    return `${base}/api/sentinel/demo-replay/samples/${encodeURIComponent(sampleId)}/topview`;
  },
};

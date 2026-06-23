// Central API client + safe fetcher.
// - Reads EXPO_PUBLIC_BACKEND_URL once.
// - Surfaces errors (does NOT silently swallow) so the UI can choose to retry or fall back to Demo data.
// - Never throws on missing config in production: returns null-shaped Promise rejections that callers handle.

import type {
  SentinelStatus,
  Hazard,
  NearbyVehicle,
  WorldModel,
} from "@/src/types/sentinel";

export type WorldModelParams = {
  latitude?: number;
  longitude?: number;
  heading?: number;
  radius_m?: number;
};

function backendBase() {
  return (process.env.EXPO_PUBLIC_BACKEND_URL || "").replace(/\/$/, "");
}

export class ApiError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
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

export const api = {
  hasBackend: () => Boolean(backendBase()),
  status: () => j<SentinelStatus>("/sentinel/status"),
  hazards: () => j<Hazard[]>("/sentinel/hazards"),
  nearby: () => j<NearbyVehicle[]>("/sentinel/nearby-vehicles"),
  worldModel: (params?: WorldModelParams) => {
    const query = new URLSearchParams();
    if (typeof params?.latitude === "number") query.set("latitude", String(params.latitude));
    if (typeof params?.longitude === "number") query.set("longitude", String(params.longitude));
    if (typeof params?.heading === "number") query.set("heading", String(params.heading));
    if (typeof params?.radius_m === "number") query.set("radius_m", String(params.radius_m));
    const qs = query.toString();
    return j<WorldModel>(`/sentinel/world-model${qs ? `?${qs}` : ""}`);
  },
  confirm: (id: string) =>
    j<{ id: string; confirmed: number; reportedIncorrect: number }>(
      `/sentinel/hazards/${id}/confirm`,
      { method: "POST" }
    ),
  report: (id: string) =>
    j<{ id: string; confirmed: number; reportedIncorrect: number }>(
      `/sentinel/hazards/${id}/report-incorrect`,
      { method: "POST" }
    ),
  submitObservation: (obs: any) =>
    j<any>(
      "/sentinel/demo/observation",
      { method: "POST", body: JSON.stringify(obs) }
    ),
  resetDemo: () =>
    j<any>(
      "/sentinel/demo/reset",
      { method: "POST" }
    ),
};

// Re-export types for legacy imports
export type { SentinelStatus, Hazard, NearbyVehicle, WorldModel } from "@/src/types/sentinel";

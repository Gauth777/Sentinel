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

const BASE = (process.env.EXPO_PUBLIC_BACKEND_URL || "").replace(/\/$/, "");

export class ApiError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  if (!BASE) {
    throw new ApiError("EXPO_PUBLIC_BACKEND_URL is not configured");
  }
  let res: Response;
  try {
    res = await fetch(`${BASE}/api${path}`, {
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
  hasBackend: () => Boolean(BASE),
  status: () => j<SentinelStatus>("/sentinel/status"),
  hazards: () => j<Hazard[]>("/sentinel/hazards"),
  nearby: () => j<NearbyVehicle[]>("/sentinel/nearby-vehicles"),
  worldModel: () => j<WorldModel>("/sentinel/world-model"),
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
};

// Re-export types for legacy imports
export type { SentinelStatus, Hazard, NearbyVehicle, WorldModel } from "@/src/types/sentinel";

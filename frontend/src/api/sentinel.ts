const BASE = process.env.EXPO_PUBLIC_BACKEND_URL;

export type SentinelStatus = {
  connected: boolean;
  gps_locked: boolean;
  network: string;
  speed_kmh: number;
  road_name: string;
  heading: string;
  sentinel_vehicles_nearby: number;
};

export type Hazard = {
  id: string;
  type: string;
  label: string;
  distance_m: number;
  confidence: number;
  sources: number;
  observed_seconds_ago: number;
  direction: string;
  recommended_action: string;
  risk: "high" | "medium" | "low";
  x: number;
  y: number;
  confirmed: number;
  reported_incorrect: number;
};

export type NearbyVehicle = {
  id: string;
  x: number;
  y: number;
  heading_deg: number;
  label: string;
};

async function j<T>(p: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}/api${p}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) throw new Error(`API ${p} ${r.status}`);
  return (await r.json()) as T;
}

export const api = {
  status: () => j<SentinelStatus>("/sentinel/status"),
  hazards: () => j<Hazard[]>("/sentinel/hazards"),
  nearby: () => j<NearbyVehicle[]>("/sentinel/nearby-vehicles"),
  confirm: (id: string) =>
    j<{ id: string; confirmed: number; reported_incorrect: number }>(
      `/sentinel/hazards/${id}/confirm`,
      { method: "POST" }
    ),
  report: (id: string) =>
    j<{ id: string; confirmed: number; reported_incorrect: number }>(
      `/sentinel/hazards/${id}/report-incorrect`,
      { method: "POST" }
    ),
};

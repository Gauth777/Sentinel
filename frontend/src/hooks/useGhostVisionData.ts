// Orchestrates the world-model: tries backend, falls back to bundled demo scenario.
// Returns { worldModel, status, source, loading, error, refetch, confirm, report }.

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/src/api/sentinel";
import { demoScenario } from "@/src/data/demoScenario";
import type { Hazard, SentinelStatus, WorldModel } from "@/src/types/sentinel";

export type WorldSource = "backend" | "demo";

const DEMO_STATUS: SentinelStatus = {
  connected: false,
  gps_locked: false,
  network: "OFFLINE",
  speed_kmh: 42,
  road_name: "GST Road Northbound",
  heading: "N",
  sentinel_vehicles_nearby: demoScenario.nearbyVehicles.length,
};

export function useGhostVisionData() {
  const [worldModel, setWorldModel] = useState<WorldModel | null>(null);
  const [status, setStatus] = useState<SentinelStatus | null>(null);
  const [source, setSource] = useState<WorldSource>("demo");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (!api.hasBackend()) throw new ApiError("No backend URL");
      const [wm, st] = await Promise.all([api.worldModel(), api.status()]);
      setWorldModel(wm);
      setStatus(st);
      setSource("backend");
    } catch (err: any) {
      // Fallback to bundled demo so Ghost Vision still works offline.
      // Surface the error message in dev so we can see *why* fallback was used.
      console.warn("[Sentinel] world-model fetch failed, using demo scenario:", err?.message ?? err);
      setWorldModel(demoScenario);
      setStatus(DEMO_STATUS);
      setSource("demo");
      setError(err?.message ?? String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetch();
  }, [fetch]);

  const confirm = useCallback(async (id: string) => {
    try {
      const r = await api.confirm(id);
      setWorldModel((wm) =>
        wm
          ? { ...wm, hazards: wm.hazards.map((h) => (h.id === r.id ? { ...h, confirmed: r.confirmed } : h)) }
          : wm
      );
      return r;
    } catch {
      // Demo mode: increment locally so the user still sees feedback.
      setWorldModel((wm) =>
        wm
          ? { ...wm, hazards: wm.hazards.map((h) => (h.id === id ? { ...h, confirmed: h.confirmed + 1 } : h)) }
          : wm
      );
      return null;
    }
  }, []);

  const report = useCallback(async (id: string) => {
    try {
      const r = await api.report(id);
      setWorldModel((wm) =>
        wm
          ? { ...wm, hazards: wm.hazards.map((h) => (h.id === r.id ? { ...h, reportedIncorrect: r.reportedIncorrect } : h)) }
          : wm
      );
      return r;
    } catch {
      setWorldModel((wm) =>
        wm
          ? { ...wm, hazards: wm.hazards.map((h) => (h.id === id ? { ...h, reportedIncorrect: h.reportedIncorrect + 1 } : h)) }
          : wm
      );
      return null;
    }
  }, []);

  const setHazards = useCallback((updater: (prev: Hazard[]) => Hazard[]) => {
    setWorldModel((wm) => (wm ? { ...wm, hazards: updater(wm.hazards) } : wm));
  }, []);

  return { worldModel, status, source, loading, error, refetch: fetch, confirm, report, setHazards };
}

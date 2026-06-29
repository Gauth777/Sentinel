// Orchestrates the world-model: tries backend, falls back to bundled demo scenario.
// Returns { worldModel, status, source, loading, error, refetch, confirm, report }.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "@/src/api/sentinel";
import { demoScenario } from "@/src/data/demoScenario";
import type { GeoPoint, Hazard, SentinelStatus, WorldModel } from "@/src/types/sentinel";
import {
  LIVE_WORLD_RADIUS_M,
  type LiveTelemetryInput,
  type RefreshSnapshot,
  shouldRefreshWorldModel,
  withLiveTelemetry,
} from "@/src/utils/ghostVisionLive";

export type WorldSource = "backend" | "demo";

export type GhostVisionLiveInput = {
  enabled: boolean;
  location: GeoPoint | null;
  headingDegrees: number | null;
};

const DEMO_STATUS: SentinelStatus = {
  connected: false,
  gps_locked: false,
  network: "OFFLINE",
  speed_kmh: 42,
  road_name: "GST Road Northbound",
  heading: "N",
  sentinel_vehicles_nearby: demoScenario.nearbyVehicles.length,
};

export function useGhostVisionData(liveInput?: GhostVisionLiveInput) {
  const [worldModel, setWorldModel] = useState<WorldModel | null>(null);
  const [status, setStatus] = useState<SentinelStatus | null>(null);
  const [source, setSource] = useState<WorldSource>("demo");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestSeq = useRef(0);
  const inFlight = useRef(false);
  const queuedRefresh = useRef(false);
  const lastBackendRefresh = useRef<RefreshSnapshot | null>(null);
  const worldModelRef = useRef<WorldModel | null>(null);
  const liveTelemetryRef = useRef<LiveTelemetryInput | null>(null);
  const mountedRef = useRef(true);
  const queuedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    worldModelRef.current = worldModel;
  }, [worldModel]);

  const inputLatitude = liveInput?.location?.latitude;
const inputLongitude = liveInput?.location?.longitude;
const inputHeading = liveInput?.headingDegrees;
const liveEnabled = liveInput?.enabled;

const liveTelemetry = useMemo<LiveTelemetryInput | null>(() => {
  if (
    !liveEnabled ||
    typeof inputLatitude !== "number" ||
    typeof inputLongitude !== "number"
  ) {
    return null;
  }

  return {
    location: {
      latitude: inputLatitude,
      longitude: inputLongitude,
    },
    headingDegrees: inputHeading,
  };
}, [liveEnabled, inputLatitude, inputLongitude, inputHeading]);

  useEffect(() => {
    liveTelemetryRef.current = liveTelemetry;
  }, [liveTelemetry]);

  const fetch = useCallback(async (force = false) => {
    if (inFlight.current) {
      queuedRefresh.current = queuedRefresh.current || force || Boolean(liveTelemetryRef.current);
      return;
    }
    inFlight.current = true;
    const seq = requestSeq.current + 1;
    requestSeq.current = seq;
    const initialLoad = !worldModelRef.current;
    if (initialLoad) setLoading(true);
    setError(null);
    try {
      if (!api.hasBackend()) throw new ApiError("No backend URL");
      const requestTelemetry = liveTelemetryRef.current;
      const params = requestTelemetry
        ? {
            latitude: requestTelemetry.location.latitude,
            longitude: requestTelemetry.location.longitude,
            heading:
              typeof requestTelemetry.headingDegrees === "number"
                ? requestTelemetry.headingDegrees
                : undefined,
            radius_m: LIVE_WORLD_RADIUS_M,
          }
        : undefined;
      const [wm, st] = await Promise.all([api.worldModel(params), api.status()]);
      if (seq !== requestSeq.current) return;
      if (!mountedRef.current) return;
      const latestTelemetry = liveTelemetryRef.current;
      if (requestTelemetry && !latestTelemetry) {
        queuedRefresh.current = true;
        return;
      }
      setWorldModel(latestTelemetry ? withLiveTelemetry(wm, latestTelemetry) : wm);
      setStatus(st);
      setSource("backend");
      if (requestTelemetry) {
        lastBackendRefresh.current = { location: requestTelemetry.location, timestampMs: Date.now() };
      }
    } catch (err: any) {
      if (seq !== requestSeq.current) return;
      if (!mountedRef.current) return;
      // Fallback to bundled demo so Ghost Vision still works offline.
      // Surface the error message in dev so we can see *why* fallback was used.
      console.warn("[Sentinel] world-model fetch failed, using demo scenario:", err?.message ?? err);
      if (!worldModelRef.current) {
        const latestTelemetry = liveTelemetryRef.current;
        setWorldModel(latestTelemetry ? withLiveTelemetry(demoScenario, latestTelemetry) : demoScenario);
        setStatus(DEMO_STATUS);
        setSource("demo");
      }
      setError(err?.message ?? String(err));
    } finally {
      if (seq === requestSeq.current && mountedRef.current) {
        setLoading(false);
        inFlight.current = false;
        if (queuedRefresh.current) {
          queuedRefresh.current = false;
          queuedTimerRef.current = setTimeout(() => fetch(true), 0);
        }
      }
    }
  }, []);

  useEffect(() => {
    fetch(true);
  }, [fetch]);

  useEffect(() => {
    return () => {
      mountedRef.current = false;
      if (queuedTimerRef.current) {
        clearTimeout(queuedTimerRef.current);
      }
    };
  }, []);

  const liveLatitude = liveTelemetry?.location.latitude;
  const liveLongitude = liveTelemetry?.location.longitude;

  useEffect(() => {
    const telemetry = liveTelemetryRef.current;
    if (!telemetry) return;

    // Locally update ego position and hazard distances only when position changes.
    // Heading is rendered directly by GhostVisionScreen and should not rewrite
    // the complete world model on every compass event.
    setWorldModel((wm) =>
      wm
        ? withLiveTelemetry(wm, {
            location: telemetry.location,
            headingDegrees: wm.ego.headingDegrees,
          })
        : wm
    );
  }, [liveLatitude, liveLongitude]);

  useEffect(() => {
  const telemetry = liveTelemetryRef.current;
  if (!telemetry) return;

  const next = {
    location: telemetry.location,
    timestampMs: Date.now(),
  };

  if (!shouldRefreshWorldModel(lastBackendRefresh.current, next)) return;
  fetch();
  }, [fetch, liveLatitude, liveLongitude]);

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

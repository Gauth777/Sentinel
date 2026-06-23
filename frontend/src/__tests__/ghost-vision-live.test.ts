import { test } from "node:test";
import assert from "node:assert/strict";
import { demoScenario } from "../data/demoScenario";
import { api } from "../api/sentinel";
import {
  LIVE_REFRESH_DISTANCE_M,
  LIVE_REFRESH_INTERVAL_MS,
  buildLiveObservation,
  destinationPoint,
  shouldRefreshWorldModel,
  normalizeHeadingDegrees,
  withLiveTelemetry,
} from "../utils/ghostVisionLive";
import { haversineMeters } from "../components/ghost-vision/projection";

test("API query includes coordinates and heading", async () => {
  const originalFetch = globalThis.fetch;
  const originalBackendUrl = process.env.EXPO_PUBLIC_BACKEND_URL;
  process.env.EXPO_PUBLIC_BACKEND_URL = "http://sentinel.test";
  const calls: string[] = [];
  globalThis.fetch = (async (url: RequestInfo | URL) => {
    calls.push(String(url));
    return {
      ok: true,
      json: async () => demoScenario,
    } as Response;
  }) as typeof fetch;

  try {
    await api.worldModel({
      latitude: 12.34,
      longitude: 56.78,
      heading: 91,
      radius_m: 750,
    });
  } finally {
    globalThis.fetch = originalFetch;
    if (originalBackendUrl === undefined) {
      delete process.env.EXPO_PUBLIC_BACKEND_URL;
    } else {
      process.env.EXPO_PUBLIC_BACKEND_URL = originalBackendUrl;
    }
  }

  assert.equal(calls.length, 1);
  const url = new URL(calls[0]);
  assert.equal(url.pathname, "/api/sentinel/world-model");
  assert.equal(url.searchParams.get("latitude"), "12.34");
  assert.equal(url.searchParams.get("longitude"), "56.78");
  assert.equal(url.searchParams.get("heading"), "91");
  assert.equal(url.searchParams.get("radius_m"), "750");
});

test("invalid live headings fall back instead of propagating negative values", () => {
  assert.equal(normalizeHeadingDegrees(-1, 42), 42);
  assert.equal(normalizeHeadingDegrees(null, 42), 42);
  assert.equal(normalizeHeadingDegrees(725, 0), 5);

  const updated = withLiveTelemetry(demoScenario, {
    location: demoScenario.ego.location,
    headingDegrees: -1,
  });
  assert.equal(updated.ego.headingDegrees, demoScenario.ego.headingDegrees);

  const obs = buildLiveObservation({
    location: demoScenario.ego.location,
    headingDegrees: -1,
  });
  assert.equal(Math.round(haversineMeters(demoScenario.ego.location, obs.location)), 120);
  assert.equal(obs.location.latitude > demoScenario.ego.location.latitude, true);
});

test("local GPS updates change ego position and hazard distance", () => {
  const hazard = demoScenario.hazards[0];
  const start = demoScenario.ego.location;
  const closer = destinationPoint(hazard.location, 180, 60);

  const initial = withLiveTelemetry(demoScenario, { location: start, headingDegrees: 10 });
  const updated = withLiveTelemetry(initial, { location: closer, headingDegrees: 25 });

  assert.deepEqual(updated.ego.location, closer);
  assert.equal(updated.ego.headingDegrees, 25);
  assert.notEqual(updated.hazards[0].distanceMeters, initial.hazards[0].distanceMeters);
  assert.equal(updated.hazards[0].distanceMeters, Math.round(haversineMeters(closer, hazard.location) * 10) / 10);
});

test("backend refresh is throttled by movement and time", () => {
  const start = { latitude: 12.9436, longitude: 80.1502 };
  const movedLittle = destinationPoint(start, 0, LIVE_REFRESH_DISTANCE_M - 2);
  const movedEnough = destinationPoint(start, 0, LIVE_REFRESH_DISTANCE_M + 1);

  assert.equal(
    shouldRefreshWorldModel(
      { location: start, timestampMs: 1000 },
      { location: movedLittle, timestampMs: 1000 + LIVE_REFRESH_INTERVAL_MS - 200 }
    ),
    false
  );
  assert.equal(
    shouldRefreshWorldModel(
      { location: start, timestampMs: 1000 },
      { location: movedEnough, timestampMs: 1200 }
    ),
    true
  );
  assert.equal(
    shouldRefreshWorldModel(
      { location: start, timestampMs: 1000 },
      { location: movedLittle, timestampMs: 1000 + LIVE_REFRESH_INTERVAL_MS }
    ),
    true
  );
});

test("hazard coordinates remain unchanged while distances update", () => {
  const hazardBefore = demoScenario.hazards[0];
  const nextLocation = destinationPoint(demoScenario.ego.location, 0, 20);
  const updated = withLiveTelemetry(demoScenario, {
    location: nextLocation,
    headingDegrees: demoScenario.ego.headingDegrees,
  });

  assert.deepEqual(updated.hazards[0].location, hazardBefore.location);
  assert.notEqual(updated.hazards[0].distanceMeters, hazardBefore.distanceMeters);
});

test("live observation is placed ahead of the observer", () => {
  const observer = { latitude: 12.9436, longitude: 80.1502 };
  const obs = buildLiveObservation({ location: observer, headingDegrees: 0 });

  assert.equal(obs.id, "obs-live-demo-stationary-ahead");
  assert.equal(obs.location.latitude > observer.latitude, true);
  assert.equal(Math.round(haversineMeters(observer, obs.location)), 120);
  assert.equal(obs.polygon.length, 4);
});

test("demo fallback remains unchanged without live telemetry", () => {
  assert.equal(demoScenario.telemetrySource, "demo");
  assert.equal(demoScenario.ego.location.latitude, 12.9436);
  assert.equal(demoScenario.hazards[0].distanceMeters, 340);
});

import { test } from "node:test";
import assert from "node:assert/strict";

// Replicates the warning resolution logic in app/ghost-vision.tsx
function getWarningText(hazard: any, language: "en" | "hi" | "hinglish"): string {
  return (
    hazard.warnings?.[language] ||
    `${hazard.label}. Approximately ${hazard.distanceMeters} metres ahead. ${hazard.recommendedAction}.`
  );
}

// Replicates the hint text selector logic for vehicle role in app/ghost-vision.tsx
function getRoleHint(role: "approaching" | "observer"): string {
  if (role === "observer") {
    return "Observer vehicle Sentinel-A8 observes a hazard and submits it to the graph model.";
  }
  return "Approaching vehicle queries relevant hazards and shows the observer's hazard as a hidden Ghost object.";
}

// Replicates the demo observation generator logic in app/ghost-vision.tsx
function buildObservationPayload(timestamp: number) {
  return {
    id: `obs-demo-${timestamp}`,
    type: "stationary_vehicle",
    label: "Stationary Vehicle Ahead",
    location: {
      latitude: 12.9452,
      longitude: 80.1506,
    },
    polygon: [
      { latitude: 12.9451, longitude: 80.1505 },
      { latitude: 12.9451, longitude: 80.1507 },
      { latitude: 12.9453, longitude: 80.1507 },
      { latitude: 12.9453, longitude: 80.1505 },
    ],
    sourceVehicleId: "v-1",
    vehicleLabel: "Sentinel-A8",
  };
}

const mockHazard = {
  id: "hz-001",
  label: "Stationary Vehicle Ahead",
  distanceMeters: 180,
  recommendedAction: "Reduce speed",
  warnings: {
    en: "Stationary vehicle approximately 180 metres ahead. Reduce speed.",
    hi: "लगभग 180 मीटर आगे एक रुका हुआ वाहन है। गति कम करें।",
    hinglish: "180 metre aage stationary vehicle hai. Speed kam karein.",
  },
};

test("retrieves the warning text in English correctly", () => {
  const txt = getWarningText(mockHazard, "en");
  assert.equal(txt, "Stationary vehicle approximately 180 metres ahead. Reduce speed.");
});

test("retrieves the warning text in Hindi correctly", () => {
  const txt = getWarningText(mockHazard, "hi");
  assert.equal(txt, "लगभग 180 मीटर आगे एक रुका हुआ वाहन है। गति कम करें।");
});

test("retrieves the warning text in Hinglish correctly", () => {
  const txt = getWarningText(mockHazard, "hinglish");
  assert.equal(txt, "180 metre aage stationary vehicle hai. Speed kam karein.");
});

test("falls back to standard warning if warnings object is missing", () => {
  const legacyHazard = {
    id: "hz-legacy",
    label: "Pothole",
    distanceMeters: 50,
    recommendedAction: "Move left",
  };
  const txt = getWarningText(legacyHazard, "en");
  assert.equal(txt, "Pothole. Approximately 50 metres ahead. Move left.");
});

test("returns correct hint for approaching role", () => {
  assert.equal(
    getRoleHint("approaching"),
    "Approaching vehicle queries relevant hazards and shows the observer's hazard as a hidden Ghost object."
  );
});

test("returns correct hint for observer role", () => {
  assert.equal(
    getRoleHint("observer"),
    "Observer vehicle Sentinel-A8 observes a hazard and submits it to the graph model."
  );
});

test("builds correct observation payload", () => {
  const ts = 1718980000000;
  const payload = buildObservationPayload(ts);
  assert.deepEqual(payload, {
    id: "obs-demo-1718980000000",
    type: "stationary_vehicle",
    label: "Stationary Vehicle Ahead",
    location: {
      latitude: 12.9452,
      longitude: 80.1506,
    },
    polygon: [
      { latitude: 12.9451, longitude: 80.1505 },
      { latitude: 12.9451, longitude: 80.1507 },
      { latitude: 12.9453, longitude: 80.1507 },
      { latitude: 12.9453, longitude: 80.1505 },
    ],
    sourceVehicleId: "v-1",
    vehicleLabel: "Sentinel-A8",
  });
});

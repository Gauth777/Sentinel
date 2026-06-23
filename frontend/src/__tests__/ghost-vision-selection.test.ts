/**
 * Frontend selection-preservation test for the Ghost Vision screen.
 *
 * Run with:
 *   cd frontend && node --import tsx src/__tests__/ghost-vision-selection.test.ts
 *   (or just `node` if compiled to JS — the file uses only built-in test runner APIs)
 */
import { test } from "node:test";
import assert from "node:assert/strict";

type Hazard = {
  id: string;
  risk: "high" | "medium" | "low";
  routeRelevance: "none" | "low" | "medium" | "high";
  confirmed: number;
  reportedIncorrect: number;
};

/**
 * Replicates the selection logic in app/ghost-vision.tsx:
 *   - keep the previously-selected id if it still exists in the new hazards list
 *   - otherwise fall back to the primary (high route-relevance > high risk > first)
 */
function pickActiveId(prevId: string | null, hazards: Hazard[]): string | null {
  if (prevId && hazards.some((h) => h.id === prevId)) return prevId;
  const primary =
    hazards.find((h) => h.routeRelevance === "high") ??
    hazards.find((h) => h.risk === "high") ??
    hazards[0] ??
    null;
  return primary ? primary.id : null;
}

const baseHazards: Hazard[] = [
  { id: "hz-001", risk: "high", routeRelevance: "high", confirmed: 0, reportedIncorrect: 0 },
  { id: "hz-002", risk: "medium", routeRelevance: "medium", confirmed: 0, reportedIncorrect: 0 },
];

test("initial selection picks the high route-relevance hazard", () => {
  assert.equal(pickActiveId(null, baseHazards), "hz-001");
});

test("keeps the user's secondary selection when worldModel updates (e.g. confirm)", () => {
  const userSelectedId = "hz-002";
  const afterConfirm: Hazard[] = [
    baseHazards[0],
    { ...baseHazards[1], confirmed: 1 },
  ];
  assert.equal(pickActiveId(userSelectedId, afterConfirm), "hz-002");
});

test("keeps the secondary selection through repeated report-incorrect updates", () => {
  let hazards: Hazard[] = baseHazards;
  let active: string | null = "hz-002";
  for (let i = 0; i < 3; i++) {
    hazards = hazards.map((h) =>
      h.id === "hz-002" ? { ...h, reportedIncorrect: h.reportedIncorrect + 1 } : h
    );
    active = pickActiveId(active, hazards);
  }
  assert.equal(active, "hz-002");
  assert.equal(hazards.find((h) => h.id === "hz-002")?.reportedIncorrect, 3);
});

test("falls back to the primary if the selected hazard is removed", () => {
  const withoutSecondary: Hazard[] = [baseHazards[0]];
  assert.equal(pickActiveId("hz-002", withoutSecondary), "hz-001");
});

test("returns null when no hazards exist", () => {
  assert.equal(pickActiveId("hz-002", []), null);
});

// Export so tsc treats this as a module and not pollute globals.
export { pickActiveId };

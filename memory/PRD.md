# Sentinel — Ghost Vision (Phase 3: Pre-merge corrections)

## What this phase fixes

This is a focused correction pass on top of Phase 2 (structured world model). No new features were added; nothing was rebuilt from scratch.

### 1. Honest "GPS Position Preview" wording
- `Use Live Geo (GPS)` → `GPS POSITION PREVIEW · EXPERIMENTAL`.
- Helper copy: _"Uses live device position with simulated Sentinel world-model overlays. Roads and buildings around your real location are not loaded."_
- Telemetry source badge in Live mode now reads `GPS POSITION PREVIEW · EXPERIMENTAL` instead of `LIVE GEO · SIMULATED OVERLAYS`.
- The experimental affordance is rendered as a subordinate row beneath the map (not in the primary status strip) so the judged Demo Scenario path remains the default.

### 2. Selected-hazard preservation
- Component state changed from `active: Hazard | null` to `activeHazardId: string | null`. The active hazard is **derived** from the latest `worldModel.hazards`.
- New selection rule: keep the previously-selected id if it still exists; otherwise fall back to high-route-relevance → high-risk → first hazard.
- Confirm / Report on a non-primary hazard no longer flips the selection back to the primary.
- Counter updates appear immediately in the card.
- Selection survives world-model refetches.
- Backed by 5 frontend unit tests (`frontend/src/__tests__/ghost-vision-selection.test.ts`, runs under Node 20's built-in test runner via `tsx`).

### 3. Idempotent demo-data migration
- New `SEED_VERSION = 2` constant in `backend/server.py`.
- `ensure_seed()` is now a true migration:
  - Reads the applied version from `db.sentinel_meta` (single doc `{id:"seed", version}`).
  - On mismatch, `replace_one({"id": ...}, doc, upsert=True)` for every known demo hazard (`hz-*`) and vehicle (`v-*`).
  - **Counters are preserved**: existing `confirmed` and `reportedIncorrect` carry across the migration.
  - Records the applied version. Subsequent startups are a no-op fast path.
- Touches **only** `db.hazards`, `db.nearby_vehicles`, and `db.sentinel_meta`. No unrelated collections are modified.
- Verified by three tests: `test_old_schema_migration`, `test_repeated_seeding_is_idempotent`, `test_seed_meta_records_version`.
- `GET /api/sentinel/world-model` now has `response_model=WorldModel` so FastAPI validates the response contract.

### 4. Removed false OSM-data claim
- Map attribution changed from `© OpenStreetMap contributors · Demo derived layout` to `Synthetic GST Road demo scenario`.
- README/PRD wording clarified: roads, buildings, hazards, and occupied regions are **synthetic geometry placed on real geographic coordinates** — no OSM tiles, vector data, or extracts are loaded at runtime. (The lat/lon centre point lives near GST Road, Tambaram, Chennai for plausibility, nothing more.)
- Real phone GPS is still used in GPS Position Preview mode — that is explicitly labelled experimental.

### 5. Animation pausing
- Ghost Vision now reads `useIsFocused()` from `@react-navigation/native`.
- `WorldMap` accepts a `paused` prop, which threads down to `GhostObjectLayer`. While the screen is unfocused the active-hazard pulse animation is cancelled (`pulse.value = 0`), and `Speech.stop()` is called.
- Reduced-motion users see no pulse to begin with (animation runs only on the active hazard, ≤ 1 simultaneous transform).

### 6. Local backend validation
- Tests now default to `http://127.0.0.1:8001`. Override with `SENTINEL_TEST_URL`.
- Includes a 10-second readiness wait so they cope with backend reload.
- Run from the repo:
  ```bash
  cd backend && python -m pytest tests/test_sentinel.py -v
  ```
  **Result this pass:** `10 passed in 0.93s`.

### 7. Frontend validation
- `npx tsc --noEmit` → ✅ zero errors.
- `npx expo lint` → ✅ no issues.
- Frontend selection tests → ✅ 5 / 5 pass under Node 20 test runner.
- `npx expo-doctor@latest` → 15 / 18 checks pass. Remaining 3 failures are pre-existing in the template (non-square stock `icon.png` / `adaptive-icon.png` shipped at 512×513, duplicate sub-dependency versions of `@expo/vector-icons` and `@react-navigation/native` pulled in by `expo` and `expo-router`, and three patch-version mismatches on `expo`, `expo-font`, `expo-router`). None were introduced by this phase.

## API surface (unchanged shape, response_model added)
- `GET /api/sentinel/status`
- `GET /api/sentinel/world-model` — now validated against `WorldModel`
- `GET /api/sentinel/hazards`
- `GET /api/sentinel/nearby-vehicles`
- `POST /api/sentinel/hazards/{id}/confirm`
- `POST /api/sentinel/hazards/{id}/report-incorrect`

## Honest "Real vs Synthetic" matrix
| Element | Source |
| --- | --- |
| Geographic coordinates | Real lat/lon near GST Road, Tambaram (Chennai). Plausibility only. |
| Road geometry, building footprints, road corridor | **Synthetic, hand-authored.** Not OSM. |
| Hazards, occupied regions, nearby vehicles | **Simulated.** Deterministic demo data. |
| Phone GPS | Real (foreground permission via `expo-location`), available only in Android dev build. |
| OSM tiles / vector data | **Not used.** Reserved for a future native MapLibre adapter (`EXPO_PUBLIC_MAP_STYLE_URL`). |

## Files changed
- `backend/server.py` — `SEED_VERSION`, migration in `ensure_seed`, `response_model=WorldModel` on world-model.
- `backend/tests/test_sentinel.py` — default `http://127.0.0.1:8001`, new migration / idempotency / meta-version tests, readiness wait.
- `frontend/app/ghost-vision.tsx` — `activeHazardId` refactor + derive, GPS Position Preview wording, attribution change, `useIsFocused` → `paused` flow, Speech.stop on blur.
- `frontend/app.json` — fixed 6-char hex colours.
- `frontend/src/components/ghost-vision/WorldMap.tsx` — `paused` prop, threaded to ghost layer.
- `frontend/src/components/ghost-vision/layers.tsx` — `GhostObjectLayer` accepts `paused`, cancels the reanimated repeat when unfocused.
- `frontend/src/__tests__/ghost-vision-selection.test.ts` — new, 5 tests.
- `memory/PRD.md` — this document.

## Running

### Backend (local)
```bash
cd backend
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8001
# in another shell:
python -m pytest tests/test_sentinel.py -v
```

### Frontend (Expo dev server)
```bash
cd frontend && yarn install && yarn start
# Selection unit tests
npx tsx --test src/__tests__/ghost-vision-selection.test.ts
# Type check + lint + doctor
npx tsc --noEmit
npx expo lint
npx expo-doctor@latest
```

### Android development build (for GPS Position Preview)
```bash
cd frontend && npx eas build --profile development --platform android
```

## Confirmation
- ✅ **Selected-hazard reset bug is fixed.** Confirming or reporting `hz-002` keeps `hz-002` selected and shows its updated counter. Verified by 5 frontend unit tests (`ghost-vision-selection.test.ts`) and by the existing UI flow regression in the test report.
- ✅ **Old MongoDB seed data is migrated safely.** `SEED_VERSION = 2` plus per-id `replace_one(... upsert=True)` upserts only the known demo documents (`hz-*`, `v-*`); counters preserved; repeated startup is idempotent; unrelated collections are untouched. Verified by `test_old_schema_migration` and `test_repeated_seeding_is_idempotent`.

## Remaining limitations
- Web preview cannot exercise the experimental GPS Position Preview (sandboxed iframe geolocation is unreliable); the screen falls through to `MapErrorState` with Retry / Use Demo. Works on an Android dev build.
- Roads/buildings around the user's real GPS coordinates are **not** loaded — the overlays remain synthetic. This is now clearly labelled.
- Pre-existing template asset issues (non-square `icon.png`/`adaptive-icon.png`) and minor `expo`/`expo-font`/`expo-router` patch-version drift surface in `expo-doctor`; out of scope for this correction pass.

## Out of scope (explicitly not implemented)
Neo4j AuraDB · Sarvam AI · Render Workflows · Bluetooth dashcam · live object detection / camera depth / lane detection · real V2V networking · native MapLibre dependency · authentication · unrelated screens.

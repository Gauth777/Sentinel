# Sentinel — Ghost Vision (Phase 2: Structured World Model)

## Vision
Sentinel is a shared road-perception platform for Indian roads. One Sentinel-connected vehicle detects a road hazard and warns another vehicle approaching the same road segment before that hazard becomes visible. Ghost Vision is the structured, tactical local digital twin of the vehicle's surroundings — a fusion of three layers: a static OSM-derived world, the local perception view, and the shared Ghost view from other Sentinel vehicles.

## Phase 2 — what changed
- **Synthetic S-shaped road removed.** `TacticalMap.tsx` is gone. The Ghost Vision screen now renders from a structured `WorldModel` (`scenarioId`, `mapBounds`, `roads`, `buildings`, `occupiedRegions`, `nearbyVehicles`, `hazards`, `roadCorridor`, `ego`, `telemetrySource`) using a real lat/lon → pixel projection.
- **Three visual layers**:
  1. **Static world layer** — road geometry from the world model (GST Road + side roads), building footprints, safe driving corridor, geospatial grid.
  2. **Local perception layer** — solid blue polygons for visible vehicles, amber polygons for road obstructions, dashed grey polygons for unknown occupied regions (semantic-free).
  3. **Shared Ghost layer** — translucent + dashed footprints for hazards observed by other Sentinel vehicles (hz-001 stationary vehicle, hz-002 pothole). Visibility state is shown in the card (`HIDDEN · Beyond line of sight`).
- **Route relevance** drives prominence: only `medium`/`high` hazards trigger TTS voice alerts. `low`/`none` items render quietly.
- **Field-of-view cone** is aligned with the ego heading (not a fixed up-cone).
- **Two operating modes**:
  - **Demo Scenario Mode** — bundled deterministic scenario (`/app/frontend/src/data/demoScenario.ts`), works without GPS, network, or backend. Visible badge: `DEMO SCENARIO · SIMULATED TELEMETRY`.
  - **Live Geo Mode** — `expo-location` foreground permission, requested contextually via "Use Live Geo (GPS)" button. Centres the WorldMap on the user with a 280 m bounding box, follows real heading. On web preview (or if permission denied / unavailable / services off) we surface a `MapErrorState` with `Retry` and `Open Demo Scenario` — the app never blocks.
- **Modular file layout**:
  ```
  src/
    api/sentinel.ts                — typed fetch wrapper with ApiError surfacing
    types/sentinel.ts              — WorldModel / Hazard / OccupiedRegion (graph-friendly)
    data/demoScenario.ts           — bundled deterministic scenario
    hooks/useGhostVisionData.ts    — backend or demo fallback, no silent swallow
    hooks/useSentinelLocation.ts   — contextual permission, watch, heading
    components/ghost-vision/
      WorldMap.tsx                 — projection-driven orchestrator
      layers.tsx                   — StaticWorld/FieldOfView/OccupiedRegion/Vehicle/Ghost/Ego/Grid
      projection.ts                — lat/lon → pixel, boundsAround, haversine
      HazardBottomSheet.tsx        — collapsible detail surface
      MapLegend.tsx                — symbol legend
      MapErrorState.tsx            — friendly GPS/backend fallback
  ```
- **Backend** updated to camelCase geo schema. New endpoint `GET /api/sentinel/world-model` returns the full scenario. Mongo collections were re-seeded with the new schema.
- **Honest distance language**: `≈180 m` / "Approximately X metres". No centimetre-level claims.
- **Animations restrained**: radar sweep removed in favour of subtle hazard pulse on the active hazard only. TTS / animations pause on route blur.
- **No more `LogBox.ignoreAllLogs`.** SafeAreaProvider added at the root.
- **Android dev-build ready**: `app.json` configured with package `com.gauth777.sentinel`, scheme `sentinel`, `ACCESS_FINE_LOCATION` / `ACCESS_COARSE_LOCATION`, `expo-location` plugin with usage description. `eas.json` ships `development` / `preview` / `production` profiles.

## API Surface (FastAPI + MongoDB)
- `GET /api/sentinel/status`
- `GET /api/sentinel/world-model` — full structured world
- `GET /api/sentinel/hazards` — hazards w/ `location {lat,lon}`, polygon, visibilityState, sourceType, routeRelevance
- `GET /api/sentinel/nearby-vehicles`
- `POST /api/sentinel/hazards/{id}/confirm` — community validate
- `POST /api/sentinel/hazards/{id}/report-incorrect`

No `_id` leakage; idempotent demo seed; deterministic content matches the bundled frontend scenario so they line up visually.

## Environment Variables
| Key | Required? | Behaviour when missing |
| --- | --- | --- |
| `EXPO_PUBLIC_BACKEND_URL` | Required for live API | UI shows offline banner and falls back to bundled Demo Scenario. App still works. |
| `EXPO_PUBLIC_MAP_STYLE_URL` | Optional | Reserved for the native MapLibre adapter shipped in the Android dev build. Web preview always renders the SVG WorldMap. |
| `MONGO_URL`, `DB_NAME` (backend) | Required | Backend will fail to start without them. |

## Running
```bash
# Frontend (Expo dev server — preview)
cd frontend && yarn install && yarn start

# Backend
cd backend && pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8001

# Android EAS development build
cd frontend && npx eas build --profile development --platform android
# Then install the APK and run: yarn start --dev-client
```

## Real vs Simulated
- **Real**: device GPS (expo-location, foreground permission, watch + heading), expo-haptics, expo-speech TTS, expo-router navigation, MongoDB persistence for hazard confirm/report counters.
- **OSM-derived**: road geometry is taken from real GST Road / Tambaram lat/lon coordinates; OSM attribution displayed.
- **Mocked / Bundled**: hazards, nearby Sentinel vehicles, occupied regions, building footprints — all deterministic demo data. No real perception pipeline, no real V2V networking.
- **Not implemented (deferred to next sponsor phase)**: Neo4j AuraDB, Sarvam AI, Render Workflows, Bluetooth dashcam, live object detection, lane detection, real cross-vehicle networking, native MapLibre live tiles (interface point reserved via `EXPO_PUBLIC_MAP_STYLE_URL`).

## Known Limitations
- The web preview cannot exercise Live Geo Mode — geolocation in sandboxed iframes is unreliable, so `useSentinelLocation` reports `unavailable` and the UI directs the user to Demo Scenario. On an Android dev build, Live Geo works end-to-end.
- The map background is currently a stylised SVG world (roads + buildings + corridor) — a true tile-server OSM raster will be wired in by the native MapLibre adapter in the dev build.
- The polygon footprint of a Ghost hazard is approximate (~5×9 m) and not safety-certified.

## Validation Results
- `npx tsc --noEmit` — clean, zero errors.
- ESLint — zero errors after cleanup.
- `pytest backend/tests/test_sentinel.py` — 7/7 pass (status, hazards geo schema, nearby vehicles, world-model shape, confirm/report increment, 404).
- Frontend testing subagent — full UI flow verified on 375×667 viewport; engage-live-geo on web preview correctly surfaces `MapErrorState` with Retry/Use Demo (no crash).
- Real screenshots captured of Drive HUD, Ghost Vision world map (with FOV cone, building footprints, occupied regions, ghost hazard polygon, distance pill, legend, attribution), and expanded hazard sheet.

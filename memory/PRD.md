# Sentinel — Ghost Vision MVP

## Vision
A shared road-perception platform for Indian roads. One Sentinel-connected vehicle detects a hazard; another vehicle sees it on a tactical top-down map *before* it becomes visible through the windshield. Built Android-first, for delivery drivers, fleet operators, taxi & bus drivers, logistics and emergency operators.

## Implemented Scope (v1)
1. **Drive HUD stub** (`/`) — cinematic dark surface, brand strip, large 120pt speed, road name, 2x2 telemetry grid (GPS / Network / Nearby / Heading), warning hint banner, primary cyan "Engage Ghost Vision" CTA. Heavy haptic on engage.
2. **Ghost Vision hero screen** (`/ghost-vision`) — the centerpiece:
   - **Top status strip**: Sentinel/GPS/Network chips (red when offline) + prominent live speed.
   - **Road row**: road name + compass heading badge.
   - **Tactical SVG map** (custom — no external tiles, performant on mid-range Android): geospatial grid, dark curved road polyline with lane edges & dashed center, risk-highlighted segment, cyan FOV cone (gradient), 3 radar rings, animated radar sweep (3.5s rotation), 4 nearby Sentinel-vehicle markers (electric blue), hazard markers with crosshair + outer ring + glow + pulse animation (reanimated), live distance pill above active hazard, top-right compass.
   - **Hazard Info Card** (animated entrance): HIGH/MED/LOW RISK tag, observation age, hazard title with icon, Distance / Confidence / Sources metric row, Northbound lane direction, source attribution + confirms count, **RECOMMENDED · REDUCE SPEED** call-to-action panel (tinted by risk).
   - **Bottom Action Row** (56dp+ touch targets): Drive View · Confirm (primary cyan) · Report · Voice/Muted.
3. **Real interactions**:
   - `expo-haptics` on every action (heavy / medium / light / selection).
   - `expo-speech` TTS voice alert: "Stationary Vehicle Ahead. 180 metres ahead. Reduce speed." Mutable via the Voice button.
   - Tap any hazard marker on the map to switch the active hazard.

## Backend (FastAPI + MongoDB, idempotent seed)
- `GET /api/sentinel/status` — connection, GPS, network, speed, road, heading, nearby count.
- `GET /api/sentinel/hazards` — hazards on this segment (hz-001 stationary vehicle 180m 91%, hz-002 pothole 340m 76%).
- `GET /api/sentinel/nearby-vehicles` — 4 Sentinel-connected vehicles with map coords.
- `POST /api/sentinel/hazards/{id}/confirm` — community-validate a hazard (+1).
- `POST /api/sentinel/hazards/{id}/report-incorrect` — flag false-positive (+1).
- No `_id` leakage; uuid string ids; `datetime` UTC where applicable.

## Design System (`/app/design_guidelines.json`)
- **Personality**: Dark-First Utility tactical. No purple/violet, no neon overload, no glass.
- **Palette**: surface `#090A0C`, secondary `#111418`, tertiary `#1E232A`, cyan `#00F0FF`, electric blue `#3399FF`, amber `#D29922`, red `#F85149`, green `#2EA043`.
- **Type**: Inter, weights ≤ 500. Numerics (speed/distance/confidence) intentionally oversized.
- **Spacing**: strict 8pt grid. Min 48dp touch targets — actions are 56dp.

## Tech Stack
- Expo SDK 54, Expo Router, TypeScript, react-native-svg, react-native-reanimated, expo-haptics, expo-speech, expo-linear-gradient, @expo/vector-icons.
- FastAPI + Motor + MongoDB.

## Future Work (deferred)
- Real OSM tile layer behind the tactical overlay (toggleable via app.json plugin).
- WebSocket push for live hazard broadcasts.
- Driver onboarding + fleet profiles.
- Voice-only "eyes-off" mode for delivery riders.
- Offline-first hazard cache + sync.

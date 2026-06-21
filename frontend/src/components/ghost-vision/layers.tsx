// Per-layer renderers for the Ghost Vision world map.
// All consume the shared Projector and a slice of the WorldModel; nothing else.

import React from "react";
import {
  G,
  Path,
  Polygon,
  Circle,
  Line,
  Rect,
} from "react-native-svg";
import Animated, {
  useAnimatedProps,
  useSharedValue,
  withRepeat,
  withTiming,
  Easing,
  interpolate,
} from "react-native-reanimated";
import { colors } from "@/src/theme";
import type {
  WorldModel,
  Hazard,
  NearbyVehicle,
  OccupiedRegion,
  GeoPoint,
} from "@/src/types/sentinel";
import type { Projector } from "./projection";

const AnimatedCircle = Animated.createAnimatedComponent(Circle);

function pointsString(points: GeoPoint[], proj: Projector): string {
  return points
    .map((p) => {
      const { x, y } = proj.project(p);
      return `${x},${y}`;
    })
    .join(" ");
}

// =================== Static world layer ===================
export function StaticWorldLayer({
  worldModel,
  proj,
}: {
  worldModel: WorldModel;
  proj: Projector;
}) {
  return (
    <G>
      {/* Buildings */}
      {worldModel.buildings.map((b) => (
        <Polygon
          key={b.id}
          points={pointsString(b.polygon, proj)}
          fill="#161A1F"
          stroke="#2A3038"
          strokeWidth={0.8}
        />
      ))}

      {/* Safe driving corridor */}
      <Polygon
        points={pointsString(worldModel.roadCorridor, proj)}
        fill={colors.brand}
        opacity={0.04}
      />

      {/* Roads */}
      {worldModel.roads.map((r) => {
        const pts = r.path.map((p) => proj.project(p));
        const d = pts
          .map((pt, i) => `${i === 0 ? "M" : "L"} ${pt.x} ${pt.y}`)
          .join(" ");
        const isMain = r.id === "gst";
        return (
          <G key={r.id}>
            <Path
              d={d}
              stroke="#1F252D"
              strokeWidth={isMain ? 26 : 16}
              fill="none"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <Path
              d={d}
              stroke="#2A3038"
              strokeWidth={isMain ? 22 : 14}
              fill="none"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            {isMain && (
              <Path
                d={d}
                stroke="#3D4753"
                strokeWidth={1.2}
                strokeDasharray="8 10"
                fill="none"
              />
            )}
          </G>
        );
      })}
    </G>
  );
}

// =================== Field of view layer ===================
export function FieldOfViewLayer({
  egoPx,
  headingDeg,
  width,
  height,
}: {
  egoPx: { x: number; y: number };
  headingDeg: number;
  width: number;
  height: number;
}) {
  // Build a triangular cone pointing along headingDeg (0 = north on screen).
  const rangePx = Math.max(width, height) * 0.65;
  const halfAngle = (28 * Math.PI) / 180; // 56° total FOV
  const theta = ((headingDeg - 90) * Math.PI) / 180; // svg 0° = east, map heading 0° = north
  const leftA = theta - halfAngle;
  const rightA = theta + halfAngle;
  const lp = {
    x: egoPx.x + Math.cos(leftA) * rangePx,
    y: egoPx.y + Math.sin(leftA) * rangePx,
  };
  const rp = {
    x: egoPx.x + Math.cos(rightA) * rangePx,
    y: egoPx.y + Math.sin(rightA) * rangePx,
  };
  const points = `${egoPx.x},${egoPx.y} ${lp.x},${lp.y} ${rp.x},${rp.y}`;
  return (
    <Polygon
      points={points}
      fill={colors.brand}
      opacity={0.12}
      stroke={colors.brand}
      strokeWidth={0.5}
      strokeOpacity={0.25}
    />
  );
}

// =================== Occupied region layer ===================
function regionFill(r: OccupiedRegion) {
  switch (r.objectType) {
    case "vehicle":
      return { fill: colors.brandSecondary, stroke: colors.brandSecondary };
    case "road_obstruction":
      return { fill: colors.warning, stroke: colors.warning };
    case "pedestrian":
      return { fill: "#F2C94C", stroke: "#F2C94C" };
    case "unknown":
    default:
      return { fill: "#3A434F", stroke: colors.onSurfaceTertiary };
  }
}

export function OccupiedRegionLayer({
  regions,
  proj,
}: {
  regions: OccupiedRegion[];
  proj: Projector;
}) {
  return (
    <G>
      {regions.map((r) => {
        const tone = regionFill(r);
        const isUncertain = r.visibilityState === "uncertain" || r.objectType === "unknown";
        const fillOpacity = isUncertain ? 0.16 : 0.42;
        return (
          <G key={r.id}>
            <Polygon
              points={pointsString(r.polygon, proj)}
              fill={tone.fill}
              fillOpacity={fillOpacity}
              stroke={tone.stroke}
              strokeWidth={1}
              strokeOpacity={isUncertain ? 0.85 : 1}
              strokeDasharray={isUncertain ? "3 3" : undefined}
            />
          </G>
        );
      })}
    </G>
  );
}

// =================== Vehicle layer (nearby Sentinel) ===================
export function VehicleLayer({
  vehicles,
  proj,
}: {
  vehicles: NearbyVehicle[];
  proj: Projector;
}) {
  return (
    <G>
      {vehicles.map((v) => {
        const p = proj.project(v.location);
        return (
          <G key={v.id}>
            <Circle cx={p.x} cy={p.y} r={9} fill={colors.brandSecondary} opacity={0.18} />
            <Circle cx={p.x} cy={p.y} r={3.5} fill={colors.brandSecondary} />
            <Circle
              cx={p.x}
              cy={p.y}
              r={5}
              stroke={colors.brandSecondary}
              strokeWidth={1}
              fill="none"
            />
          </G>
        );
      })}
    </G>
  );
}

// =================== Ghost (shared) object layer + hazards ===================
export function GhostObjectLayer({
  hazards,
  proj,
  activeId,
  paused = false,
}: {
  hazards: Hazard[];
  proj: Projector;
  activeId?: string;
  paused?: boolean;
}) {
  const pulse = useSharedValue(0);
  React.useEffect(() => {
    if (paused) {
      // Cancel any running animation so the pulse stops while the screen is unfocused.
      pulse.value = 0;
      return;
    }
    pulse.value = withRepeat(
      withTiming(1, { duration: 1800, easing: Easing.out(Easing.quad) }),
      -1,
      false
    );
  }, [pulse, paused]);

  const pulseProps = useAnimatedProps(() => ({
    r: interpolate(pulse.value, [0, 1], [12, 44]),
    opacity: interpolate(pulse.value, [0, 1], [0.55, 0]),
  }));

  return (
    <G>
      {hazards.map((h) => {
        const c = proj.project(h.location);
        const tint =
          h.risk === "high" ? colors.error : h.risk === "medium" ? colors.warning : colors.success;
        const isActive = h.id === activeId;
        const isGhost = h.sourceType === "shared_vehicle" || h.visibilityState === "hidden";
        return (
          <G key={h.id}>
            {/* Polygon footprint if present — translucent + dashed for Ghost */}
            {h.polygon && (
              <Polygon
                points={pointsString(h.polygon, proj)}
                fill={tint}
                fillOpacity={isGhost ? 0.12 : 0.32}
                stroke={tint}
                strokeWidth={1.2}
                strokeDasharray={isGhost ? "4 3" : undefined}
              />
            )}

            {/* Pulse for the active hazard */}
            {isActive && (
              <AnimatedCircle
                cx={c.x}
                cy={c.y}
                fill="none"
                stroke={tint}
                strokeWidth={1.2}
                animatedProps={pulseProps}
              />
            )}

            {/* Marker rings + crosshair */}
            <Circle cx={c.x} cy={c.y} r={16} stroke={tint} strokeWidth={1.5} fill="none"
              strokeDasharray={isGhost ? "3 3" : undefined} />
            <Circle cx={c.x} cy={c.y} r={5} fill={tint} opacity={isGhost ? 0.85 : 1} />
            <Line x1={c.x - 24} y1={c.y} x2={c.x - 16} y2={c.y} stroke={tint} strokeWidth={1} />
            <Line x1={c.x + 16} y1={c.y} x2={c.x + 24} y2={c.y} stroke={tint} strokeWidth={1} />
            <Line x1={c.x} y1={c.y - 24} x2={c.x} y2={c.y - 16} stroke={tint} strokeWidth={1} />
            <Line x1={c.x} y1={c.y + 16} x2={c.x} y2={c.y + 24} stroke={tint} strokeWidth={1} />
          </G>
        );
      })}
    </G>
  );
}

// =================== Ego vehicle marker ===================
export function EgoMarker({
  egoPx,
  headingDeg,
}: {
  egoPx: { x: number; y: number };
  headingDeg: number;
}) {
  const theta = (headingDeg * Math.PI) / 180;
  // forward (north) vector after rotation
  const fx = Math.sin(theta);
  const fy = -Math.cos(theta);
  const lx = Math.cos(theta);
  const ly = Math.sin(theta);
  const tip = { x: egoPx.x + fx * 12, y: egoPx.y + fy * 12 };
  const back = { x: egoPx.x - fx * 6, y: egoPx.y - fy * 6 };
  const left = { x: back.x - lx * 9, y: back.y - ly * 9 };
  const right = { x: back.x + lx * 9, y: back.y + ly * 9 };
  return (
    <G>
      <Circle cx={egoPx.x} cy={egoPx.y} r={22} fill={colors.brand} opacity={0.08} />
      <Circle cx={egoPx.x} cy={egoPx.y} r={14} stroke={colors.brand} strokeWidth={1} fill="none" opacity={0.4} />
      <Polygon
        points={`${tip.x},${tip.y} ${left.x},${left.y} ${back.x},${back.y} ${right.x},${right.y}`}
        fill={colors.brand}
      />
    </G>
  );
}

// =================== Grid (subtle) ===================
export function GridLayer({ width, height }: { width: number; height: number }) {
  const rows = 14;
  const cols = 8;
  return (
    <G opacity={0.12}>
      {Array.from({ length: rows + 1 }).map((_, i) => (
        <Line
          key={`gh${i}`}
          x1={0}
          x2={width}
          y1={(height / rows) * i}
          y2={(height / rows) * i}
          stroke={colors.border}
          strokeWidth={1}
        />
      ))}
      {Array.from({ length: cols + 1 }).map((_, i) => (
        <Line
          key={`gv${i}`}
          y1={0}
          y2={height}
          x1={(width / cols) * i}
          x2={(width / cols) * i}
          stroke={colors.border}
          strokeWidth={1}
        />
      ))}
    </G>
  );
}

// =================== Background ===================
export function MapBackground({ width, height }: { width: number; height: number }) {
  return <Rect x={0} y={0} width={width} height={height} fill="#06080A" />;
}

// Core structured world-model map. Renders all layers from a WorldModel via the projection module.
// Works identically for Demo Scenario data and (future) Live-Geo data — same shape.

import React, { useMemo } from "react";
import { View, StyleSheet } from "react-native";
import Svg, { Defs, ClipPath, Polygon } from "react-native-svg";
import { colors } from "@/src/theme";
import type { GeoPoint, Hazard, WorldModel } from "@/src/types/sentinel";
import { makeProjector, type Bounds } from "./projection";
import {
  EgoMarker,
  FieldOfViewLayer,
  GhostObjectLayer,
  GridLayer,
  MapBackground,
  OccupiedRegionLayer,
  StaticWorldLayer,
  VehicleLayer,
  pointsString,
} from "./layers";

type Props = {
  width: number;
  height: number;
  worldModel: WorldModel;
  /** Override ego location/heading (used in Live Geo mode). */
  egoOverride?: { location?: GeoPoint; headingDegrees?: number };
  /** Override map bounds (used in Live Geo mode to follow the user). */
  boundsOverride?: Bounds;
  activeHazardId?: string;
  /** Pause animations (e.g. while the screen is unfocused). */
  paused?: boolean;
  onHazardPress?: (h: Hazard) => void;
};

export default function WorldMap({
  width,
  height,
  worldModel,
  egoOverride,
  boundsOverride,
  activeHazardId,
  paused = false,
  onHazardPress,
}: Props) {
  const ego = useMemo(
    () => ({
      location: egoOverride?.location ?? worldModel.ego.location,
      headingDegrees:
        typeof egoOverride?.headingDegrees === "number"
          ? egoOverride.headingDegrees
          : worldModel.ego.headingDegrees,
    }),
    [egoOverride, worldModel.ego]
  );

  const bounds = boundsOverride ?? (worldModel.mapBounds as Bounds);
  const proj = useMemo(() => makeProjector(bounds, width, height), [bounds, width, height]);
  const egoPx = proj.project(ego.location);

  return (
    <View style={[styles.wrap, { width, height }]} testID="world-map">
      <Svg width={width} height={height}>
        <Defs>
          <ClipPath id="corridor-clip">
            <Polygon points={pointsString(worldModel.roadCorridor, proj)} />
          </ClipPath>
        </Defs>
        <MapBackground width={width} height={height} />
        <GridLayer width={width} height={height} />
        <StaticWorldLayer worldModel={worldModel} proj={proj} />
        <FieldOfViewLayer
          egoPx={egoPx}
          headingDeg={ego.headingDegrees}
          width={width}
          height={height}
          clipPath="url(#corridor-clip)"
        />
        <OccupiedRegionLayer regions={worldModel.occupiedRegions} proj={proj} />
        <VehicleLayer vehicles={worldModel.nearbyVehicles} proj={proj} />
        <GhostObjectLayer
          hazards={worldModel.hazards}
          proj={proj}
          activeId={activeHazardId}
          paused={paused}
        />
        <EgoMarker egoPx={egoPx} headingDeg={ego.headingDegrees} />
      </Svg>

      {/* Tap-handler overlay for hazards (SVG onPress is unreliable on web preview) */}
      {worldModel.hazards.map((h) => {
        const p = proj.project(h.location);
        return (
          <View
            key={h.id}
            testID={`hazard-marker-${h.id}`}
            onTouchEnd={() => onHazardPress?.(h)}
            style={[styles.tapTarget, { left: p.x - 24, top: p.y - 24 }]}
          />
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { overflow: "hidden", backgroundColor: colors.surface },
  tapTarget: {
    position: "absolute",
    width: 48,
    height: 48,
    borderRadius: 24,
  },
});

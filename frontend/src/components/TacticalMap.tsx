import React, { useMemo } from "react";
import { View, StyleSheet } from "react-native";
import Svg, {
  Defs,
  LinearGradient as SvgLinearGradient,
  Stop,
  RadialGradient,
  Polygon,
  Path,
  Line,
  Circle,
  G,
  Rect,
} from "react-native-svg";
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withRepeat,
  withTiming,
  Easing,
  useAnimatedProps,
  interpolate,
} from "react-native-reanimated";
import { colors } from "@/src/theme";
import type { Hazard, NearbyVehicle } from "@/src/api/sentinel";

const AnimatedCircle = Animated.createAnimatedComponent(Circle);

type Props = {
  width: number;
  height: number;
  hazards: Hazard[];
  vehicles: NearbyVehicle[];
  activeHazardId?: string;
  onHazardPress?: (h: Hazard) => void;
};

export default function TacticalMap({
  width,
  height,
  hazards,
  vehicles,
  activeHazardId,
  onHazardPress,
}: Props) {
  // Radar sweep rotation
  const sweep = useSharedValue(0);
  React.useEffect(() => {
    sweep.value = withRepeat(
      withTiming(360, { duration: 3500, easing: Easing.linear }),
      -1,
      false
    );
  }, [sweep]);

  // Hazard pulse
  const pulse = useSharedValue(0);
  React.useEffect(() => {
    pulse.value = withRepeat(
      withTiming(1, { duration: 1800, easing: Easing.out(Easing.quad) }),
      -1,
      false
    );
  }, [pulse]);

  const sweepStyle = useAnimatedStyle(() => ({
    transform: [{ rotate: `${sweep.value}deg` }],
  }));

  const pulseProps = useAnimatedProps(() => ({
    r: interpolate(pulse.value, [0, 1], [10, 40]),
    opacity: interpolate(pulse.value, [0, 1], [0.55, 0]),
  }));

  // Road polyline points (curved S-shape going up)
  const roadPath = useMemo(() => {
    const cx = width * 0.5;
    return `M ${cx} ${height}
            C ${cx - 20} ${height * 0.78}, ${width * 0.35} ${height * 0.65}, ${cx} ${height * 0.5}
            C ${width * 0.65} ${height * 0.38}, ${width * 0.42} ${height * 0.22}, ${width * 0.5} ${0}`;
  }, [width, height]);

  const laneOffset = (dx: number) =>
    `M ${width * 0.5 + dx} ${height}
     C ${width * 0.5 + dx - 20} ${height * 0.78}, ${width * 0.35 + dx} ${height * 0.65}, ${width * 0.5 + dx} ${height * 0.5}
     C ${width * 0.65 + dx} ${height * 0.38}, ${width * 0.42 + dx} ${height * 0.22}, ${width * 0.5 + dx} ${0}`;

  // user vehicle position (bottom middle)
  const userX = width * 0.5;
  const userY = height * 0.86;

  // FOV cone polygon (cone shooting upward from user)
  const fov = useMemo(() => {
    const apexX = userX;
    const apexY = userY - 8;
    const leftX = userX - width * 0.45;
    const rightX = userX + width * 0.45;
    const topY = height * 0.05;
    return `${apexX},${apexY} ${leftX},${topY} ${rightX},${topY}`;
  }, [width, height, userX, userY]);

  return (
    <View style={{ width, height, overflow: "hidden" }} testID="tactical-map">
      {/* Base SVG layer */}
      <Svg width={width} height={height}>
        <Defs>
          <SvgLinearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
            <Stop offset="0" stopColor="#0A1014" stopOpacity="1" />
            <Stop offset="1" stopColor="#050709" stopOpacity="1" />
          </SvgLinearGradient>
          <SvgLinearGradient id="fov" x1="0" y1="1" x2="0" y2="0">
            <Stop offset="0" stopColor={colors.brand} stopOpacity="0.32" />
            <Stop offset="0.6" stopColor={colors.brand} stopOpacity="0.08" />
            <Stop offset="1" stopColor={colors.brand} stopOpacity="0" />
          </SvgLinearGradient>
          <RadialGradient id="hazardGlow" cx="50%" cy="50%" r="50%">
            <Stop offset="0" stopColor={colors.warning} stopOpacity="0.55" />
            <Stop offset="1" stopColor={colors.warning} stopOpacity="0" />
          </RadialGradient>
          <SvgLinearGradient id="riskSeg" x1="0" y1="0" x2="0" y2="1">
            <Stop offset="0" stopColor={colors.error} stopOpacity="0.6" />
            <Stop offset="1" stopColor={colors.warning} stopOpacity="0.05" />
          </SvgLinearGradient>
        </Defs>

        {/* Background */}
        <Rect x="0" y="0" width={width} height={height} fill="url(#bg)" />

        {/* Grid */}
        <G opacity={0.18}>
          {Array.from({ length: 12 }).map((_, i) => (
            <Line
              key={`h${i}`}
              x1="0"
              x2={width}
              y1={(height / 12) * i}
              y2={(height / 12) * i}
              stroke={colors.border}
              strokeWidth="1"
            />
          ))}
          {Array.from({ length: 8 }).map((_, i) => (
            <Line
              key={`v${i}`}
              y1="0"
              y2={height}
              x1={(width / 8) * i}
              x2={(width / 8) * i}
              stroke={colors.border}
              strokeWidth="1"
            />
          ))}
        </G>

        {/* Risk highlighted segment (under hazard) */}
        <Path
          d={roadPath}
          stroke="url(#riskSeg)"
          strokeWidth="46"
          fill="none"
          strokeLinecap="round"
          opacity={0.45}
        />

        {/* Road body */}
        <Path
          d={roadPath}
          stroke="#1C2128"
          strokeWidth="36"
          fill="none"
          strokeLinecap="round"
        />
        {/* Center dashed line */}
        <Path
          d={roadPath}
          stroke="#3A4250"
          strokeWidth="1.5"
          strokeDasharray="8 10"
          fill="none"
        />
        {/* Lane edges */}
        <Path d={laneOffset(-18)} stroke="#2A3038" strokeWidth="1" fill="none" />
        <Path d={laneOffset(18)} stroke="#2A3038" strokeWidth="1" fill="none" />

        {/* FOV cone */}
        <Polygon points={fov} fill="url(#fov)" />

        {/* Radar concentric rings */}
        <G opacity={0.35}>
          <Circle cx={userX} cy={userY} r={80} stroke={colors.brand} strokeWidth="0.8" fill="none" />
          <Circle cx={userX} cy={userY} r={160} stroke={colors.brand} strokeWidth="0.6" fill="none" />
          <Circle cx={userX} cy={userY} r={260} stroke={colors.brand} strokeWidth="0.4" fill="none" />
        </G>

        {/* Nearby Sentinel vehicles */}
        {vehicles.map((v) => {
          const vx = v.x * width;
          const vy = v.y * height;
          return (
            <G key={v.id} testID={`nearby-vehicle-${v.id}`}>
              <Circle cx={vx} cy={vy} r={9} fill={colors.brandSecondary} opacity={0.18} />
              <Circle cx={vx} cy={vy} r={4} fill={colors.brandSecondary} />
              <Circle cx={vx} cy={vy} r={5} stroke={colors.brandSecondary} strokeWidth="1" fill="none" />
            </G>
          );
        })}

        {/* Hazard markers */}
        {hazards.map((h) => {
          const hx = h.x * width;
          const hy = h.y * height;
          const tint =
            h.risk === "high" ? colors.error : h.risk === "medium" ? colors.warning : colors.success;
          const isActive = activeHazardId === h.id;
          return (
            <G key={h.id} testID={`hazard-marker-${h.id}`} onPress={() => onHazardPress?.(h)}>
              {/* Glow */}
              <Circle cx={hx} cy={hy} r={36} fill="url(#hazardGlow)" />
              {/* Pulse ring (only for active) */}
              {isActive && (
                <AnimatedCircle
                  cx={hx}
                  cy={hy}
                  fill="none"
                  stroke={tint}
                  strokeWidth="1.2"
                  animatedProps={pulseProps}
                />
              )}
              {/* Outer ring */}
              <Circle cx={hx} cy={hy} r={14} stroke={tint} strokeWidth="1.5" fill="none" />
              {/* Inner dot */}
              <Circle cx={hx} cy={hy} r={6} fill={tint} />
              {/* Crosshair */}
              <Line x1={hx - 22} y1={hy} x2={hx - 14} y2={hy} stroke={tint} strokeWidth="1" />
              <Line x1={hx + 14} y1={hy} x2={hx + 22} y2={hy} stroke={tint} strokeWidth="1" />
              <Line x1={hx} y1={hy - 22} x2={hx} y2={hy - 14} stroke={tint} strokeWidth="1" />
            </G>
          );
        })}

        {/* User vehicle arrow (cyan triangle pointing up) */}
        <G testID="user-vehicle">
          <Circle cx={userX} cy={userY} r={22} fill={colors.brand} opacity={0.08} />
          <Circle cx={userX} cy={userY} r={14} stroke={colors.brand} strokeWidth="1" fill="none" opacity={0.4} />
          <Polygon
            points={`${userX},${userY - 12} ${userX - 9},${userY + 8} ${userX},${userY + 4} ${userX + 9},${userY + 8}`}
            fill={colors.brand}
          />
        </G>
      </Svg>

      {/* Radar sweep — overlay using Animated rotation */}
      <Animated.View
        pointerEvents="none"
        style={[
          styles.sweepWrap,
          { left: userX - width, top: userY - width, width: width * 2, height: width * 2 },
          sweepStyle,
        ]}
      >
        <Svg width={width * 2} height={width * 2}>
          <Defs>
            <SvgLinearGradient id="sweep" x1="0" y1="0" x2="1" y2="0">
              <Stop offset="0" stopColor={colors.brand} stopOpacity="0" />
              <Stop offset="1" stopColor={colors.brand} stopOpacity="0.22" />
            </SvgLinearGradient>
          </Defs>
          <Path
            d={`M ${width} ${width} L ${width * 2} ${width} A ${width} ${width} 0 0 0 ${width + Math.cos(-Math.PI / 6) * width} ${width + Math.sin(-Math.PI / 6) * width} Z`}
            fill="url(#sweep)"
          />
        </Svg>
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  sweepWrap: { position: "absolute" },
});

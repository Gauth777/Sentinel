import React, { useEffect, useState, useCallback, useRef } from "react";
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  StatusBar,
  ActivityIndicator,
  useWindowDimensions,
  ScrollView,
} from "react-native";
import { SafeAreaView, useSafeAreaInsets } from "react-native-safe-area-context";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import * as Speech from "expo-speech";
import { useRouter } from "expo-router";
import Animated, { FadeIn, FadeInDown } from "react-native-reanimated";
import { colors, spacing, radius, fonts } from "@/src/theme";
import { api, type SentinelStatus, type Hazard, type NearbyVehicle } from "@/src/api/sentinel";
import TacticalMap from "@/src/components/TacticalMap";

export default function GhostVisionScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { width } = useWindowDimensions();

  const [status, setStatus] = useState<SentinelStatus | null>(null);
  const [hazards, setHazards] = useState<Hazard[]>([]);
  const [vehicles, setVehicles] = useState<NearbyVehicle[]>([]);
  const [active, setActive] = useState<Hazard | null>(null);
  const [loading, setLoading] = useState(true);
  const [muted, setMuted] = useState(false);
  const spokenRef = useRef(false);
  const [actionFlash, setActionFlash] = useState<string | null>(null);

  // tactical map dimensions
  const mapH = Math.round(width * 1.15);

  useEffect(() => {
    let alive = true;
    Promise.all([api.status(), api.hazards(), api.nearby()])
      .then(([s, h, v]) => {
        if (!alive) return;
        setStatus(s);
        setHazards(h);
        setVehicles(v);
        // primary hazard = highest risk closest
        const primary =
          h.find((x) => x.risk === "high") ?? h[0] ?? null;
        setActive(primary);
      })
      .catch(() => {})
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
      Speech.stop();
    };
  }, []);

  // TTS voice alert (once per active hazard, unless muted)
  useEffect(() => {
    if (!active || muted || spokenRef.current) return;
    spokenRef.current = true;
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning).catch(() => {});
    Speech.speak(
      `${active.label}. ${active.distance_m} metres ahead. ${active.recommended_action}.`,
      { rate: 0.95, pitch: 1.0 }
    );
  }, [active, muted]);

  const onConfirm = useCallback(async () => {
    if (!active) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    setActionFlash("Hazard confirmed");
    try {
      const r = await api.confirm(active.id);
      setHazards((prev) =>
        prev.map((h) => (h.id === r.id ? { ...h, confirmed: r.confirmed } : h))
      );
      setActive((a) => (a ? { ...a, confirmed: r.confirmed } : a));
    } catch {}
    setTimeout(() => setActionFlash(null), 1800);
  }, [active]);

  const onReport = useCallback(async () => {
    if (!active) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    setActionFlash("Report submitted");
    try {
      const r = await api.report(active.id);
      setHazards((prev) =>
        prev.map((h) =>
          h.id === r.id ? { ...h, reported_incorrect: r.reported_incorrect } : h
        )
      );
      setActive((a) =>
        a ? { ...a, reported_incorrect: r.reported_incorrect } : a
      );
    } catch {}
    setTimeout(() => setActionFlash(null), 1800);
  }, [active]);

  const onMute = useCallback(() => {
    Haptics.selectionAsync().catch(() => {});
    setMuted((m) => {
      const next = !m;
      if (next) Speech.stop();
      return next;
    });
  }, []);

  const onReturn = useCallback(() => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
    Speech.stop();
    router.back();
  }, [router]);

  if (loading) {
    return (
      <View style={[styles.root, styles.center]} testID="ghost-vision-loading">
        <ActivityIndicator color={colors.brand} />
        <Text style={styles.loadingText}>Initialising Ghost Vision…</Text>
      </View>
    );
  }

  const riskColor =
    active?.risk === "high"
      ? colors.error
      : active?.risk === "medium"
      ? colors.warning
      : colors.success;

  return (
    <View style={styles.root} testID="ghost-vision-screen">
      <StatusBar barStyle="light-content" />
      <SafeAreaView style={{ flex: 1 }} edges={["top", "bottom"]}>
        {/* === Top Status Strip === */}
        <View style={styles.topStrip} testID="top-status-strip">
          <View style={styles.topGroup}>
            <StatusChip
              icon="radio-tower"
              ok={!!status?.connected}
              label={status?.connected ? "SENTINEL" : "OFFLINE"}
              testID="sentinel-status-chip"
            />
            <StatusChip
              icon="crosshairs-gps"
              ok={!!status?.gps_locked}
              label={status?.gps_locked ? "GPS" : "NO GPS"}
              testID="gps-status-chip"
            />
            <StatusChip
              icon="signal-cellular-3"
              ok={!!status?.network && status.network !== "OFFLINE"}
              label={status?.network ?? "—"}
              testID="network-status-chip"
            />
          </View>
          <View style={styles.speedBlock} testID="top-speed">
            <Text style={styles.speedTopNum}>{status?.speed_kmh ?? 0}</Text>
            <Text style={styles.speedTopUnit}>km/h</Text>
          </View>
        </View>

        <View style={styles.roadRow}>
          <MaterialCommunityIcons name="road-variant" size={14} color={colors.onSurfaceSecondary} />
          <Text style={styles.roadText}>{status?.road_name}</Text>
          <View style={styles.headingBadge}>
            <MaterialCommunityIcons name="navigation" size={11} color={colors.brand} />
            <Text style={styles.headingText}>{status?.heading}</Text>
          </View>
        </View>

        {/* === Tactical Map === */}
        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={{ paddingBottom: spacing.lg }}
          showsVerticalScrollIndicator={false}
        >
          <View style={[styles.mapWrap, { height: mapH }]} testID="ghost-vision-map">
            <TacticalMap
              width={width}
              height={mapH}
              hazards={hazards}
              vehicles={vehicles}
              activeHazardId={active?.id}
              onHazardPress={(h) => {
                Haptics.selectionAsync().catch(() => {});
                setActive(h);
                spokenRef.current = false;
              }}
            />

            {/* Distance label pill for active hazard */}
            {active && (
              <Animated.View
                entering={FadeIn.duration(300)}
                style={[
                  styles.distancePill,
                  {
                    left: active.x * width - 40,
                    top: active.y * mapH - 56,
                    borderColor: riskColor,
                  },
                ]}
                testID="active-hazard-distance"
                pointerEvents="none"
              >
                <Text style={[styles.distanceNum, { color: riskColor }]}>
                  {active.distance_m}
                </Text>
                <Text style={styles.distanceUnit}>m</Text>
              </Animated.View>
            )}

            {/* Compass marker */}
            <View style={styles.compass}>
              <MaterialCommunityIcons name="navigation" size={16} color={colors.brand} />
              <Text style={styles.compassLabel}>N</Text>
            </View>

            {/* Action flash */}
            {actionFlash && (
              <Animated.View
                entering={FadeIn.duration(180)}
                style={styles.actionFlash}
                testID="action-flash"
              >
                <MaterialCommunityIcons name="check-circle" size={16} color={colors.brand} />
                <Text style={styles.actionFlashText}>{actionFlash}</Text>
              </Animated.View>
            )}
          </View>

          {/* === Hazard Info Card === */}
          {active && (
            <Animated.View
              entering={FadeInDown.duration(350)}
              style={styles.card}
              testID="hazard-info-card"
            >
              <View style={styles.cardHeader}>
                <View style={[styles.riskTag, { borderColor: riskColor }]}>
                  <View style={[styles.riskDot, { backgroundColor: riskColor }]} />
                  <Text style={[styles.riskTagText, { color: riskColor }]}>
                    {active.risk.toUpperCase()} RISK
                  </Text>
                </View>
                <Text style={styles.cardAge}>{active.observed_seconds_ago}s ago</Text>
              </View>

              <View style={styles.cardTitleRow}>
                <MaterialCommunityIcons
                  name="car-brake-alert"
                  size={26}
                  color={riskColor}
                />
                <Text style={styles.cardTitle} testID="hazard-title">
                  {active.label}
                </Text>
              </View>

              <View style={styles.metricsRow}>
                <Metric label="DISTANCE" value={`${active.distance_m}`} unit="m" big />
                <View style={styles.vline} />
                <Metric
                  label="CONFIDENCE"
                  value={`${active.confidence}`}
                  unit="%"
                  big
                />
                <View style={styles.vline} />
                <Metric label="SOURCES" value={`${active.sources}`} unit="veh" big />
              </View>

              <View style={styles.detailRow}>
                <MaterialCommunityIcons name="arrow-up-bold" size={14} color={colors.onSurfaceSecondary} />
                <Text style={styles.detailText}>{active.direction}</Text>
              </View>
              <View style={styles.detailRow}>
                <MaterialCommunityIcons name="account-multiple-check" size={14} color={colors.onSurfaceSecondary} />
                <Text style={styles.detailText}>
                  Reported by {active.sources} Sentinel vehicle{active.sources > 1 ? "s" : ""}
                  {active.confirmed > 0 ? ` · ${active.confirmed} confirms` : ""}
                </Text>
              </View>

              <View style={styles.actionAdvice} testID="recommended-action">
                <MaterialCommunityIcons name="alert-decagram" size={16} color={riskColor} />
                <Text style={[styles.actionAdviceText, { color: riskColor }]}>
                  RECOMMENDED · {active.recommended_action.toUpperCase()}
                </Text>
              </View>
            </Animated.View>
          )}
        </ScrollView>

        {/* === Bottom Action Row === */}
        <View
          style={[
            styles.actionRow,
            { paddingBottom: Math.max(insets.bottom, spacing.md) },
          ]}
          testID="bottom-action-row"
        >
          <ActionButton
            icon="steering"
            label="Drive View"
            onPress={onReturn}
            testID="return-to-drive-button"
          />
          <ActionButton
            icon="check-circle-outline"
            label="Confirm"
            primary
            onPress={onConfirm}
            testID="confirm-hazard-button"
          />
          <ActionButton
            icon="alert-octagon-outline"
            label="Report"
            onPress={onReport}
            testID="report-incorrect-button"
          />
          <ActionButton
            icon={muted ? "volume-off" : "volume-high"}
            label={muted ? "Muted" : "Voice"}
            onPress={onMute}
            active={muted}
            testID="mute-voice-button"
          />
        </View>
      </SafeAreaView>
    </View>
  );
}

function StatusChip({
  icon,
  label,
  ok,
  testID,
}: {
  icon: keyof typeof MaterialCommunityIcons.glyphMap;
  label: string;
  ok: boolean;
  testID?: string;
}) {
  return (
    <View style={styles.chip} testID={testID}>
      <MaterialCommunityIcons
        name={icon}
        size={12}
        color={ok ? colors.brand : colors.error}
      />
      <Text style={[styles.chipText, !ok && { color: colors.error }]}>{label}</Text>
    </View>
  );
}

function Metric({
  label,
  value,
  unit,
  big,
}: {
  label: string;
  value: string;
  unit?: string;
  big?: boolean;
}) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricLabel}>{label}</Text>
      <View style={{ flexDirection: "row", alignItems: "flex-end" }}>
        <Text style={[styles.metricValue, big && { fontSize: fonts.size.xxl }]}>
          {value}
        </Text>
        {unit && <Text style={styles.metricUnit}>{unit}</Text>}
      </View>
    </View>
  );
}

function ActionButton({
  icon,
  label,
  primary,
  active,
  onPress,
  testID,
}: {
  icon: keyof typeof MaterialCommunityIcons.glyphMap;
  label: string;
  primary?: boolean;
  active?: boolean;
  onPress: () => void;
  testID?: string;
}) {
  return (
    <Pressable
      onPress={onPress}
      testID={testID}
      android_ripple={{ color: "#003844" }}
      style={({ pressed }) => [
        styles.actionBtn,
        primary && styles.actionBtnPrimary,
        active && styles.actionBtnActive,
        pressed && { opacity: 0.85 },
      ]}
    >
      <MaterialCommunityIcons
        name={icon}
        size={22}
        color={primary ? "#000" : active ? colors.warning : colors.onSurface}
      />
      <Text
        style={[
          styles.actionBtnText,
          primary && { color: "#000" },
          active && { color: colors.warning },
        ]}
      >
        {label}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { alignItems: "center", justifyContent: "center" },
  loadingText: {
    color: colors.onSurfaceSecondary,
    marginTop: spacing.md,
    letterSpacing: 2,
    fontSize: fonts.size.sm,
  },

  // Top strip
  topStrip: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
  },
  topGroup: { flexDirection: "row", gap: spacing.sm, alignItems: "center" },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: spacing.sm,
    paddingVertical: 4,
    borderRadius: radius.sm,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
  },
  chipText: {
    color: colors.onSurface,
    fontSize: 10,
    letterSpacing: 1.4,
    fontWeight: "500",
  },
  speedBlock: { flexDirection: "row", alignItems: "flex-end" },
  speedTopNum: {
    color: colors.onSurface,
    fontSize: fonts.size.xxxl,
    fontWeight: "500",
    lineHeight: 34,
    letterSpacing: -0.5,
  },
  speedTopUnit: {
    color: colors.brand,
    fontSize: fonts.size.sm,
    marginLeft: 4,
    marginBottom: 4,
    letterSpacing: 1,
  },

  roadRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
  },
  roadText: { color: colors.onSurfaceSecondary, fontSize: fonts.size.sm, flex: 1 },
  headingBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 2,
    backgroundColor: colors.surfaceSecondary,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  headingText: { color: colors.brand, fontSize: 10, letterSpacing: 1.2, fontWeight: "500" },

  // Map
  mapWrap: {
    width: "100%",
    overflow: "hidden",
    backgroundColor: "#05080A",
    borderTopWidth: 1,
    borderBottomWidth: 1,
    borderColor: colors.border,
  },
  distancePill: {
    position: "absolute",
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderRadius: radius.sm,
    backgroundColor: "rgba(9,10,12,0.92)",
    borderWidth: 1,
    flexDirection: "row",
    alignItems: "flex-end",
    minWidth: 72,
    justifyContent: "center",
  },
  distanceNum: { fontSize: fonts.size.lg, fontWeight: "500", letterSpacing: 0.5 },
  distanceUnit: {
    color: colors.onSurfaceSecondary,
    fontSize: 10,
    marginLeft: 2,
    marginBottom: 3,
  },
  compass: {
    position: "absolute",
    top: spacing.md,
    right: spacing.md,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: 4,
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  compassLabel: {
    color: colors.brand,
    fontSize: 10,
    letterSpacing: 1.2,
    fontWeight: "500",
  },
  actionFlash: {
    position: "absolute",
    top: spacing.md,
    left: spacing.md,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.md,
    backgroundColor: "rgba(0,240,255,0.12)",
    borderWidth: 1,
    borderColor: colors.brand,
  },
  actionFlashText: {
    color: colors.brand,
    fontSize: fonts.size.sm,
    letterSpacing: 1.5,
    fontWeight: "500",
  },

  // Card
  card: {
    marginHorizontal: spacing.lg,
    marginTop: spacing.lg,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    padding: spacing.lg,
  },
  cardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  riskTag: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    borderWidth: 1,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: 4,
  },
  riskDot: { width: 6, height: 6, borderRadius: 3 },
  riskTagText: {
    fontSize: 10,
    letterSpacing: 1.6,
    fontWeight: "500",
  },
  cardAge: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    letterSpacing: 1.2,
  },
  cardTitleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    marginTop: spacing.md,
  },
  cardTitle: {
    color: colors.onSurface,
    fontSize: fonts.size.xl,
    fontWeight: "500",
    flex: 1,
  },
  metricsRow: {
    flexDirection: "row",
    alignItems: "stretch",
    marginTop: spacing.lg,
    backgroundColor: colors.surfaceTertiary,
    borderRadius: radius.md,
    paddingVertical: spacing.md,
  },
  metric: { flex: 1, alignItems: "center" },
  metricLabel: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    letterSpacing: 1.5,
    marginBottom: 4,
  },
  metricValue: {
    color: colors.onSurface,
    fontSize: fonts.size.xl,
    fontWeight: "500",
    lineHeight: 28,
  },
  metricUnit: {
    color: colors.onSurfaceSecondary,
    fontSize: 11,
    marginLeft: 2,
    marginBottom: 4,
  },
  vline: { width: 1, backgroundColor: colors.border, marginVertical: spacing.xs },
  detailRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    marginTop: spacing.md,
  },
  detailText: { color: colors.onSurfaceSecondary, fontSize: fonts.size.sm },
  actionAdvice: {
    marginTop: spacing.md,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: "rgba(248,81,73,0.08)",
    borderWidth: 1,
    borderColor: "rgba(248,81,73,0.35)",
    borderRadius: radius.md,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
  },
  actionAdviceText: {
    fontSize: fonts.size.sm,
    fontWeight: "500",
    letterSpacing: 1.5,
  },

  // Action row
  actionRow: {
    flexDirection: "row",
    paddingHorizontal: spacing.md,
    paddingTop: spacing.md,
    gap: spacing.sm,
    borderTopWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  actionBtn: {
    flex: 1,
    minHeight: 56,
    backgroundColor: colors.surfaceSecondary,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radius.md,
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: spacing.sm,
    gap: 2,
  },
  actionBtnPrimary: {
    backgroundColor: colors.brand,
    borderColor: colors.brand,
  },
  actionBtnActive: {
    borderColor: colors.warning,
    backgroundColor: "rgba(210,153,34,0.08)",
  },
  actionBtnText: {
    color: colors.onSurface,
    fontSize: 11,
    letterSpacing: 1.2,
    fontWeight: "500",
  },
});

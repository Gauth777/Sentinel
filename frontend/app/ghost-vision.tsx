import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { useRouter, useFocusEffect } from "expo-router";
import { useIsFocused } from "@react-navigation/native";
import { colors, spacing, radius, fonts } from "@/src/theme";
import { useGhostVisionData } from "@/src/hooks/useGhostVisionData";
import { useSentinelLocation } from "@/src/hooks/useSentinelLocation";
import WorldMap from "@/src/components/ghost-vision/WorldMap";
import HazardBottomSheet from "@/src/components/ghost-vision/HazardBottomSheet";
import MapLegend from "@/src/components/ghost-vision/MapLegend";
import MapErrorState from "@/src/components/ghost-vision/MapErrorState";
import { boundsAround } from "@/src/components/ghost-vision/projection";
import type { Hazard } from "@/src/types/sentinel";

// process.env.EXPO_PUBLIC_MAP_STYLE_URL is reserved for the native MapLibre adapter
// that ships in the Android development build. Web preview always uses the SVG WorldMap.

export default function GhostVisionScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { width } = useWindowDimensions();

  const { worldModel, status, source, loading, error, confirm, report } =
    useGhostVisionData();
  const loc = useSentinelLocation();
  const isFocused = useIsFocused();

  // Store only the id; derive the live hazard from worldModel so confirm/report
  // counter updates don't reset which hazard the user has selected.
  const [activeHazardId, setActiveHazardId] = useState<string | null>(null);
  const [cardExpanded, setCardExpanded] = useState(false);
  const [muted, setMuted] = useState(false);
  const [actionFlash, setActionFlash] = useState<string | null>(null);
  const spokenForId = useRef<string | null>(null);

  // Pick the primary hazard the first time the world model loads (and only if
  // nothing is selected yet, or the selected id no longer exists).
  useEffect(() => {
    if (!worldModel) return;
    setActiveHazardId((prev) => {
      if (prev && worldModel.hazards.some((h) => h.id === prev)) return prev;
      const primary =
        worldModel.hazards.find((h) => h.routeRelevance === "high") ??
        worldModel.hazards.find((h) => h.risk === "high") ??
        worldModel.hazards[0] ??
        null;
      return primary ? primary.id : null;
    });
  }, [worldModel]);

  const active: Hazard | null = useMemo(() => {
    if (!worldModel || !activeHazardId) return null;
    return worldModel.hazards.find((h) => h.id === activeHazardId) ?? null;
  }, [worldModel, activeHazardId]);

  // TTS voice alert (once per hazard, unless muted).
  useEffect(() => {
    if (!active || muted) return;
    if (spokenForId.current === active.id) return;
    if (active.routeRelevance !== "high" && active.routeRelevance !== "medium") return;
    spokenForId.current = active.id;
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning).catch(() => {});
    Speech.speak(
      `${active.label}. Approximately ${active.distanceMeters} metres ahead. ${active.recommendedAction}.`,
      { rate: 0.95, pitch: 1.0 }
    );
  }, [active, muted]);

  // Pause animations / TTS when screen loses focus.
  // Pause speech (and animations via the `paused` prop below) whenever the screen
  // loses focus. This is the single source of truth for animation pausing.
  useEffect(() => {
    if (!isFocused) {
      Speech.stop();
    }
  }, [isFocused]);

  useFocusEffect(
    useCallback(() => {
      return () => {
        Speech.stop();
      };
    }, [])
  );

  const onConfirm = useCallback(async () => {
    if (!active) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    setActionFlash("Hazard confirmed");
    await confirm(active.id);
    setTimeout(() => setActionFlash(null), 1800);
  }, [active, confirm]);

  const onReport = useCallback(async () => {
    if (!active) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    setActionFlash("Report submitted");
    await report(active.id);
    setTimeout(() => setActionFlash(null), 1800);
  }, [active, report]);

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

  const onEngageLiveGeo = useCallback(() => {
    Haptics.selectionAsync().catch(() => {});
    loc.request();
  }, [loc]);

  const onUseDemoMode = useCallback(() => {
    Haptics.selectionAsync().catch(() => {});
    loc.switchToDemo();
  }, [loc]);

  // Compose ego override + bounds override based on location mode.
  const liveOverride = useMemo(() => {
    if (loc.mode === "live" && loc.location) {
      return {
        egoOverride: {
          location: loc.location,
          headingDegrees: typeof loc.headingDegrees === "number" ? loc.headingDegrees : undefined,
        },
        boundsOverride: boundsAround(loc.location, 280),
      };
    }
    return {};
  }, [loc.mode, loc.location, loc.headingDegrees]);

  if (loading || !worldModel) {
    return (
      <View style={[styles.root, styles.center]} testID="ghost-vision-loading">
        <ActivityIndicator color={colors.brand} />
        <Text style={styles.loadingText}>Initialising Ghost Vision…</Text>
      </View>
    );
  }

  const mapH = Math.round(width * 1.05);
  const isLiveGeo = loc.mode === "live";
  const showLiveError =
    loc.mode === "denied" || loc.mode === "unavailable" || loc.mode === "requesting";

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
              ok={isLiveGeo || !!status?.gps_locked}
              label={isLiveGeo ? "GPS LIVE" : status?.gps_locked ? "GPS" : "NO GPS"}
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
            <Text style={styles.speedTopNum}>{status?.speed_kmh ?? worldModel.ego.speedKmh}</Text>
            <Text style={styles.speedTopUnit}>km/h</Text>
          </View>
        </View>

        <View style={styles.roadRow}>
          <MaterialCommunityIcons name="road-variant" size={14} color={colors.onSurfaceSecondary} />
          <Text style={styles.roadText} numberOfLines={1}>
            {status?.road_name ?? worldModel.roads[0]?.name ?? "—"}
          </Text>
          <ModeBadge source={source} isLiveGeo={isLiveGeo} telemetry={worldModel.telemetrySource} />
        </View>

        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={{ paddingBottom: spacing.md }}
          showsVerticalScrollIndicator={false}
        >
          {/* === World Map === */}
          <View style={[styles.mapWrap, { height: mapH }]} testID="ghost-vision-map">
            <WorldMap
              width={width}
              height={mapH}
              worldModel={worldModel}
              egoOverride={liveOverride.egoOverride}
              boundsOverride={liveOverride.boundsOverride}
              activeHazardId={active?.id}
              paused={!isFocused}
              onHazardPress={(h) => {
                Haptics.selectionAsync().catch(() => {});
                setActiveHazardId(h.id);
                spokenForId.current = null; // allow voice for the new active
              }}
            />

            {/* Distance label above active hazard (informational; live coords) */}
            {active && (
              <View
                style={[
                  styles.distancePill,
                  { top: 12, right: 12, borderColor: riskTint(active.risk), pointerEvents: "none" },
                ]}
                testID="active-hazard-distance"
              >
                <MaterialCommunityIcons name="map-marker-distance" size={12} color={riskTint(active.risk)} />
                <Text style={[styles.distanceNum, { color: riskTint(active.risk) }]}>≈{active.distanceMeters}</Text>
                <Text style={styles.distanceUnit}>m</Text>
              </View>
            )}

            {/* Action flash */}
            {actionFlash && (
              <View style={styles.actionFlash} testID="action-flash">
                <MaterialCommunityIcons name="check-circle" size={16} color={colors.brand} />
                <Text style={styles.actionFlashText}>{actionFlash}</Text>
              </View>
            )}

            {/* Telemetry source pill (TOP LEFT) */}
            <View
              style={[
                styles.scenarioBadge,
                isLiveGeo
                  ? { borderColor: colors.success }
                  : { borderColor: colors.warning },
              ]}
              testID="telemetry-source-badge"
            >
              <MaterialCommunityIcons
                name={isLiveGeo ? "satellite-variant" : "test-tube"}
                size={11}
                color={isLiveGeo ? colors.success : colors.warning}
              />
              <Text
                style={[
                  styles.scenarioBadgeText,
                  { color: isLiveGeo ? colors.success : colors.warning },
                ]}
              >
                {isLiveGeo
                  ? "GPS POSITION PREVIEW · EXPERIMENTAL"
                  : "DEMO SCENARIO · SIMULATED TELEMETRY"}
              </Text>
            </View>

            {/* Attribution */}
            <Text style={styles.attribution} testID="map-attribution">
              Synthetic GST Road demo scenario
            </Text>

            <MapLegend />
          </View>

          {/* GPS Position Preview controls (Experimental — hidden from primary demo path). */}
          {!isLiveGeo && (
            <View style={styles.modeRow}>
              <Pressable
                onPress={onEngageLiveGeo}
                style={({ pressed }) => [styles.modeBtn, pressed && { opacity: 0.85 }]}
                testID="engage-live-geo-button"
                android_ripple={{ color: "#003844" }}
              >
                <MaterialCommunityIcons name="map-marker-radius" size={16} color={colors.brand} />
                <View style={{ flex: 1 }}>
                  <View style={styles.modeBtnTitleRow}>
                    <Text style={styles.modeBtnText}>
                      {loc.mode === "requesting"
                        ? "REQUESTING GPS…"
                        : "GPS POSITION PREVIEW"}
                    </Text>
                    <Text style={styles.experimentalTag}>EXPERIMENTAL</Text>
                  </View>
                  <Text style={styles.modeBtnHint}>
                    Uses live device position with simulated Sentinel world-model overlays. Roads and buildings around your real location are not loaded.
                  </Text>
                </View>
              </Pressable>
              {loc.mode !== "idle" && loc.mode !== "requesting" && (
                <Pressable
                  onPress={onUseDemoMode}
                  style={({ pressed }) => [styles.modeBtnGhost, pressed && { opacity: 0.85 }]}
                  testID="use-demo-mode-button"
                >
                  <Text style={styles.modeBtnGhostText}>Use Demo Scenario</Text>
                </Pressable>
              )}
            </View>
          )}

          {/* Map error / fallback panel */}
          {showLiveError && loc.error && (
            <MapErrorState
              title={
                loc.mode === "denied"
                  ? "Location permission denied"
                  : loc.mode === "requesting"
                  ? "Acquiring GPS…"
                  : "Live GPS unavailable"
              }
              message={loc.error}
              onRetry={() => loc.request()}
              onUseDemo={() => loc.switchToDemo()}
            />
          )}

          {/* Backend offline notice (non-blocking) */}
          {error && source === "demo" && (
            <View style={styles.banner} testID="offline-banner">
              <MaterialCommunityIcons name="cloud-off-outline" size={14} color={colors.warning} />
              <Text style={styles.bannerText}>
                Backend unreachable — using bundled demo data
              </Text>
            </View>
          )}

          {/* === Hazard Bottom Sheet === */}
          {active && (
            <HazardBottomSheet
              hazard={active}
              expanded={cardExpanded}
              onToggle={() => {
                Haptics.selectionAsync().catch(() => {});
                setCardExpanded((e) => !e);
              }}
            />
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
          <ActionButton icon="steering" label="Drive View" onPress={onReturn} testID="return-to-drive-button" />
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
            active={muted}
            onPress={onMute}
            testID="mute-voice-button"
          />
        </View>
      </SafeAreaView>
    </View>
  );
}

function riskTint(risk: Hazard["risk"]) {
  return risk === "high" ? colors.error : risk === "medium" ? colors.warning : colors.success;
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
      <MaterialCommunityIcons name={icon} size={12} color={ok ? colors.brand : colors.error} />
      <Text style={[styles.chipText, !ok && { color: colors.error }]}>{label}</Text>
    </View>
  );
}

function ModeBadge({
  source,
  isLiveGeo,
  telemetry,
}: {
  source: "backend" | "demo";
  isLiveGeo: boolean;
  telemetry: string;
}) {
  const isLive = isLiveGeo;
  return (
    <View
      style={[
        styles.modeBadge,
        { borderColor: isLive ? colors.success : colors.warning },
      ]}
      testID="mode-badge"
    >
      <View
        style={[
          styles.modeBadgeDot,
          { backgroundColor: isLive ? colors.success : colors.warning },
        ]}
      />
      <Text
        style={[
          styles.modeBadgeText,
          { color: isLive ? colors.success : colors.warning },
        ]}
      >
        {isLive ? "LIVE GEO" : telemetry === "demo" ? "DEMO" : source === "backend" ? "CACHED" : "DEMO"}
      </Text>
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
  chipText: { color: colors.onSurface, fontSize: 10, letterSpacing: 1.4, fontWeight: "500" },
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
  modeBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: colors.surfaceSecondary,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderRadius: radius.sm,
    borderWidth: 1,
  },
  modeBadgeDot: { width: 6, height: 6, borderRadius: 3 },
  modeBadgeText: { fontSize: 10, letterSpacing: 1.4, fontWeight: "500" },
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
    paddingVertical: 4,
    borderRadius: radius.sm,
    backgroundColor: "rgba(9,10,12,0.92)",
    borderWidth: 1,
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 4,
  },
  distanceNum: { fontSize: fonts.size.lg, fontWeight: "500", letterSpacing: 0.5 },
  distanceUnit: { color: colors.onSurfaceSecondary, fontSize: 10, marginBottom: 2 },
  actionFlash: {
    position: "absolute",
    top: 60,
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
  scenarioBadge: {
    position: "absolute",
    top: spacing.md,
    left: spacing.md,
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: "rgba(9,10,12,0.85)",
    paddingHorizontal: spacing.sm,
    paddingVertical: 5,
    borderRadius: radius.sm,
    borderWidth: 1,
  },
  scenarioBadgeText: { fontSize: 9, letterSpacing: 1.3, fontWeight: "500" },
  attribution: {
    position: "absolute",
    right: spacing.sm,
    bottom: 4,
    color: colors.onSurfaceTertiary,
    fontSize: 9,
    opacity: 0.8,
  },
  modeRow: {
    flexDirection: "row",
    gap: spacing.sm,
    paddingHorizontal: spacing.lg,
    marginTop: spacing.md,
  },
  modeBtn: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.sm,
    borderWidth: 1,
    borderColor: colors.brand,
    backgroundColor: "rgba(0,240,255,0.06)",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
    minHeight: 56,
    flex: 1,
  },
  modeBtnTitleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    flexWrap: "wrap",
  },
  modeBtnText: { color: colors.brand, fontSize: fonts.size.sm, fontWeight: "500", letterSpacing: 1 },
  experimentalTag: {
    color: colors.warning,
    fontSize: 9,
    letterSpacing: 1.4,
    fontWeight: "500",
    borderWidth: 1,
    borderColor: colors.warning,
    paddingHorizontal: 6,
    paddingVertical: 1,
    borderRadius: 3,
  },
  modeBtnHint: {
    color: colors.onSurfaceSecondary,
    fontSize: 11,
    marginTop: 4,
    lineHeight: 16,
  },
  modeBtnGhost: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.md,
    minHeight: 44,
    justifyContent: "center",
  },
  modeBtnGhostText: { color: colors.onSurfaceSecondary, fontSize: fonts.size.sm, letterSpacing: 1 },
  banner: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    marginHorizontal: spacing.lg,
    marginTop: spacing.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.warning + "59",
    backgroundColor: colors.warning + "14",
  },
  bannerText: { color: colors.warning, fontSize: fonts.size.sm, letterSpacing: 0.5 },
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
  actionBtnPrimary: { backgroundColor: colors.brand, borderColor: colors.brand },
  actionBtnActive: { borderColor: colors.warning, backgroundColor: "rgba(210,153,34,0.08)" },
  actionBtnText: { color: colors.onSurface, fontSize: 11, letterSpacing: 1.2, fontWeight: "500" },
});

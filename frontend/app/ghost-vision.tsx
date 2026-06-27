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
import PerceptionGraphPanel from "@/src/components/ghost-vision/PerceptionGraphPanel";
import { boundsAround } from "@/src/components/ghost-vision/projection";
import type { Hazard } from "@/src/types/sentinel";
import { api } from "@/src/api/sentinel";
import { buildLiveObservation } from "@/src/utils/ghostVisionLive";

// process.env.EXPO_PUBLIC_MAP_STYLE_URL is reserved for the native MapLibre adapter
// that ships in the Android development build. Web preview always uses the SVG WorldMap.

export default function GhostVisionScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { width } = useWindowDimensions();

  const loc = useSentinelLocation();
  const { worldModel, status, source, loading, error, refetch, confirm, report } =
    useGhostVisionData({
      enabled: loc.mode === "live",
      location: loc.location,
      headingDegrees: loc.headingDegrees,
    });
  const isFocused = useIsFocused();

  const [activeHazardId, setActiveHazardId] = useState<string | null>(null);
  const [cardExpanded, setCardExpanded] = useState(false);
  const [muted, setMuted] = useState(false);
  const [actionFlash, setActionFlash] = useState<string | null>(null);
  const spokenForId = useRef<string | null>(null);
  const actionFlashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [selectedLanguage, setSelectedLanguage] = useState<"en" | "hi" | "hinglish">("en");
  const [role, setRole] = useState<"approaching" | "observer">("approaching");
  const [graphRefreshKey, setGraphRefreshKey] = useState(0);

  const showActionFlash = useCallback((message: string | null, durationMs?: number) => {
    if (actionFlashTimer.current) {
      clearTimeout(actionFlashTimer.current);
      actionFlashTimer.current = null;
    }
    setActionFlash(message);
    if (message && durationMs) {
      actionFlashTimer.current = setTimeout(() => {
        setActionFlash(null);
        actionFlashTimer.current = null;
      }, durationMs);
    }
  }, []);

  useEffect(() => {
    return () => {
      if (actionFlashTimer.current) {
        clearTimeout(actionFlashTimer.current);
      }
    };
  }, []);

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
    if (!isFocused) {
      spokenForId.current = null;
      return;
    }
    if (!active || muted) return;
    if (spokenForId.current === active.id) return;
    if (active.routeRelevance !== "high" && active.routeRelevance !== "medium") return;
    spokenForId.current = active.id;
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning).catch(() => {});
    
    const text = (active as any).warnings?.[selectedLanguage] || 
      `${active.label}. Approximately ${active.distanceMeters} metres ahead. ${active.recommendedAction}.`;
    
    Speech.speak(text, { rate: 0.95, pitch: 1.0 });
  }, [active, muted, isFocused, selectedLanguage]);

  // Pause animations / TTS when screen loses focus.
  // Pause speech (and animations via the `paused` prop below) whenever the screen
  // loses focus. This is the single source of truth for animation pausing.
  useEffect(() => {
    if (!isFocused) {
      Speech.stop();
    }
  }, [isFocused]);

  const onSubmitObservation = useCallback(async () => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    showActionFlash("Sharing observation…");
    try {
      const obs =
        loc.mode === "live" && loc.location
          ? buildLiveObservation({
              location: loc.location,
              headingDegrees: loc.headingDegrees,
            })
          : {
              id: "obs-demo-stationary-001",
              type: "stationary_vehicle",
              label: "Stationary Vehicle Ahead",
              location: {
                latitude: 12.9452,
                longitude: 80.1506
              },
              polygon: [
                { latitude: 12.9451, longitude: 80.1505 },
                { latitude: 12.9451, longitude: 80.1507 },
                { latitude: 12.9453, longitude: 80.1507 },
                { latitude: 12.9453, longitude: 80.1505 }
              ],
              sourceVehicleId: "v-1",
              vehicleLabel: "Sentinel-A8"
            };
      await api.submitObservation(obs);
      await refetch(true);
      setGraphRefreshKey((k) => k + 1);

      showActionFlash(
        loc.mode === "live"
          ? "Live hazard shared with approaching vehicles"
          : "Observation shared with approaching vehicles",
        2200
      );
    } catch (err: any) {
      console.error("Failed to submit observation:", err);
      showActionFlash("Submission Failed", 1800);
    }
  }, [loc.headingDegrees, loc.location, loc.mode, refetch, showActionFlash]);

  const onResetDemo = useCallback(async () => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    showActionFlash("Resetting Demo");
    try {
      await api.resetDemo();
      showActionFlash("Demo reset", 1800);
      await refetch(true);
      setActiveHazardId(null);
      setGraphRefreshKey((k) => k + 1);
    } catch (err) {
      console.error("Failed to reset demo:", err);
      showActionFlash("Reset Failed", 1800);
    }
  }, [refetch, showActionFlash]);

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
    showActionFlash("Hazard confirmed", 1800);
    await confirm(active.id);
  }, [active, confirm, showActionFlash]);

  const onReport = useCallback(async () => {
    if (!active) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    showActionFlash("Report submitted", 1800);
    await report(active.id);
  }, [active, report, showActionFlash]);

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

  const boundsOverride = useMemo(
    () => (loc.mode === "live" && loc.location ? boundsAround(loc.location, 280) : undefined),
    [loc.mode, loc.location]
  );

  const egoOverride = useMemo(() => {
  if (loc.mode !== "live" || !loc.location) {
    return undefined;
  }

  return {
    location: loc.location,
    headingDegrees:
      typeof loc.headingDegrees === "number"
        ? loc.headingDegrees
        : 0,
  };
  }, [
    loc.mode,
    loc.location,
    loc.headingDegrees,
  ]);

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
  const displayedSpeedKmh = isLiveGeo
  ? Math.round(loc.speedKmh)
  : status?.speed_kmh ?? worldModel.ego.speedKmh;
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
            <Text style={styles.speedTopNum}>{displayedSpeedKmh}</Text>
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
              egoOverride={egoOverride}
              boundsOverride={boundsOverride}
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
                  ? "LIVE GPS · SHARED HAZARD LAYER"
                  : "DEMO SCENARIO · SIMULATED TELEMETRY"}
              </Text>
            </View>

            {/* Attribution */}
            <Text style={styles.attribution} testID="map-attribution">
              Synthetic Sentinel context; not real map data
            </Text>

            <MapLegend />
          </View>

          {/* === Two-Vehicle Demo Control Panel === */}
          <View style={styles.demoPanel} testID="demo-control-panel">
            <View style={styles.demoPanelHeader}>
              <MaterialCommunityIcons name="car-multiple" size={16} color={colors.brand} />
              <Text style={styles.demoPanelTitle}>TWO-VEHICLE DEMO CONTROLS</Text>
            </View>
            
            <View style={styles.roleSwitcher}>
              <Pressable
                onPress={() => {
                  Haptics.selectionAsync().catch(() => {});
                  setRole("approaching");
                }}
                style={[styles.roleBtn, role === "approaching" && styles.roleBtnActive]}
                testID="role-approaching-button"
                android_ripple={{ color: "#003844" }}
              >
                <Text style={[styles.roleBtnText, role === "approaching" && styles.roleBtnTextActive]}>
                  Approaching (Ego)
                </Text>
              </Pressable>
              <Pressable
                onPress={() => {
                  Haptics.selectionAsync().catch(() => {});
                  setRole("observer");
                }}
                style={[styles.roleBtn, role === "observer" && styles.roleBtnActive]}
                testID="role-observer-button"
                android_ripple={{ color: "#003844" }}
              >
                <Text style={[styles.roleBtnText, role === "observer" && styles.roleBtnTextActive]}>
                  Observer Vehicle
                </Text>
              </Pressable>
            </View>

            {role === "observer" ? (
              <View style={styles.observerControls}>
                <Pressable
                  onPress={onSubmitObservation}
                  style={({ pressed }) => [styles.submitObsBtn, pressed && { opacity: 0.85 }]}
                  testID="submit-observation-button"
                  android_ripple={{ color: "#000" }}
                >
                  <MaterialCommunityIcons name="plus-circle-outline" size={18} color="#000" />
                  <Text style={styles.submitObsBtnText}>Submit Demo Observation</Text>
                </Pressable>
                <Text style={styles.observerHint}>
                  Observer vehicle Sentinel-A8 observes a hazard and submits it to the graph model.
                </Text>
              </View>
            ) : (
              <Text style={styles.approachingHint}>
                Approaching vehicle queries relevant hazards and shows the observer hazard as a hidden Ghost object.
              </Text>
            )}

            <Pressable
              onPress={onResetDemo}
              style={({ pressed }) => [styles.resetDemoBtn, pressed && { opacity: 0.85 }]}
              testID="reset-demo-button"
              android_ripple={{ color: "#003844" }}
            >
              <MaterialCommunityIcons name="refresh" size={14} color={colors.warning} />
              <Text style={styles.resetDemoBtnText}>Reset Demo Data</Text>
            </Pressable>
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
                        : "LIVE GPS"}
                    </Text>
                    <Text style={styles.experimentalTag}>EXPERIMENTAL</Text>
                  </View>
                  <Text style={styles.modeBtnHint}>
                    Uses live device position with a shared hazard layer and synthetic Sentinel context.
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

          {/* === Perception Provenance Panel === */}
          {active && (
            <PerceptionGraphPanel
              hazardId={active.id}
              refreshKey={graphRefreshKey}
            />
          )}
        </ScrollView>

        {/* === Language Selector Row === */}
        <View style={styles.langSelectorRow} testID="language-selector-row">
          <Text style={styles.langSelectorLabel}>ALERT LANGUAGE:</Text>
          <View style={styles.langButtonsGroup}>
            {(["en", "hi", "hinglish"] as const).map((lang) => (
              <Pressable
                key={lang}
                onPress={() => {
                  Haptics.selectionAsync().catch(() => {});
                  setSelectedLanguage(lang);
                  spokenForId.current = null; // re-trigger voice with new language
                }}
                style={[
                  styles.langBtn,
                  selectedLanguage === lang && styles.langBtnActive
                ]}
                testID={`lang-button-${lang}`}
              >
                <Text
                  style={[
                    styles.langBtnText,
                    selectedLanguage === lang && styles.langBtnTextActive
                  ]}
                >
                  {lang.toUpperCase()}
                </Text>
              </Pressable>
            ))}
          </View>
        </View>

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
        {isLive ? "LIVE GPS" : telemetry === "demo" ? "DEMO" : source === "backend" ? "CACHED" : "DEMO"}
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
  demoPanel: {
    marginHorizontal: spacing.lg,
    marginTop: spacing.md,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.md,
  },
  demoPanelHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    marginBottom: spacing.sm,
  },
  demoPanelTitle: {
    color: colors.brand,
    fontSize: 11,
    fontWeight: "600",
    letterSpacing: 1.2,
  },
  roleSwitcher: {
    flexDirection: "row",
    gap: spacing.sm,
    marginBottom: spacing.sm,
  },
  roleBtn: {
    flex: 1,
    paddingVertical: 10,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    alignItems: "center",
  },
  roleBtnActive: {
    borderColor: colors.brand,
    backgroundColor: "rgba(0,240,255,0.04)",
  },
  roleBtnText: {
    color: colors.onSurfaceSecondary,
    fontSize: 11,
    fontWeight: "500",
  },
  roleBtnTextActive: {
    color: colors.brand,
    fontWeight: "600",
  },
  observerControls: {
    marginTop: spacing.xs,
    marginBottom: spacing.sm,
  },
  submitObsBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    backgroundColor: colors.brand,
    paddingVertical: 12,
    borderRadius: radius.sm,
  },
  submitObsBtnText: {
    color: "#000",
    fontWeight: "600",
    fontSize: 12,
    letterSpacing: 0.5,
  },
  observerHint: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    marginTop: 6,
    lineHeight: 14,
  },
  approachingHint: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    marginBottom: spacing.sm,
    lineHeight: 14,
  },
  resetDemoBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 4,
    borderWidth: 1,
    borderColor: colors.warning + "59",
    paddingVertical: 8,
    borderRadius: radius.sm,
    backgroundColor: "rgba(210,153,34,0.04)",
  },
  resetDemoBtnText: {
    color: colors.warning,
    fontSize: 11,
    fontWeight: "500",
    letterSpacing: 0.5,
  },
  langSelectorRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    borderTopWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  langSelectorLabel: {
    color: colors.onSurfaceSecondary,
    fontSize: 10,
    fontWeight: "600",
    letterSpacing: 1.2,
  },
  langButtonsGroup: {
    flexDirection: "row",
    gap: spacing.sm,
  },
  langBtn: {
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
    borderRadius: radius.sm,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
  },
  langBtnActive: {
    backgroundColor: "rgba(0,240,255,0.08)",
    borderColor: colors.brand,
  },
  langBtnText: {
    color: colors.onSurfaceSecondary,
    fontSize: 10,
    fontWeight: "600",
    letterSpacing: 1,
  },
  langBtnTextActive: {
    color: colors.brand,
  },
});

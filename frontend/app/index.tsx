import React, { useEffect, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  StatusBar,
  ActivityIndicator,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { useRouter } from "expo-router";
import { colors, spacing, radius, fonts } from "@/src/theme";
import { api, type SentinelStatus } from "@/src/api/sentinel";

export default function DriveHUD() {
  const router = useRouter();
  const [status, setStatus] = useState<SentinelStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    api
      .status()
      .then((s) => {
        if (alive) setStatus(s);
      })
      .catch(() => {})
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const onEngage = () => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy).catch(() => {});
    router.push("/ghost-vision");
  };

  return (
    <View style={styles.root} testID="drive-hud-screen">
      <StatusBar barStyle="light-content" />
      <LinearGradient
        colors={["#00131A", "#090A0C", "#000"]}
        style={StyleSheet.absoluteFill}
      />
      <SafeAreaView style={{ flex: 1 }} edges={["top", "bottom"]}>
        {/* Top brand strip */}
        <View style={styles.topRow}>
          <View style={styles.brandRow}>
            <MaterialCommunityIcons name="shield-check" size={18} color={colors.brand} />
            <Text style={styles.brandText}>SENTINEL</Text>
          </View>
          <View style={styles.statusPill} testID="connection-status">
            <View
              style={[
                styles.dot,
                { backgroundColor: status?.connected ? colors.success : colors.error },
              ]}
            />
            <Text style={styles.statusPillText}>
              {status?.connected ? "Connected" : "Offline"}
            </Text>
          </View>
        </View>

        {/* Hero metrics */}
        <View style={styles.hero}>
          <Text style={styles.label}>CURRENT SPEED</Text>
          <View style={styles.speedRow}>
            <Text style={styles.speedNum} testID="drive-speed">
              {status?.speed_kmh ?? 0}
            </Text>
            <Text style={styles.speedUnit}>km/h</Text>
          </View>
          <View style={styles.roadRow}>
            <MaterialCommunityIcons name="road-variant" size={16} color={colors.onSurfaceSecondary} />
            <Text style={styles.road}>{status?.road_name ?? "Locating…"}</Text>
          </View>

          {/* Telemetry grid */}
          <View style={styles.telGrid}>
            <Telemetry icon="crosshairs-gps" label="GPS" value={status?.gps_locked ? "Locked" : "Searching"} />
            <Telemetry icon="signal-cellular-3" label="Network" value={status?.network ?? "—"} />
            <Telemetry icon="car-multiple" label="Nearby" value={`${status?.sentinel_vehicles_nearby ?? 0} units`} />
            <Telemetry icon="compass" label="Heading" value={status?.heading ?? "—"} />
          </View>
        </View>

        {/* Engage CTA */}
        <View style={styles.ctaWrap}>
          <Text style={styles.ctaHint}>Hidden hazard detected ahead</Text>
          <Pressable
            onPress={onEngage}
            style={({ pressed }) => [styles.cta, pressed && { opacity: 0.85 }]}
            testID="engage-ghost-vision-button"
            android_ripple={{ color: "#003844" }}
          >
            {loading ? (
              <ActivityIndicator color={colors.onBrandPrimary as any} />
            ) : (
              <>
                <MaterialCommunityIcons name="radar" size={22} color="#000" />
                <Text style={styles.ctaText}>ENGAGE GHOST VISION</Text>
              </>
            )}
          </Pressable>
          <Text style={styles.footer}>Tap to view tactical road perception</Text>
        </View>
      </SafeAreaView>
    </View>
  );
}

function Telemetry({
  icon,
  label,
  value,
}: {
  icon: keyof typeof MaterialCommunityIcons.glyphMap;
  label: string;
  value: string;
}) {
  return (
    <View style={styles.tel}>
      <MaterialCommunityIcons name={icon} size={16} color={colors.brand} />
      <View style={{ marginLeft: spacing.sm }}>
        <Text style={styles.telLabel}>{label}</Text>
        <Text style={styles.telValue}>{value}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  topRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
  },
  brandRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  brandText: {
    color: colors.onSurface,
    fontSize: fonts.size.base,
    letterSpacing: 4,
    fontWeight: "500",
  },
  statusPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: colors.surfaceSecondary,
    borderColor: colors.border,
    borderWidth: 1,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    borderRadius: radius.pill,
  },
  dot: { width: 8, height: 8, borderRadius: 4 },
  statusPillText: {
    color: colors.onSurfaceSecondary,
    fontSize: fonts.size.sm,
    letterSpacing: 1.2,
  },
  hero: { flex: 1, justifyContent: "center", paddingHorizontal: spacing.xl },
  label: {
    color: colors.onSurfaceTertiary,
    fontSize: fonts.size.sm,
    letterSpacing: 3,
  },
  speedRow: { flexDirection: "row", alignItems: "flex-end", marginTop: spacing.xs },
  speedNum: {
    color: colors.onSurface,
    fontSize: 120,
    fontWeight: "500",
    lineHeight: 124,
    letterSpacing: -2,
  },
  speedUnit: {
    color: colors.brand,
    fontSize: fonts.size.xl,
    marginLeft: spacing.sm,
    marginBottom: spacing.md,
    letterSpacing: 1,
  },
  roadRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, marginTop: spacing.xs },
  road: { color: colors.onSurfaceSecondary, fontSize: fonts.size.lg },
  telGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    marginTop: spacing.xl,
    gap: spacing.md,
  },
  tel: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surfaceSecondary,
    borderColor: colors.border,
    borderWidth: 1,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.lg,
    borderRadius: radius.md,
    width: "47%",
  },
  telLabel: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    letterSpacing: 1.5,
  },
  telValue: {
    color: colors.onSurface,
    fontSize: fonts.size.lg,
    fontWeight: "500",
  },
  ctaWrap: { padding: spacing.xl, alignItems: "center" },
  ctaHint: {
    color: colors.warning,
    fontSize: fonts.size.sm,
    letterSpacing: 2,
    marginBottom: spacing.md,
    textTransform: "uppercase",
  },
  cta: {
    width: "100%",
    backgroundColor: colors.brand,
    paddingVertical: spacing.lg,
    borderRadius: radius.md,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    minHeight: 56,
  },
  ctaText: {
    color: "#000",
    fontSize: fonts.size.lg,
    fontWeight: "500",
    letterSpacing: 2,
  },
  footer: {
    color: colors.onSurfaceTertiary,
    fontSize: fonts.size.sm,
    marginTop: spacing.md,
  },
});

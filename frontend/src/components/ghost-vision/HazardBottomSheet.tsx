// Collapsible hazard detail panel. Tap header to expand/collapse — no external bottom-sheet dep
// needed for v1 to keep the implementation Expo-Go-compatible and lightweight.

import React from "react";
import { View, Text, StyleSheet, Pressable } from "react-native";
import Animated, { FadeInDown } from "react-native-reanimated";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, spacing, radius, fonts } from "@/src/theme";
import type { Hazard } from "@/src/types/sentinel";

function riskTint(risk: Hazard["risk"]) {
  return risk === "high" ? colors.error : risk === "medium" ? colors.warning : colors.success;
}

function sourceLabel(h: Hazard): string {
  if (h.sourceType === "shared_vehicle") return "Shared by Sentinel network";
  if (h.sourceType === "local_sensor") return "Local sensor";
  return "Demo data";
}

function visibilityLabel(v: Hazard["visibilityState"]): string {
  if (v === "hidden") return "HIDDEN · Beyond line of sight";
  if (v === "uncertain") return "UNCERTAIN";
  return "VISIBLE";
}

export default function HazardBottomSheet({
  hazard,
  expanded,
  onToggle,
}: {
  hazard: Hazard;
  expanded: boolean;
  onToggle: () => void;
}) {
  const tint = riskTint(hazard.risk);
  return (
    <Animated.View
      entering={FadeInDown.duration(350)}
      style={styles.sheet}
      testID="hazard-info-card"
    >
      <Pressable
        onPress={onToggle}
        style={styles.header}
        testID="hazard-card-toggle"
        android_ripple={{ color: "#003844" }}
      >
        <View style={styles.handle} />
        <View style={styles.row}>
          <View style={[styles.riskTag, { borderColor: tint }]}>
            <View style={[styles.riskDot, { backgroundColor: tint }]} />
            <Text style={[styles.riskTagText, { color: tint }]}>
              {hazard.risk.toUpperCase()} RISK
            </Text>
          </View>
          <Text style={styles.age}>{hazard.observedSecondsAgo}s ago</Text>
        </View>
        <View style={styles.titleRow}>
          <MaterialCommunityIcons name="car-brake-alert" size={22} color={tint} />
          <Text style={styles.title} testID="hazard-title">
            {hazard.label}
          </Text>
          <MaterialCommunityIcons
            name={expanded ? "chevron-down" : "chevron-up"}
            size={20}
            color={colors.onSurfaceSecondary}
          />
        </View>
        <Text style={styles.visibility}>{visibilityLabel(hazard.visibilityState)}</Text>
      </Pressable>

      <View style={styles.metricsRow}>
        <Metric label="DISTANCE" value={`≈${hazard.distanceMeters}`} unit="m" />
        <View style={styles.vline} />
        <Metric label="CONFIDENCE" value={`${hazard.confidence}`} unit="%" />
        <View style={styles.vline} />
        <Metric label="SOURCES" value={`${hazard.sources}`} unit="veh" />
      </View>

      {expanded && (
        <View style={styles.expanded}>
          <Detail icon="arrow-up-bold" text={hazard.direction} />
          <Detail icon="account-multiple-check" text={sourceLabel(hazard)} />
          <Detail
            icon="target"
            text={`Route relevance · ${hazard.routeRelevance.toUpperCase()}`}
          />
          {hazard.confirmed > 0 && (
            <Detail icon="check-all" text={`Confirmed ${hazard.confirmed} time${hazard.confirmed > 1 ? "s" : ""}`} />
          )}
        </View>
      )}

      <View style={[styles.advice, { borderColor: tint + "59", backgroundColor: tint + "14" }]}
        testID="recommended-action"
      >
        <MaterialCommunityIcons name="alert-decagram" size={16} color={tint} />
        <Text style={[styles.adviceText, { color: tint }]}>
          RECOMMENDED · {hazard.recommendedAction.toUpperCase()}
        </Text>
      </View>
    </Animated.View>
  );
}

function Metric({ label, value, unit }: { label: string; value: string; unit?: string }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricLabel}>{label}</Text>
      <View style={{ flexDirection: "row", alignItems: "flex-end" }}>
        <Text style={styles.metricValue}>{value}</Text>
        {unit && <Text style={styles.metricUnit}>{unit}</Text>}
      </View>
    </View>
  );
}

function Detail({
  icon,
  text,
}: {
  icon: keyof typeof MaterialCommunityIcons.glyphMap;
  text: string;
}) {
  return (
    <View style={styles.detailRow}>
      <MaterialCommunityIcons name={icon} size={14} color={colors.onSurfaceSecondary} />
      <Text style={styles.detailText}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  sheet: {
    marginHorizontal: spacing.lg,
    marginTop: spacing.md,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    padding: spacing.lg,
  },
  header: {},
  handle: {
    alignSelf: "center",
    width: 36,
    height: 3,
    borderRadius: 2,
    backgroundColor: colors.border,
    marginBottom: spacing.sm,
  },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
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
  riskTagText: { fontSize: 10, letterSpacing: 1.6, fontWeight: "500" },
  age: { color: colors.onSurfaceTertiary, fontSize: 10, letterSpacing: 1.2 },
  titleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    marginTop: spacing.sm,
  },
  title: { color: colors.onSurface, fontSize: fonts.size.xl, fontWeight: "500", flex: 1 },
  visibility: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    letterSpacing: 1.4,
    marginTop: 4,
  },
  metricsRow: {
    flexDirection: "row",
    marginTop: spacing.md,
    backgroundColor: colors.surfaceTertiary,
    borderRadius: radius.md,
    paddingVertical: spacing.md,
  },
  metric: { flex: 1, alignItems: "center" },
  metricLabel: { color: colors.onSurfaceTertiary, fontSize: 10, letterSpacing: 1.5, marginBottom: 4 },
  metricValue: { color: colors.onSurface, fontSize: fonts.size.xxl, fontWeight: "500", lineHeight: 28 },
  metricUnit: { color: colors.onSurfaceSecondary, fontSize: 11, marginLeft: 2, marginBottom: 4 },
  vline: { width: 1, backgroundColor: colors.border, marginVertical: spacing.xs },
  expanded: { marginTop: spacing.md, gap: spacing.sm },
  detailRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  detailText: { color: colors.onSurfaceSecondary, fontSize: fonts.size.sm },
  advice: {
    marginTop: spacing.md,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    borderWidth: 1,
    borderRadius: radius.md,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
  },
  adviceText: { fontSize: fonts.size.sm, fontWeight: "500", letterSpacing: 1.5 },
});

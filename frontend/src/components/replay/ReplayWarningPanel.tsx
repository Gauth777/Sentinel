import React from "react";
import { View, Text, StyleSheet } from "react-native";
import { colors, spacing, radius, fonts } from "@/src/theme";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import type { DemoReplayInferenceResponse } from "@/src/types/demoReplay";

type ReplayWarningPanelProps = {
  inference: DemoReplayInferenceResponse;
};

function getRiskStyle(risk: "high" | "medium" | "low") {
  if (risk === "high") {
    return {
      bg: "rgba(248, 81, 73, 0.1)",
      border: colors.error,
      text: colors.error,
      icon: "alert-decagram",
      title: "HIGH COLLISION RISK ALERT",
    };
  }
  if (risk === "medium") {
    return {
      bg: "rgba(210, 153, 34, 0.1)",
      border: colors.warning,
      text: colors.warning,
      icon: "alert",
      title: "POTENTIAL HAZARD WARNING",
    };
  }
  return {
    bg: "rgba(46, 160, 67, 0.1)",
    border: colors.success,
    text: colors.success,
    icon: "checkbox-marked-circle-outline",
    title: "NORMAL ROAD CONDITION",
  };
}

export default function ReplayWarningPanel({ inference }: ReplayWarningPanelProps) {
  const { prediction, runtimeHazard, activation } = inference;
  const riskMeta = getRiskStyle(prediction.anticipatedRisk);

  const hasHazard = prediction.hazardPresence === "yes";

  return (
    <View style={styles.container} testID="demo-replay-warning">
      {/* Risk Alert Header */}
      <View
        style={[
          styles.alertBanner,
          { backgroundColor: riskMeta.bg, borderColor: riskMeta.border },
        ]}
      >
        <MaterialCommunityIcons name={riskMeta.icon as any} size={20} color={riskMeta.text} />
        <Text style={[styles.alertTitle, { color: riskMeta.text }]}>{riskMeta.title}</Text>
      </View>

      {/* Hazard & Recommendation info */}
      <View style={styles.content}>
        {hasHazard && runtimeHazard ? (
          <View style={styles.hazardRow}>
            <View style={styles.infoBox}>
              <Text style={styles.infoTitle}>DETECTED HAZARD</Text>
              <Text style={styles.hazardText}>{runtimeHazard.hazardDescription}</Text>
            </View>
          </View>
        ) : (
          <View style={styles.hazardRow}>
            <View style={styles.infoBox}>
              <Text style={styles.infoTitle}>ROAD STATUS</Text>
              <Text style={styles.hazardText}>Clear path. No hazards reported by VLM.</Text>
            </View>
          </View>
        )}

        <View style={styles.actionBox}>
          <Text style={styles.infoTitle}>TACTICAL ACTION</Text>
          <View style={styles.actionBadge}>
            <MaterialCommunityIcons name="steering" size={16} color={colors.brand} />
            <Text style={styles.actionText}>
              {prediction.recommendedAction === "slow_down"
                ? "SLOW DOWN"
                : prediction.recommendedAction === "prepare_to_stop"
                ? "PREPARE TO STOP"
                : prediction.recommendedAction === "increase_attention"
                ? "INCREASE ATTENTION"
                : prediction.recommendedAction.replace(/_/g, " ").toUpperCase()}
            </Text>
          </View>
        </View>

        {/* Integration Status Panel */}
        <View style={styles.graphPanel}>
          <Text style={styles.graphPanelTitle}>SENTINEL GRAPH & COOPERATIVE FLOW</Text>

          <View style={styles.statusRow}>
            <View style={styles.statusItem}>
              <MaterialCommunityIcons
                name={activation.activated ? "check-circle" : "minus-circle"}
                size={14}
                color={activation.activated ? colors.success : colors.onSurfaceTertiary}
              />
              <Text style={styles.statusText}>
                {activation.activated
                  ? `Observation: ${activation.observationId}`
                  : `Inactive: ${activation.reason || "no hazard"}`}
              </Text>
            </View>

            <View style={styles.statusItem}>
              <MaterialCommunityIcons
                name={activation.hazardId ? "lan-connect" : "link-off"}
                size={14}
                color={activation.hazardId ? colors.brand : colors.onSurfaceTertiary}
              />
              <Text style={styles.statusText}>
                {activation.hazardId
                  ? `Provenance Sync: ${activation.hazardId}`
                  : "No provenance link"}
              </Text>
            </View>

            <View style={styles.statusItem}>
              <MaterialCommunityIcons
                name={activation.warningTextGenerated ? "check-circle" : "minus-circle"}
                size={14}
                color={activation.warningTextGenerated ? colors.success : colors.onSurfaceTertiary}
              />
              <Text style={styles.statusText}>
                {activation.warningTextGenerated ? "Warning text prepared" : "No warnings generated"}
              </Text>
            </View>

            <View style={styles.statusItem}>
              <MaterialCommunityIcons
                name={activation.warningEventCreated ? "bell-ring" : "bell-off"}
                size={14}
                color={activation.warningEventCreated ? colors.warning : colors.onSurfaceTertiary}
              />
              <Text style={styles.statusText}>
                {activation.warningEventCreated
                  ? "Cooperative alert dispatched"
                  : "No approaching vehicle alert created"}
              </Text>
            </View>
          </View>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
    overflow: "hidden",
  },
  alertBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    padding: spacing.md,
    borderBottomWidth: 1,
  },
  alertTitle: {
    fontSize: fonts.size.base,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 0.5,
  },
  content: {
    padding: spacing.md,
    gap: spacing.md,
  },
  hazardRow: {
    flexDirection: "row",
    gap: spacing.md,
  },
  infoBox: {
    flex: 1,
    gap: 4,
  },
  infoTitle: {
    color: colors.onSurfaceTertiary,
    fontSize: fonts.size.sm - 3,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 0.5,
  },
  hazardText: {
    color: colors.onSurface,
    fontSize: fonts.size.base,
    fontFamily: fonts.family,
    fontWeight: "500",
    lineHeight: 20,
  },
  actionBox: {
    gap: spacing.xs,
  },
  actionBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: "rgba(0, 240, 255, 0.05)",
    borderWidth: 1,
    borderColor: "rgba(0, 240, 255, 0.2)",
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    alignSelf: "flex-start",
  },
  actionText: {
    color: colors.brand,
    fontSize: fonts.size.base,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 0.5,
  },
  graphPanel: {
    backgroundColor: colors.surfaceTertiary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.sm,
    gap: spacing.sm,
  },
  graphPanelTitle: {
    color: colors.onSurfaceSecondary,
    fontSize: fonts.size.sm - 3,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 0.5,
  },
  statusRow: {
    gap: 4,
  },
  statusItem: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
  },
  statusText: {
    color: colors.onSurfaceSecondary,
    fontSize: fonts.size.sm - 1,
    fontFamily: fonts.family,
  },
});

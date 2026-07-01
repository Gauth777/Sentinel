import React from "react";
import { View, Text, StyleSheet } from "react-native";
import { colors, spacing, radius, fonts } from "@/src/theme";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import type { DemoReplayInferenceResponse } from "@/src/types/demoReplay";

type ReplayPredictionPanelProps = {
  inference: DemoReplayInferenceResponse;
};

function formatLabel(val: string): string {
  return val.replace(/_/g, " ").toUpperCase();
}

function getRiskColor(risk: "high" | "medium" | "low"): string {
  if (risk === "high") return colors.error;
  if (risk === "medium") return colors.warning;
  return colors.success;
}

export default function ReplayPredictionPanel({ inference }: ReplayPredictionPanelProps) {
  const { prediction, model, inferenceMode, latencyMs } = inference;

  return (
    <View style={styles.container} testID="demo-replay-prediction">
      {/* Header Info */}
      <View style={styles.header}>
        <View style={styles.metaInfo}>
          <Text style={styles.modelText}>{model}</Text>
          <Text style={styles.latencyText}>
            {inferenceMode === "live_qwen" ? `Latency: ${latencyMs} ms` : "Cached output"}
          </Text>
        </View>

        {/* Badge */}
        <View
          style={[
            styles.badge,
            {
              backgroundColor:
                inferenceMode === "live_qwen"
                  ? "rgba(46, 160, 67, 0.15)"
                  : "rgba(210, 153, 34, 0.15)",
              borderColor:
                inferenceMode === "live_qwen" ? colors.success : colors.warning,
            },
          ]}
        >
          <MaterialCommunityIcons
            name={inferenceMode === "live_qwen" ? "flash" : "cached"}
            size={12}
            color={inferenceMode === "live_qwen" ? colors.success : colors.warning}
          />
          <Text
            style={[
              styles.badgeText,
              { color: inferenceMode === "live_qwen" ? colors.success : colors.warning },
            ]}
          >
            {inferenceMode === "live_qwen" ? "LIVE QWEN" : "CACHED QWEN FALLBACK"}
          </Text>
        </View>
      </View>

      {/* Grid of structured labels */}
      <View style={styles.grid}>
        <View style={styles.gridCol}>
          <View style={styles.gridItem}>
            <Text style={styles.labelTitle}>ROAD TYPE</Text>
            <Text style={styles.labelValue}>{formatLabel(prediction.roadType)}</Text>
          </View>
          <View style={styles.gridItem}>
            <Text style={styles.labelTitle}>TRAFFIC DENSITY</Text>
            <Text style={styles.labelValue}>{formatLabel(prediction.trafficDensity)}</Text>
          </View>
          <View style={styles.gridItem}>
            <Text style={styles.labelTitle}>ROAD COMPLEXITY</Text>
            <Text style={styles.labelValue}>{formatLabel(prediction.roadComplexity)}</Text>
          </View>
        </View>

        <View style={styles.gridCol}>
          <View style={styles.gridItem}>
            <Text style={styles.labelTitle}>HAZARD DETECTED</Text>
            <Text
              style={[
                styles.labelValue,
                {
                  color:
                    prediction.hazardPresence === "yes" ? colors.error : colors.onSurface,
                  fontWeight: "bold",
                },
              ]}
            >
              {prediction.hazardPresence.toUpperCase()}
            </Text>
          </View>
          <View style={styles.gridItem}>
            <Text style={styles.labelTitle}>ANTICIPATED RISK</Text>
            <Text
              style={[
                styles.labelValue,
                {
                  color: getRiskColor(prediction.anticipatedRisk),
                  fontWeight: "bold",
                },
              ]}
            >
              {prediction.anticipatedRisk.toUpperCase()} RISK
            </Text>
          </View>
          <View style={styles.gridItem}>
            <Text style={styles.labelTitle}>RECOMMENDED ACTION</Text>
            <Text
              style={[
                styles.labelValue,
                {
                  color:
                    prediction.recommendedAction === "maintain_speed"
                      ? colors.onSurface
                      : colors.brand,
                  fontWeight: "bold",
                },
              ]}
            >
              {formatLabel(prediction.recommendedAction)}
            </Text>
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
    padding: spacing.md,
    gap: spacing.md,
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
    paddingBottom: spacing.sm,
  },
  metaInfo: {
    gap: 2,
  },
  modelText: {
    color: colors.onSurface,
    fontSize: fonts.size.sm,
    fontFamily: fonts.family,
    fontWeight: "bold",
  },
  latencyText: {
    color: colors.onSurfaceTertiary,
    fontSize: fonts.size.sm - 2,
    fontFamily: fonts.family,
  },
  badge: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.xs,
    paddingHorizontal: spacing.sm,
    paddingVertical: 4,
    borderRadius: radius.pill,
    borderWidth: 1,
  },
  badgeText: {
    fontSize: fonts.size.sm - 2,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 0.5,
  },
  grid: {
    flexDirection: "row",
    gap: spacing.md,
  },
  gridCol: {
    flex: 1,
    gap: spacing.sm,
  },
  gridItem: {
    backgroundColor: colors.surfaceTertiary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.sm,
    gap: 2,
  },
  labelTitle: {
    color: colors.onSurfaceTertiary,
    fontSize: fonts.size.sm - 3,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 0.5,
  },
  labelValue: {
    color: colors.onSurface,
    fontSize: fonts.size.sm,
    fontFamily: fonts.family,
    fontWeight: "500",
  },
});

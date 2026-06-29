import React from "react";
import { View, Text, Pressable, StyleSheet } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, spacing, radius, fonts } from "@/src/theme";
import type { TrainingSample } from "@/src/types/training";

const STATUS_COLORS: Record<string, string> = {
  pending: colors.warning,
  verified: colors.success,
  rejected: colors.error,
  confirmed: colors.success,
  corrected: colors.brandSecondary,
};

const INFERENCE_BADGE: Record<string, string> = {
  demo: "DEMO",
  live: "LIVE",
  remote: "REMOTE",
  local: "LOCAL",
  imported: "IMPORTED",
};

const INFERENCE_COLOR: Record<string, string> = {
  demo: colors.warning,
  live: colors.success,
  remote: colors.brand,
  local: colors.brandSecondary,
  imported: colors.onSurfaceSecondary,
};

type Props = {
  sample: TrainingSample;
  selected: boolean;
  onPress: () => void;
};

export default function TrainingSampleCard({ sample, selected, onPress }: Props) {
  const statusColor = STATUS_COLORS[sample.feedbackStatus] || colors.onSurfaceTertiary;
  const inferenceBadge = INFERENCE_BADGE[sample.model.inferenceMode] || sample.model.inferenceMode.toUpperCase();
  const inferenceColor = INFERENCE_COLOR[sample.model.inferenceMode] || colors.onSurfaceSecondary;
  const displayedLabels = sample.finalVerifiedLabels ?? sample.originalPrediction ?? sample.prediction;
  const statusText = `${sample.datasetStatus.toUpperCase()} · ${sample.feedbackStatus.toUpperCase()}`;

  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        styles.card,
        selected && styles.cardSelected,
        pressed && { opacity: 0.85 },
      ]}
      testID={`training-sample-card-${sample.sampleId}`}
      android_ripple={{ color: "#003844" }}
    >
      <View style={styles.headerRow}>
        <Text style={styles.sampleId} numberOfLines={1}>
          {sample.sampleId}
        </Text>
        <View style={[styles.badge, { borderColor: statusColor }]}>
          <Text style={[styles.badgeText, { color: statusColor }]}>
            {statusText}
          </Text>
        </View>
      </View>

      <View style={styles.metaRow}>
        <Text style={styles.metaText}>
          {new Date(sample.capturedAt).toLocaleString()}
        </Text>
        <View style={[styles.inferenceBadge, { borderColor: inferenceColor }]}>
          <Text style={[styles.inferenceBadgeText, { color: inferenceColor }]}>
            {inferenceBadge}
          </Text>
        </View>
      </View>

      <View style={styles.detailRow}>
        <MaterialCommunityIcons name="car" size={12} color={colors.onSurfaceTertiary} />
        <Text style={styles.detailText} numberOfLines={1}>
          {sample.sourceVehicleId} · {sample.context.roadName ?? "—"}
        </Text>
      </View>

      <View style={styles.labelsRow}>
        <LabelChip label={`Road: ${displayedLabels.roadType}`} />
        <LabelChip label={`Hazard: ${displayedLabels.hazardPresence}`} />
        <LabelChip label={`Risk: ${displayedLabels.anticipatedRisk}`} />
        <LabelChip label={`Action: ${displayedLabels.recommendedAction}`} />
      </View>

      {typeof displayedLabels.confidence === "number" && (
        <Text style={styles.confidence}>
          Confidence: {(displayedLabels.confidence * 100).toFixed(0)}%
        </Text>
      )}
    </Pressable>
  );
}

function LabelChip({ label }: { label: string }) {
  return (
    <View style={styles.chip}>
      <Text style={styles.chipText}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.md,
    marginHorizontal: spacing.lg,
    marginBottom: spacing.sm,
  },
  cardSelected: {
    borderColor: colors.brand,
    backgroundColor: "rgba(0,240,255,0.04)",
  },
  headerRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: spacing.xs,
  },
  sampleId: {
    color: colors.onSurface,
    fontSize: fonts.size.sm,
    fontWeight: "600",
    flex: 1,
    letterSpacing: 0.5,
  },
  badge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radius.sm,
    borderWidth: 1,
    backgroundColor: "rgba(0,0,0,0.2)",
  },
  badgeText: {
    fontSize: 9,
    fontWeight: "600",
    letterSpacing: 0.5,
    textTransform: "uppercase",
  },
  metaRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: spacing.xs,
  },
  metaText: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
  },
  inferenceBadge: {
    paddingHorizontal: 6,
    paddingVertical: 1,
    borderRadius: radius.sm,
    borderWidth: 1,
    backgroundColor: "rgba(0,0,0,0.2)",
  },
  inferenceBadgeText: {
    fontSize: 9,
    fontWeight: "600",
    letterSpacing: 0.5,
  },
  detailRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    marginBottom: spacing.xs,
  },
  detailText: {
    color: colors.onSurfaceSecondary,
    fontSize: 11,
    flex: 1,
  },
  labelsRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 4,
  },
  chip: {
    backgroundColor: colors.surfaceTertiary,
    paddingHorizontal: 6,
    paddingVertical: 3,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  chipText: {
    color: colors.onSurfaceSecondary,
    fontSize: 9,
    letterSpacing: 0.3,
  },
  confidence: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    marginTop: spacing.xs,
  },
});

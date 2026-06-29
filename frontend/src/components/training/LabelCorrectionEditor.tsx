import React, { useState, useCallback, useEffect } from "react";
import { View, Text, Pressable, StyleSheet } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, spacing, radius } from "@/src/theme";
import type {
  PredictionLabels,
  PartialPredictionLabels,
  RoadType,
  TrafficDensity,
  RoadComplexity,
  HazardPresence,
  AnticipatedRisk,
  RecommendedAction,
} from "@/src/types/training";

type Props = {
  original: PredictionLabels;
  currentFinal?: PredictionLabels | null;
  onSubmit: (corrections: PartialPredictionLabels) => void;
  onCancel: () => void;
  loading?: boolean;
};

const ROAD_TYPES: RoadType[] = ["urban_arterial", "residential", "highway", "junction"];
const TRAFFIC_DENSITIES: TrafficDensity[] = ["low", "medium", "high"];
const ROAD_COMPLEXITIES: RoadComplexity[] = ["simple", "moderate", "complex"];
const HAZARD_PRESENCES: HazardPresence[] = ["yes", "no"];
const ANTICIPATED_RISKS: AnticipatedRisk[] = ["low", "medium", "high"];
const RECOMMENDED_ACTIONS: RecommendedAction[] = [
  "slow_down",
  "maintain_speed",
  "increase_attention",
  "yield",
  "prepare_to_stop",
  "change_lane",
];

export default function LabelCorrectionEditor({
  original,
  currentFinal,
  onSubmit,
  onCancel,
  loading,
}: Props) {
  // editorBaseline = what the reviewer sees when opening the editor
  const editorBaseline = currentFinal ?? original;

  const [roadType, setRoadType] = useState<RoadType>(editorBaseline.roadType);
  const [trafficDensity, setTrafficDensity] = useState<TrafficDensity>(editorBaseline.trafficDensity);
  const [roadComplexity, setRoadComplexity] = useState<RoadComplexity>(editorBaseline.roadComplexity);
  const [hazardPresence, setHazardPresence] = useState<HazardPresence>(editorBaseline.hazardPresence);
  const [anticipatedRisk, setAnticipatedRisk] = useState<AnticipatedRisk>(editorBaseline.anticipatedRisk);
  const [recommendedAction, setRecommendedAction] = useState<RecommendedAction>(
    editorBaseline.recommendedAction
  );

  // Reset state when the sample changes
  useEffect(() => {
    setRoadType(editorBaseline.roadType);
    setTrafficDensity(editorBaseline.trafficDensity);
    setRoadComplexity(editorBaseline.roadComplexity);
    setHazardPresence(editorBaseline.hazardPresence);
    setAnticipatedRisk(editorBaseline.anticipatedRisk);
    setRecommendedAction(editorBaseline.recommendedAction);
  }, [editorBaseline]);

  const hasChanges = useCallback(() => {
    return (
      roadType !== editorBaseline.roadType ||
      trafficDensity !== editorBaseline.trafficDensity ||
      roadComplexity !== editorBaseline.roadComplexity ||
      hazardPresence !== editorBaseline.hazardPresence ||
      anticipatedRisk !== editorBaseline.anticipatedRisk ||
      recommendedAction !== editorBaseline.recommendedAction
    );
  }, [
    roadType,
    trafficDensity,
    roadComplexity,
    hazardPresence,
    anticipatedRisk,
    recommendedAction,
    editorBaseline,
  ]);

  const handleSubmit = useCallback(() => {
    // Build corrections relative to original prediction (backend merges over original)
    const corrections: PartialPredictionLabels = {};
    if (roadType !== original.roadType) corrections.roadType = roadType;
    if (trafficDensity !== original.trafficDensity) corrections.trafficDensity = trafficDensity;
    if (roadComplexity !== original.roadComplexity) corrections.roadComplexity = roadComplexity;
    if (hazardPresence !== original.hazardPresence) corrections.hazardPresence = hazardPresence;
    if (anticipatedRisk !== original.anticipatedRisk) corrections.anticipatedRisk = anticipatedRisk;
    if (recommendedAction !== original.recommendedAction)
      corrections.recommendedAction = recommendedAction;
    onSubmit(corrections);
  }, [
    roadType,
    trafficDensity,
    roadComplexity,
    hazardPresence,
    anticipatedRisk,
    recommendedAction,
    original,
    onSubmit,
  ]);

  const changed = hasChanges();

  return (
    <View style={styles.container} testID="training-label-editor">
      <View>
        <SegmentRow
          title="Road Type"
          options={ROAD_TYPES}
          value={roadType}
          onChange={setRoadType}
          baseline={editorBaseline.roadType}
        />
        <SegmentRow
          title="Traffic Density"
          options={TRAFFIC_DENSITIES}
          value={trafficDensity}
          onChange={setTrafficDensity}
          baseline={editorBaseline.trafficDensity}
        />
        <SegmentRow
          title="Road Complexity"
          options={ROAD_COMPLEXITIES}
          value={roadComplexity}
          onChange={setRoadComplexity}
          baseline={editorBaseline.roadComplexity}
        />
        <SegmentRow
          title="Hazard Presence"
          options={HAZARD_PRESENCES}
          value={hazardPresence}
          onChange={setHazardPresence}
          baseline={editorBaseline.hazardPresence}
        />
        <SegmentRow
          title="Anticipated Risk"
          options={ANTICIPATED_RISKS}
          value={anticipatedRisk}
          onChange={setAnticipatedRisk}
          baseline={editorBaseline.anticipatedRisk}
        />
        <SegmentRow
          title="Recommended Action"
          options={RECOMMENDED_ACTIONS}
          value={recommendedAction}
          onChange={setRecommendedAction}
          baseline={editorBaseline.recommendedAction}
        />
      </View>

      <View style={styles.actions}>
        <Pressable
          onPress={onCancel}
          style={({ pressed }) => [styles.btnGhost, pressed && { opacity: 0.7 }]}
          disabled={loading}
        >
          <Text style={styles.btnGhostText}>Cancel</Text>
        </Pressable>
        <Pressable
          onPress={handleSubmit}
          style={({ pressed }) => [
            styles.btnPrimary,
            (!changed || loading) && styles.btnDisabled,
            pressed && changed && !loading && { opacity: 0.85 },
          ]}
          disabled={!changed || loading}
          testID="training-feedback-submit"
        >
          <MaterialCommunityIcons name="check" size={16} color="#000" />
          <Text style={styles.btnPrimaryText}>Submit Correction</Text>
        </Pressable>
      </View>
    </View>
  );
}

function SegmentRow<T extends string>({
  title,
  options,
  value,
  onChange,
  baseline,
}: {
  title: string;
  options: readonly T[];
  value: T;
  onChange: (v: T) => void;
  baseline: T;
}) {
  return (
    <View style={styles.row}>
      <View style={styles.rowHeader}>
        <Text style={styles.rowTitle}>{title}</Text>
        {value !== baseline && (
          <Text style={styles.changedBadge}>CHANGED</Text>
        )}
      </View>
      <View style={styles.segments}>
        {options.map((opt) => (
          <Pressable
            key={opt}
            onPress={() => onChange(opt)}
            style={({ pressed }) => [
              styles.segment,
              value === opt && styles.segmentActive,
              pressed && { opacity: 0.8 },
            ]}
          >
            <Text
              style={[
                styles.segmentText,
                value === opt && styles.segmentTextActive,
              ]}
              numberOfLines={1}
            >
              {opt}
            </Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.md,
  },
  row: {
    marginBottom: spacing.md,
  },
  rowHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: spacing.xs,
  },
  rowTitle: {
    color: colors.onSurfaceSecondary,
    fontSize: 11,
    fontWeight: "600",
    letterSpacing: 0.5,
  },
  changedBadge: {
    color: colors.brand,
    fontSize: 9,
    fontWeight: "700",
    letterSpacing: 0.5,
  },
  segments: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 4,
  },
  segment: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: radius.sm,
    backgroundColor: colors.surfaceTertiary,
    borderWidth: 1,
    borderColor: colors.border,
  },
  segmentActive: {
    backgroundColor: "rgba(0,240,255,0.12)",
    borderColor: colors.brand,
  },
  segmentText: {
    color: colors.onSurfaceSecondary,
    fontSize: 10,
    fontWeight: "500",
  },
  segmentTextActive: {
    color: colors.brand,
    fontWeight: "600",
  },
  actions: {
    flexDirection: "row",
    gap: spacing.sm,
    marginTop: spacing.sm,
  },
  btnGhost: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: radius.sm,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: colors.border,
  },
  btnGhostText: {
    color: colors.onSurfaceSecondary,
    fontSize: 12,
    fontWeight: "500",
  },
  btnPrimary: {
    flex: 2,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingVertical: 10,
    borderRadius: radius.sm,
    backgroundColor: colors.brand,
  },
  btnDisabled: {
    backgroundColor: colors.surfaceTertiary,
    opacity: 0.6,
  },
  btnPrimaryText: {
    color: "#000",
    fontSize: 12,
    fontWeight: "600",
  },
});

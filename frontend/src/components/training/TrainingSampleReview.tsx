import React, { useState, useCallback } from "react";
import { View, Text, Pressable, StyleSheet, ScrollView, TextInput } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, spacing, radius } from "@/src/theme";
import type { TrainingSample, TrainingFeedbackCreate } from "@/src/types/training";
import LabelCorrectionEditor from "./LabelCorrectionEditor";

type Props = {
  sample: TrainingSample;
  onFeedback: (payload: TrainingFeedbackCreate) => void;
  loading?: boolean;
};

export default function TrainingSampleReview({ sample, onFeedback, loading }: Props) {
  const [mode, setMode] = useState<"view" | "correct" | "reject-confirm">("view");
  const [rejectNote, setRejectNote] = useState("");

  const handleConfirm = useCallback(() => {
    onFeedback({
      status: "confirmed",
      submittedBy: "sentinel-demo-reviewer",
    });
  }, [onFeedback]);

  const handleCorrect = useCallback(
    (corrections: import("@/src/types/training").PartialPredictionLabels) => {
      onFeedback({
        status: "corrected",
        correctedLabels: corrections,
        submittedBy: "sentinel-demo-reviewer",
      });
      setMode("view");
    },
    [onFeedback]
  );

  const handleReject = useCallback(() => {
    onFeedback({
      status: "rejected",
      submittedBy: "sentinel-demo-reviewer",
      note: rejectNote.trim() || undefined,
    });
    setMode("view");
    setRejectNote("");
  }, [onFeedback, rejectNote]);

  const labels = sample.finalVerifiedLabels ?? sample.originalPrediction;

  return (
    <View style={styles.container} testID="training-sample-review">
      <ScrollView showsVerticalScrollIndicator={false}>
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Context</Text>
          <KV k="Location" v={`${sample.context.location.latitude.toFixed(4)}, ${sample.context.location.longitude.toFixed(4)}`} />
          <KV k="Heading" v={`${sample.context.headingDegrees ?? "—"}°`} />
          <KV k="Speed" v={`${sample.context.speedKmh ?? "—"} km/h`} />
          <KV k="Road" v={sample.context.roadName ?? "—"} />
          <KV k="Direction" v={sample.context.routeDirection ?? "—"} />
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Media</Text>
          <KV k="URI" v={sample.media.uri} />
          <KV k="Storage" v={sample.media.storageMode} />
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Model</Text>
          <KV k="Provider" v={sample.model.provider} />
          <KV k="Name" v={`${sample.model.name} v${sample.model.version}`} />
          <KV k="Inference" v={sample.model.inferenceMode} />
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Original Prediction</Text>
          <PredictionBlock labels={sample.originalPrediction} />
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>
            {sample.finalVerifiedLabels ? "Final Verified Labels" : "Current Labels"}
          </Text>
          <PredictionBlock labels={labels} />
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Provenance</Text>
          <KV k="Source" v={sample.provenance.source} />
          <KV k="Hazard ID" v={sample.provenance.graphHazardId ?? "—"} />
          <KV k="Observation ID" v={sample.provenance.graphObservationId ?? "—"} />
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Quality</Text>
          <KV k="Privacy" v={sample.quality?.privacyStatus ?? "not_reviewed"} />
        </View>

        {sample.feedbackHistory.length > 0 && (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Feedback History</Text>
            {sample.feedbackHistory.map((evt, i) => (
              <View key={i} style={styles.historyRow}>
                <Text style={styles.historyStatus}>{evt.status}</Text>
                <Text style={styles.historyMeta}>
                  {new Date(evt.submittedAt).toLocaleString()}
                  {evt.note ? ` · ${evt.note}` : ""}
                </Text>
              </View>
            ))}
          </View>
        )}

        {mode === "correct" && (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Correct Labels</Text>
            <LabelCorrectionEditor
              original={sample.originalPrediction}
              currentFinal={sample.finalVerifiedLabels}
              onSubmit={handleCorrect}
              onCancel={() => setMode("view")}
              loading={loading}
            />
          </View>
        )}

        {mode === "reject-confirm" && (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Reject Sample</Text>
            <Text style={styles.rejectHint}>
              This will mark the sample as rejected and exclude it from export.
            </Text>
            <TextInput
              style={styles.noteInput}
              placeholder="Optional note…"
              placeholderTextColor={colors.onSurfaceTertiary}
              value={rejectNote}
              onChangeText={setRejectNote}
              multiline
              maxLength={500}
            />
            <View style={styles.rejectActions}>
              <Pressable
                onPress={() => setMode("view")}
                style={({ pressed }) => [styles.btnGhost, pressed && { opacity: 0.7 }]}
              >
                <Text style={styles.btnGhostText}>Cancel</Text>
              </Pressable>
              <Pressable
                onPress={handleReject}
                style={({ pressed }) => [styles.btnDanger, pressed && { opacity: 0.85 }]}
                disabled={loading}
              >
                <Text style={styles.btnDangerText}>Reject Sample</Text>
              </Pressable>
            </View>
          </View>
        )}
      </ScrollView>

      {mode === "view" && (
        <View style={styles.bottomActions}>
          <Pressable
            onPress={handleConfirm}
            style={({ pressed }) => [styles.btnConfirm, pressed && { opacity: 0.85 }]}
            disabled={loading}
            testID="training-sample-confirm"
          >
            <MaterialCommunityIcons name="check-circle" size={18} color="#000" />
            <Text style={styles.btnConfirmText}>Confirm Labels</Text>
          </Pressable>
          <Pressable
            onPress={() => setMode("correct")}
            style={({ pressed }) => [styles.btnCorrect, pressed && { opacity: 0.85 }]}
            disabled={loading}
            testID="training-sample-correct"
          >
            <MaterialCommunityIcons name="pencil" size={18} color={colors.brand} />
            <Text style={styles.btnCorrectText}>Correct Labels</Text>
          </Pressable>
          <Pressable
            onPress={() => setMode("reject-confirm")}
            style={({ pressed }) => [styles.btnReject, pressed && { opacity: 0.85 }]}
            disabled={loading}
            testID="training-sample-reject"
          >
            <MaterialCommunityIcons name="close-circle" size={18} color={colors.error} />
            <Text style={styles.btnRejectText}>Reject</Text>
          </Pressable>
        </View>
      )}
    </View>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <View style={styles.kvRow}>
      <Text style={styles.kvKey}>{k}</Text>
      <Text style={styles.kvValue} numberOfLines={1}>
        {v}
      </Text>
    </View>
  );
}

function PredictionBlock({ labels }: { labels: import("@/src/types/training").PredictionLabels }) {
  return (
    <View style={styles.predictionGrid}>
      <PredChip label="Road" value={labels.roadType} />
      <PredChip label="Traffic" value={labels.trafficDensity} />
      <PredChip label="Complexity" value={labels.roadComplexity} />
      <PredChip label="Hazard" value={labels.hazardPresence} />
      <PredChip label="Risk" value={labels.anticipatedRisk} />
      <PredChip label="Action" value={labels.recommendedAction} />
      {typeof labels.confidence === "number" && (
        <PredChip label="Confidence" value={`${(labels.confidence * 100).toFixed(0)}%`} />
      )}
    </View>
  );
}

function PredChip({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.predChip}>
      <Text style={styles.predChipLabel}>{label}</Text>
      <Text style={styles.predChipValue}>{value}</Text>
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
    marginHorizontal: spacing.lg,
    marginBottom: spacing.md,
  },
  section: {
    marginBottom: spacing.md,
  },
  sectionTitle: {
    color: colors.onSurfaceSecondary,
    fontSize: 11,
    fontWeight: "600",
    letterSpacing: 1,
    marginBottom: spacing.xs,
  },
  kvRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 3,
  },
  kvKey: {
    color: colors.onSurfaceTertiary,
    fontSize: 11,
  },
  kvValue: {
    color: colors.onSurface,
    fontSize: 11,
    fontWeight: "500",
    flex: 1,
    textAlign: "right",
    marginLeft: spacing.sm,
  },
  predictionGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 4,
  },
  predChip: {
    backgroundColor: colors.surfaceTertiary,
    paddingHorizontal: 8,
    paddingVertical: 5,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.border,
    minWidth: 80,
  },
  predChipLabel: {
    color: colors.onSurfaceTertiary,
    fontSize: 9,
    letterSpacing: 0.3,
  },
  predChipValue: {
    color: colors.onSurface,
    fontSize: 11,
    fontWeight: "600",
    marginTop: 1,
  },
  historyRow: {
    paddingVertical: 4,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  historyStatus: {
    color: colors.onSurface,
    fontSize: 11,
    fontWeight: "600",
    textTransform: "uppercase",
  },
  historyMeta: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    marginTop: 1,
  },
  rejectHint: {
    color: colors.onSurfaceTertiary,
    fontSize: 11,
    marginBottom: spacing.sm,
  },
  noteInput: {
    backgroundColor: colors.surfaceTertiary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    padding: spacing.sm,
    color: colors.onSurface,
    fontSize: 12,
    minHeight: 60,
    textAlignVertical: "top",
    marginBottom: spacing.sm,
  },
  rejectActions: {
    flexDirection: "row",
    gap: spacing.sm,
  },
  bottomActions: {
    flexDirection: "row",
    gap: spacing.sm,
    marginTop: spacing.sm,
    paddingTop: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  btnConfirm: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingVertical: 10,
    borderRadius: radius.sm,
    backgroundColor: colors.success,
  },
  btnConfirmText: {
    color: "#000",
    fontSize: 12,
    fontWeight: "600",
  },
  btnCorrect: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingVertical: 10,
    borderRadius: radius.sm,
    backgroundColor: "rgba(0,240,255,0.08)",
    borderWidth: 1,
    borderColor: colors.brand,
  },
  btnCorrectText: {
    color: colors.brand,
    fontSize: 12,
    fontWeight: "600",
  },
  btnReject: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingVertical: 10,
    borderRadius: radius.sm,
    backgroundColor: "rgba(248,81,73,0.08)",
    borderWidth: 1,
    borderColor: colors.error,
  },
  btnRejectText: {
    color: colors.error,
    fontSize: 12,
    fontWeight: "600",
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
  btnDanger: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: radius.sm,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.error,
  },
  btnDangerText: {
    color: "#000",
    fontSize: 12,
    fontWeight: "600",
  },
});

import React, { useState } from "react";
import { View, Text, StyleSheet, Pressable, LayoutAnimation, Platform, UIManager } from "react-native";
import { colors, spacing, radius, fonts } from "@/src/theme";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import type {
  DemoReplayEvidenceResponse,
  DemoReplayGraphVerifyResponse,
  DemoReplayInferenceResponse,
} from "@/src/types/demoReplay";

// Enable LayoutAnimation for Android
if (Platform.OS === "android" && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

type ReplayEvidencePanelProps = {
  evidence: DemoReplayEvidenceResponse | null;
  graphVerify: DemoReplayGraphVerifyResponse | null;
  inference: DemoReplayInferenceResponse | null;
};

function formatLabel(val: string): string {
  return val.replace(/_/g, " ").toUpperCase();
}

export default function ReplayEvidencePanel({ evidence, graphVerify, inference }: ReplayEvidencePanelProps) {
  const [collapsed, setCollapsed] = useState(true);

  const toggleCollapse = () => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setCollapsed(!collapsed);
  };

  if (!evidence || !inference) return null;

  const expected = evidence.expectedLabels;
  const actual = inference.prediction;

  // Compute field comparison mapping
  const fields = [
    { key: "roadType", label: "ROAD TYPE", exp: expected?.roadType, act: actual.roadType },
    { key: "trafficDensity", label: "TRAFFIC DENSITY", exp: expected?.trafficDensity, act: actual.trafficDensity },
    { key: "roadComplexity", label: "ROAD COMPLEXITY", exp: expected?.roadComplexity, act: actual.roadComplexity },
    { key: "hazardPresence", label: "HAZARD DETECTED", exp: expected?.hazardPresence, act: actual.hazardPresence },
    { key: "anticipatedRisk", label: "ANTICIPATED RISK", exp: expected?.anticipatedRisk, act: actual.anticipatedRisk },
    { key: "recommendedAction", label: "RECOMMENDED ACTION", exp: expected?.recommendedAction, act: actual.recommendedAction },
  ];

  return (
    <View style={styles.container} testID="demo-replay-evidence-panel">
      {/* Header (Pressable for collapse/expand) */}
      <Pressable onPress={toggleCollapse} style={styles.header}>
        <View style={styles.headerTitleRow}>
          <MaterialCommunityIcons name="file-document-outline" size={18} color={colors.brand} />
          <Text style={styles.headerTitle}>RESEARCH PROVENANCE & EVIDENCE</Text>
        </View>
        <MaterialCommunityIcons
          name={collapsed ? "chevron-down" : "chevron-up"}
          size={20}
          color={colors.onSurfaceSecondary}
        />
      </Pressable>

      {!collapsed && (
        <View style={styles.content}>
          {/* Provenance Metadata Section */}
          <View style={styles.metaSection}>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>Dataset Replay Mode:</Text>
              <View style={styles.badge}>
                <Text style={styles.badgeText}>Dataset Replay</Text>
              </View>
            </View>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>Source Sample mapping:</Text>
              <Text style={styles.metaValue}>
                {evidence.sourceSampleId ? `Original: ${evidence.sourceSampleId}` : "Unmapped"}
              </Text>
            </View>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>VLM Output Mode:</Text>
              <View style={[styles.badge, styles.badgeOrange]}>
                <Text style={[styles.badgeText, styles.badgeTextOrange]}>
                  Cached genuine Qwen fusion output
                </Text>
              </View>
            </View>
          </View>

          {/* Expected vs Actual Grid */}
          <Text style={styles.sectionTitle}>PREDICTION COMPARISON</Text>
          <View style={styles.grid}>
            {fields.map((f) => {
              const matches = f.exp === f.act;
              return (
                <View key={f.key} style={styles.gridItem}>
                  <View style={styles.fieldHeader}>
                    <Text style={styles.fieldLabel}>{f.label}</Text>
                    <MaterialCommunityIcons
                      name={matches ? "check-circle" : "alert-circle"}
                      size={14}
                      color={matches ? colors.success : colors.warning}
                    />
                  </View>
                  <View style={styles.valuesRow}>
                    <View style={styles.valueBox}>
                      <Text style={styles.valueBoxLabel}>Expected (GT)</Text>
                      <Text style={styles.valueBoxText}>
                        {f.exp ? formatLabel(f.exp) : "N/A"}
                      </Text>
                    </View>
                    <View style={styles.valueBox}>
                      <Text style={styles.valueBoxLabel}>Actual (Qwen)</Text>
                      <Text style={[styles.valueBoxText, { color: matches ? colors.onSurface : colors.brand }]}>
                        {formatLabel(f.act)}
                      </Text>
                    </View>
                  </View>
                </View>
              );
            })}
          </View>

          {/* Graph Verification Section */}
          {graphVerify && (
            <View style={styles.graphSection}>
              <Text style={styles.sectionTitle}>PERSISTENCE & GRAPH VERIFICATION</Text>
              <View style={styles.graphVerifyCard}>
                <View style={styles.backendModeRow}>
                  <Text style={styles.backendModeLabel}>Graph Backend:</Text>
                  <Text style={styles.backendModeValue}>
                    {graphVerify.graphBackend === "neo4j" ? "Neo4j AuraDB (Strict Verified)" : "In-Memory Fallback"}
                  </Text>
                </View>

                <View style={styles.checksList}>
                  <View style={styles.checkItem}>
                    <MaterialCommunityIcons
                      name={graphVerify.hazardNodeFound ? "check-bold" : "close-thick"}
                      size={14}
                      color={graphVerify.hazardNodeFound ? colors.success : colors.error}
                    />
                    <Text style={styles.checkText}>Hazard node exists in graph database</Text>
                  </View>

                  <View style={styles.checkItem}>
                    <MaterialCommunityIcons
                      name={graphVerify.observationNodeFound ? "check-bold" : "close-thick"}
                      size={14}
                      color={graphVerify.observationNodeFound ? colors.success : colors.error}
                    />
                    <Text style={styles.checkText}>Observation node exists in graph database</Text>
                  </View>

                  <View style={styles.checkItem}>
                    <MaterialCommunityIcons
                      name={graphVerify.relationshipFound ? "check-bold" : "close-thick"}
                      size={14}
                      color={graphVerify.relationshipFound ? colors.success : colors.error}
                    />
                    <Text style={styles.checkText}>SUPPORTS provenance relationship verified</Text>
                  </View>

                  <View style={styles.checkItem}>
                    <MaterialCommunityIcons
                      name={graphVerify.warningNodeFound ? "check-bold" : "minus"}
                      size={14}
                      color={graphVerify.warningNodeFound ? colors.success : colors.onSurfaceTertiary}
                    />
                    <Text style={styles.checkText}>
                      Warning node verification ({graphVerify.warningNodeFound ? "Dispatched" : "No warned vehicles"})
                    </Text>
                  </View>
                </View>

                <View style={styles.summaryContainer}>
                  <MaterialCommunityIcons name="shield-check" size={16} color={colors.success} />
                  <Text style={styles.summaryText}>{graphVerify.summary}</Text>
                </View>
              </View>
            </View>
          )}
        </View>
      )}
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
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    padding: spacing.md,
    backgroundColor: colors.surfaceSecondary,
  },
  headerTitleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
  },
  headerTitle: {
    color: colors.onSurface,
    fontSize: fonts.size.sm,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 0.5,
  },
  content: {
    padding: spacing.md,
    borderTopWidth: 1,
    borderTopColor: colors.divider,
    gap: spacing.md,
  },
  metaSection: {
    backgroundColor: colors.surfaceTertiary,
    borderRadius: radius.md,
    padding: spacing.md,
    gap: spacing.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  metaRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  metaLabel: {
    color: colors.onSurfaceSecondary,
    fontSize: fonts.size.sm,
    fontFamily: fonts.family,
  },
  metaValue: {
    color: colors.onSurface,
    fontSize: fonts.size.sm,
    fontFamily: fonts.family,
    fontWeight: "bold",
  },
  badge: {
    backgroundColor: "rgba(0, 240, 255, 0.1)",
    borderWidth: 1,
    borderColor: colors.brand,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  badgeText: {
    color: colors.brand,
    fontSize: fonts.size.sm - 3,
    fontFamily: fonts.family,
    fontWeight: "bold",
  },
  badgeOrange: {
    backgroundColor: "rgba(210, 153, 34, 0.1)",
    borderColor: colors.warning,
  },
  badgeTextOrange: {
    color: colors.warning,
  },
  sectionTitle: {
    color: colors.onSurfaceSecondary,
    fontSize: fonts.size.sm - 2,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 1,
    marginTop: spacing.xs,
  },
  grid: {
    gap: spacing.sm,
  },
  gridItem: {
    backgroundColor: colors.surfaceTertiary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.sm,
    gap: spacing.xs,
  },
  fieldHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  fieldLabel: {
    color: colors.onSurfaceSecondary,
    fontSize: fonts.size.sm - 3,
    fontFamily: fonts.family,
    fontWeight: "bold",
  },
  valuesRow: {
    flexDirection: "row",
    gap: spacing.sm,
  },
  valueBox: {
    flex: 1,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.sm,
    padding: 6,
    gap: 2,
    borderWidth: 1,
    borderColor: colors.border,
  },
  valueBoxLabel: {
    color: colors.onSurfaceTertiary,
    fontSize: fonts.size.sm - 4,
    fontFamily: fonts.family,
    fontWeight: "500",
  },
  valueBoxText: {
    color: colors.onSurfaceSecondary,
    fontSize: fonts.size.sm - 1,
    fontFamily: fonts.family,
    fontWeight: "600",
  },
  graphSection: {
    gap: spacing.sm,
  },
  graphVerifyCard: {
    backgroundColor: colors.surfaceTertiary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.md,
    gap: spacing.sm,
  },
  backendModeRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
    paddingBottom: spacing.sm,
  },
  backendModeLabel: {
    color: colors.onSurfaceSecondary,
    fontSize: fonts.size.sm,
    fontFamily: fonts.family,
  },
  backendModeValue: {
    color: colors.brand,
    fontSize: fonts.size.sm,
    fontFamily: fonts.family,
    fontWeight: "bold",
  },
  checksList: {
    gap: 6,
    paddingVertical: 2,
  },
  checkItem: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
  },
  checkText: {
    color: colors.onSurfaceSecondary,
    fontSize: fonts.size.sm - 1,
    fontFamily: fonts.family,
  },
  summaryContainer: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.xs,
    backgroundColor: "rgba(46, 160, 67, 0.1)",
    borderWidth: 1,
    borderColor: colors.success,
    borderRadius: radius.sm,
    padding: spacing.sm,
    marginTop: spacing.xs,
  },
  summaryText: {
    color: colors.success,
    fontSize: fonts.size.sm,
    fontFamily: fonts.family,
    fontWeight: "bold",
  },
});

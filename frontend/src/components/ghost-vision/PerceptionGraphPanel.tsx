import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  ActivityIndicator,
} from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, spacing, radius, fonts } from "@/src/theme";
import { api } from "@/src/api/sentinel";
import type {
  GraphNode,
  GraphEdge,
  PerceptionGraphResponse,
} from "@/src/types/sentinel";

type Props = {
  hazardId?: string | null;
  refreshKey?: number;
};

export default function PerceptionGraphPanel({ hazardId, refreshKey = 0 }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [data, setData] = useState<PerceptionGraphResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchGraph = useCallback(async () => {
    if (!hazardId) return;
    setLoading(true);
    setError(null);
    try {
      const graph = await api.perceptionGraph(hazardId, 25);
      setData(graph);
    } catch (err: any) {
      setError(err?.message ?? "Failed to load provenance");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [hazardId]);

  useEffect(() => {
    if (expanded && hazardId) {
      fetchGraph();
    }
  }, [expanded, hazardId, refreshKey, fetchGraph]);

  const toggle = useCallback(() => {
    setExpanded((prev) => !prev);
  }, []);

  const chain = useMemo(() => {
    if (!data || !hazardId) return null;
    return buildChain(data.nodes, data.edges, hazardId);
  }, [data, hazardId]);

  const recentTimeline = useMemo(() => {
    if (!data) return [];
    return data.timeline.slice(0, 5);
  }, [data]);

  const modeLabel = data?.mode === "neo4j" ? "NEO4J" : "MEMORY";
  const focus = data?.summary.focus;

  return (
    <View style={styles.container} testID="perception-graph-panel">
      {/* Collapsed button / card */}
      {!expanded && (
        <Pressable
          onPress={toggle}
          style={({ pressed }) => [styles.toggleCard, pressed && { opacity: 0.85 }]}
          testID="perception-graph-toggle"
        >
          <MaterialCommunityIcons name="graph-outline" size={16} color={colors.brand} />
          <Text style={styles.toggleText}>WHY THIS WARNING?</Text>
          <MaterialCommunityIcons name="chevron-down" size={16} color={colors.onSurfaceSecondary} />
        </Pressable>
      )}

      {/* Expanded panel */}
      {expanded && (
        <View style={styles.expandedPanel}>
          <Pressable
            onPress={toggle}
            style={styles.headerRow}
            testID="perception-graph-toggle"
          >
            <View style={styles.headerLeft}>
              <MaterialCommunityIcons name="graph-outline" size={16} color={colors.brand} />
              <Text style={styles.headerTitle}>WHY THIS WARNING?</Text>
            </View>
            <MaterialCommunityIcons name="chevron-up" size={16} color={colors.onSurfaceSecondary} />
          </Pressable>

          {loading && (
            <View style={styles.centered} testID="perception-graph-loading">
              <ActivityIndicator size="small" color={colors.brand} />
              <Text style={styles.loadingText}>Loading provenance…</Text>
            </View>
          )}

          {!loading && error && (
            <View style={styles.errorBlock} testID="perception-graph-error">
              <MaterialCommunityIcons name="alert-circle-outline" size={18} color={colors.error} />
              <Text style={styles.errorText}>{error}</Text>
              <Pressable
                onPress={fetchGraph}
                style={styles.retryBtn}
                testID="perception-graph-retry"
              >
                <Text style={styles.retryBtnText}>Retry</Text>
              </Pressable>
            </View>
          )}

          {!loading && !error && !data && (
            <View style={styles.centered}>
              <Text style={styles.emptyText}>No provenance data available</Text>
            </View>
          )}

          {!loading && !error && data && (
            <>
              {/* Summary */}
              <View style={styles.summaryBlock} testID="perception-graph-summary">
                <View style={styles.summaryRow}>
                  <SummaryPill
                    icon="account-check"
                    label="Sources"
                    value={String(focus?.sourceCount ?? 0)}
                  />
                  <SummaryPill
                    icon="shield-check"
                    label="Confidence"
                    value={`${focus?.confidence ?? 0}%`}
                  />
                  <SummaryPill
                    icon="bell-alert"
                    label="Warnings"
                    value={String(focus?.warningCount ?? 0)}
                  />
                  <SummaryPill
                    icon="database"
                    label="Mode"
                    value={modeLabel}
                  />
                </View>
              </View>

              {/* Provenance Chain */}
              {chain && (
                <View style={styles.chainBlock} testID="perception-graph-chain">
                  <Text style={styles.sectionTitle}>PROVENANCE CHAIN</Text>
                  {chain.stages.map((stage, i) => (
                    <View key={stage.node.id + i}>
                      <StageCard node={stage.node} />
                      {stage.relLabel && (
                        <View style={styles.relRow}>
                          <View style={styles.relLine} />
                          <Text style={styles.relLabel}>{stage.relLabel}</Text>
                          <View style={styles.relLine} />
                        </View>
                      )}
                    </View>
                  ))}
                </View>
              )}

              {/* Timeline */}
              {recentTimeline.length > 0 && (
                <View style={styles.timelineBlock} testID="perception-graph-timeline">
                  <Text style={styles.sectionTitle}>RECENT EVENTS</Text>
                  {recentTimeline.map((evt) => (
                    <View key={evt.eventId} style={styles.timelineItem}>
                      <View style={styles.timelineDot} />
                      <View style={styles.timelineContent}>
                        <Text style={styles.timelineType}>{evt.type}</Text>
                        <Text style={styles.timelineDesc} numberOfLines={2}>
                          {evt.description}
                        </Text>
                      </View>
                    </View>
                  ))}
                </View>
              )}
            </>
          )}
        </View>
      )}
    </View>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type ChainStage = {
  node: GraphNode;
  relLabel?: string;
};

function buildChain(
  nodes: GraphNode[],
  edges: GraphEdge[],
  hazardId: string
): { stages: ChainStage[] } | null {
  const nodeMap = new Map(nodes.map((n) => [n.id, n]));
  const outEdges = new Map<string, GraphEdge[]>();
  const inEdges = new Map<string, GraphEdge[]>();
  for (const e of edges) {
    outEdges.set(e.source, [...(outEdges.get(e.source) ?? []), e]);
    inEdges.set(e.target, [...(inEdges.get(e.target) ?? []), e]);
  }

  const hazard = nodeMap.get(hazardId);
  if (!hazard) return null;

  const stages: ChainStage[] = [];

  // Observer vehicle → Observation → Hazard
  const obsEdges = inEdges.get(hazardId)?.filter((e) => e.type === "SUPPORTS") ?? [];
  const primaryObs = obsEdges[0];
  if (primaryObs) {
    const obsNode = nodeMap.get(primaryObs.source);
    if (obsNode) {
      const obsFromVehicle = inEdges.get(obsNode.id)?.filter((e) => e.type === "OBSERVED")[0];
      if (obsFromVehicle) {
        const vehicleNode = nodeMap.get(obsFromVehicle.source);
        if (vehicleNode) {
          stages.push({ node: vehicleNode });
          stages.push({ node: obsNode, relLabel: "OBSERVED" });
          stages.push({ node: hazard, relLabel: "SUPPORTS" });
        }
      }
    }
  }

  if (stages.length === 0) {
    stages.push({ node: hazard });
  }

  // Hazard → Warning → Recipient Vehicle
  const warningEdges = outEdges.get(hazardId)?.filter((e) => e.type === "TRIGGERED_WARNING") ?? [];
  for (const we of warningEdges) {
    const warningNode = nodeMap.get(we.target);
    if (!warningNode) continue;
    stages.push({ node: warningNode, relLabel: "TRIGGERED_WARNING" });

    const deliveredEdges = outEdges.get(warningNode.id)?.filter((e) => e.type === "DELIVERED_TO") ?? [];
    for (const de of deliveredEdges) {
      const recipientNode = nodeMap.get(de.target);
      if (recipientNode) {
        stages.push({ node: recipientNode, relLabel: "DELIVERED_TO" });
      }
    }
  }

  return { stages };
}

function StageCard({ node }: { node: GraphNode }) {
  const icon = nodeTypeIcon(node.type);
  const subtitle = nodeSubtitle(node);
  return (
    <View style={styles.stageCard}>
      <View style={styles.stageIconWrap}>
        <MaterialCommunityIcons name={icon} size={18} color={colors.brand} />
      </View>
      <View style={styles.stageTextWrap}>
        <Text style={styles.stageType}>{node.type.toUpperCase()}</Text>
        <Text style={styles.stageLabel} numberOfLines={1}>
          {node.label}
        </Text>
        {subtitle && <Text style={styles.stageSub}>{subtitle}</Text>}
      </View>
    </View>
  );
}

function nodeTypeIcon(type: GraphNode["type"]): keyof typeof MaterialCommunityIcons.glyphMap {
  switch (type) {
    case "Vehicle":
      return "car";
    case "Observation":
      return "eye-outline";
    case "Hazard":
      return "alert-circle";
    case "RoadSegment":
      return "road-variant";
    case "Warning":
      return "bell-alert";
    default:
      return "help-circle";
  }
}

function nodeSubtitle(node: GraphNode): string | null {
  if (node.type === "Hazard") {
    const sc = node.properties.sourceCount;
    const conf = node.properties.confidence;
    if (typeof sc === "number" && typeof conf === "number") {
      return `${sc} source${sc === 1 ? "" : "s"} · ${conf}% confidence`;
    }
  }
  if (node.type === "Warning") {
    const lang = node.properties.language;
    if (lang) return `Language: ${lang}`;
  }
  return null;
}

function SummaryPill({
  icon,
  label,
  value,
}: {
  icon: keyof typeof MaterialCommunityIcons.glyphMap;
  label: string;
  value: string;
}) {
  return (
    <View style={styles.pill}>
      <MaterialCommunityIcons name={icon} size={12} color={colors.onSurfaceSecondary} />
      <Text style={styles.pillValue}>{value}</Text>
      <Text style={styles.pillLabel}>{label}</Text>
    </View>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles = StyleSheet.create({
  container: {
    marginHorizontal: spacing.lg,
    marginTop: spacing.md,
  },
  toggleCard: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: spacing.sm,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  toggleText: {
    flex: 1,
    color: colors.onSurface,
    fontSize: fonts.size.sm,
    fontWeight: "600",
    letterSpacing: 1,
  },
  expandedPanel: {
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.md,
    gap: spacing.md,
  },
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  headerLeft: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
  },
  headerTitle: {
    color: colors.onSurface,
    fontSize: fonts.size.sm,
    fontWeight: "600",
    letterSpacing: 1,
  },
  centered: {
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: spacing.xl,
    gap: spacing.sm,
  },
  loadingText: {
    color: colors.onSurfaceSecondary,
    fontSize: fonts.size.sm,
    letterSpacing: 1,
  },
  errorBlock: {
    alignItems: "center",
    gap: spacing.sm,
    paddingVertical: spacing.xl,
  },
  errorText: {
    color: colors.error,
    fontSize: fonts.size.sm,
    textAlign: "center",
  },
  retryBtn: {
    marginTop: spacing.sm,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    backgroundColor: colors.surfaceTertiary,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  retryBtnText: {
    color: colors.brand,
    fontSize: fonts.size.sm,
    fontWeight: "600",
  },
  emptyText: {
    color: colors.onSurfaceSecondary,
    fontSize: fonts.size.sm,
  },
  summaryBlock: {
    marginTop: spacing.xs,
  },
  summaryRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.sm,
  },
  pill: {
    flex: 1,
    minWidth: 64,
    alignItems: "center",
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.xs,
    gap: 2,
  },
  pillValue: {
    color: colors.onSurface,
    fontSize: fonts.size.lg,
    fontWeight: "600",
  },
  pillLabel: {
    color: colors.onSurfaceSecondary,
    fontSize: 9,
    letterSpacing: 1,
  },
  chainBlock: {
    gap: spacing.xs,
  },
  sectionTitle: {
    color: colors.onSurfaceSecondary,
    fontSize: 10,
    fontWeight: "600",
    letterSpacing: 1.2,
    marginBottom: spacing.xs,
  },
  stageCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.sm,
  },
  stageIconWrap: {
    width: 36,
    height: 36,
    borderRadius: radius.sm,
    backgroundColor: colors.surfaceSecondary,
    alignItems: "center",
    justifyContent: "center",
  },
  stageTextWrap: {
    flex: 1,
    gap: 2,
  },
  stageType: {
    color: colors.brand,
    fontSize: 9,
    fontWeight: "600",
    letterSpacing: 1.2,
  },
  stageLabel: {
    color: colors.onSurface,
    fontSize: fonts.size.sm,
    fontWeight: "500",
  },
  stageSub: {
    color: colors.onSurfaceSecondary,
    fontSize: 10,
  },
  relRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingVertical: 4,
  },
  relLine: {
    flex: 1,
    height: 1,
    backgroundColor: colors.border,
  },
  relLabel: {
    color: colors.onSurfaceTertiary,
    fontSize: 9,
    fontWeight: "600",
    letterSpacing: 1.2,
  },
  timelineBlock: {
    gap: spacing.sm,
  },
  timelineItem: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.sm,
  },
  timelineDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: colors.brand,
    marginTop: 6,
  },
  timelineContent: {
    flex: 1,
    gap: 2,
  },
  timelineType: {
    color: colors.brand,
    fontSize: 9,
    fontWeight: "600",
    letterSpacing: 1.2,
  },
  timelineDesc: {
    color: colors.onSurfaceSecondary,
    fontSize: 10,
    lineHeight: 14,
  },
});

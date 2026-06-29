import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  ScrollView,
  ActivityIndicator,
  RefreshControl,
  Linking,
} from "react-native";
import { SafeAreaView, useSafeAreaInsets } from "react-native-safe-area-context";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { colors, spacing, radius, fonts } from "@/src/theme";
import { trainingApi } from "@/src/api/trainingSamples";
import { ApiError } from "@/src/api/sentinel";
import type {
  TrainingSample,
  TrainingStats,
  DatasetStatus,
  TrainingFeedbackCreate,
} from "@/src/types/training";
import TrainingStatsComp from "@/src/components/training/TrainingStats";
import TrainingSampleCard from "@/src/components/training/TrainingSampleCard";
import TrainingSampleReview from "@/src/components/training/TrainingSampleReview";

const FILTERS: { label: string; key: DatasetStatus | "all"; testId: string }[] = [
  { label: "All", key: "all", testId: "training-data-filter-all" },
  { label: "Pending", key: "pending", testId: "training-data-filter-pending" },
  { label: "Verified", key: "verified", testId: "training-data-filter-verified" },
  { label: "Rejected", key: "rejected", testId: "training-data-filter-rejected" },
];

export default function TrainingDataScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const mountedRef = useRef(true);

  const [stats, setStats] = useState<TrainingStats | null>(null);
  const [samples, setSamples] = useState<TrainingSample[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeFilter, setActiveFilter] = useState<DatasetStatus | "all">("all");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mutationLoading, setMutationLoading] = useState(false);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const fetchAll = useCallback(async (isRefresh = false) => {
    if (!isRefresh) setLoading(true);
    setError(null);
    try {
      const [s, list] = await Promise.all([
        trainingApi.getTrainingStats(),
        trainingApi.listTrainingSamples(
          activeFilter === "all" ? undefined : { status: activeFilter }
        ),
      ]);
      if (!mountedRef.current) return;
      setStats(s);
      setSamples(list.items);
      // Keep selected only if it still belongs in the active filter
      setSelectedId((prev) => {
        if (!prev) return null;
        const stillPresent = list.items.some((i) => i.sampleId === prev);
        if (!stillPresent) return null;
        // If filtering by status, verify the sample still matches
        if (activeFilter !== "all") {
          const sample = list.items.find((i) => i.sampleId === prev);
          if (!sample || sample.datasetStatus !== activeFilter) return null;
        }
        return prev;
      });
    } catch (err: unknown) {
      if (!mountedRef.current) return;
      const msg = err instanceof ApiError ? err.message : String(err);
      setError(msg);
    } finally {
      if (mountedRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [activeFilter]);

  useEffect(() => {
    mountedRef.current = true;
    fetchAll();
    return () => {
      mountedRef.current = false;
    };
  }, [fetchAll]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    fetchAll(true);
  }, [fetchAll]);

  const onFeedback = useCallback(
    async (sampleId: string, payload: TrainingFeedbackCreate) => {
      setMutationLoading(true);
      setMutationError(null);
      setSuccessMsg(null);
      try {
        const updated = await trainingApi.submitTrainingFeedback(sampleId, payload);
        if (!mountedRef.current) return updated;

        // Refresh the filtered list so the sample disappears if it no longer matches
        await fetchAll(true);

        if (mountedRef.current) {
          setSuccessMsg(
            payload.status === "confirmed"
              ? "Sample confirmed"
              : payload.status === "corrected"
              ? "Sample corrected"
              : "Sample rejected"
          );
          // Clear success message after delay
          setTimeout(() => {
            if (mountedRef.current) setSuccessMsg(null);
          }, 2000);
        }
        return updated;
      } catch (err: unknown) {
        if (!mountedRef.current) throw err;
        const msg = err instanceof ApiError ? err.message : String(err);
        if (err instanceof ApiError && err.status === 404) {
          setMutationError("Sample no longer exists. Refreshing list…");
          setSelectedId(null);
          fetchAll(true);
        } else {
          setMutationError(msg);
        }
        throw err;
      } finally {
        if (mountedRef.current) setMutationLoading(false);
      }
    },
    [fetchAll]
  );

  const onExport = useCallback(() => {
    const url = trainingApi.getTrainingExportUrl();
    if (url) {
      Linking.openURL(url).catch(() => {});
    }
  }, []);

  const exportable = stats?.exportable ?? 0;
  const isMemory = stats?.mode === "memory";
  const modeLabel = stats?.mode === "mongo" ? "MONGO" : stats?.mode === "memory" ? "MEMORY" : "";

  return (
    <View style={styles.root} testID="training-data-screen">
      <SafeAreaView style={{ flex: 1 }} edges={["top", "bottom"]}>
        {/* Header */}
        <View style={styles.header}>
          <View style={styles.headerTop}>
            <Pressable
              onPress={() => router.back()}
              style={({ pressed }) => [styles.backBtn, pressed && { opacity: 0.7 }]}
            >
              <MaterialCommunityIcons name="arrow-left" size={20} color={colors.onSurface} />
            </Pressable>
            <View style={styles.headerTitleBlock}>
              <Text style={styles.headerTitle}>SENTINEL DATASET LAB</Text>
              <Text style={styles.headerSubtitle}>Indian-road VLM sample review</Text>
            </View>
            <View style={{ width: 28 }} />
          </View>
          <View style={styles.headerMetaRow}>
            {modeLabel ? (
              <View style={styles.modeBadge}>
                <Text style={styles.modeBadgeText}>{modeLabel}</Text>
              </View>
            ) : null}
            {isMemory && (
              <View style={styles.memoryNotice} testID="training-memory-mode-notice">
                <MaterialCommunityIcons name="database-alert" size={14} color={colors.warning} />
                <Text style={styles.memoryNoticeText}>
                  Temporary storage · data resets with backend restart
                </Text>
              </View>
            )}
          </View>
        </View>

        {/* Stats */}
        {stats && <TrainingStatsComp stats={stats} />}

        {/* Export */}
        <View style={styles.exportRow}>
          <Pressable
            onPress={onExport}
            disabled={exportable === 0}
            style={({ pressed }) => [
              styles.exportBtn,
              exportable === 0 && styles.exportBtnDisabled,
              pressed && exportable > 0 && { opacity: 0.85 },
            ]}
            testID="training-export-button"
          >
            <MaterialCommunityIcons name="download" size={16} color={exportable > 0 ? "#000" : colors.onSurfaceTertiary} />
            <Text style={[styles.exportBtnText, exportable === 0 && { color: colors.onSurfaceTertiary }]}>
              Export Verified JSONL ({exportable})
            </Text>
          </Pressable>
          {exportable === 0 && (
            <Text style={styles.exportHint}>No verified samples to export</Text>
          )}
        </View>

        {/* Filters */}
        <View style={styles.filterRow}>
          {FILTERS.map((f) => (
            <Pressable
              key={f.key}
              onPress={() => setActiveFilter(f.key)}
              style={[
                styles.filterChip,
                activeFilter === f.key && styles.filterChipActive,
              ]}
              testID={f.testId}
            >
              <Text
                style={[
                  styles.filterChipText,
                  activeFilter === f.key && styles.filterChipTextActive,
                ]}
              >
                {f.label}
              </Text>
            </Pressable>
          ))}
        </View>

        {/* Success message */}
        {successMsg && (
          <View style={styles.successBanner}>
            <Text style={styles.successBannerText}>{successMsg}</Text>
          </View>
        )}

        {/* Content */}
        {loading && !refreshing && (
          <View style={styles.center} testID="training-data-loading">
            <ActivityIndicator color={colors.brand} />
            <Text style={styles.loadingText}>Loading dataset…</Text>
          </View>
        )}

        {error && (
          <View style={styles.center} testID="training-data-error">
            <MaterialCommunityIcons name="cloud-off-outline" size={32} color={colors.error} />
            <Text style={styles.errorText}>{error}</Text>
            <Pressable
              onPress={() => fetchAll()}
              style={({ pressed }) => [styles.retryBtn, pressed && { opacity: 0.85 }]}
              testID="training-data-retry"
            >
              <Text style={styles.retryBtnText}>Retry</Text>
            </Pressable>
          </View>
        )}

        {!loading && !error && (
          <ScrollView
            style={{ flex: 1 }}
            contentContainerStyle={{ paddingBottom: Math.max(insets.bottom, spacing.lg) }}
            refreshControl={
              <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.brand} />
            }
            testID="training-data-list"
          >
            {samples.length === 0 ? (
              <View style={styles.empty}>
                <MaterialCommunityIcons name="database-check" size={32} color={colors.onSurfaceTertiary} />
                <Text style={styles.emptyText}>No samples found</Text>
              </View>
            ) : (
              <>
                {samples.map((sample) => (
                  <React.Fragment key={sample.sampleId}>
                    <TrainingSampleCard
                      sample={sample}
                      selected={selectedId === sample.sampleId}
                      onPress={() =>
                        setSelectedId((prev) =>
                          prev === sample.sampleId ? null : sample.sampleId
                        )
                      }
                    />
                    {selectedId === sample.sampleId && (
                      <TrainingSampleReview
                        sample={sample}
                        onFeedback={(payload) => onFeedback(sample.sampleId, payload)}
                        loading={mutationLoading}
                        error={mutationError}
                      />
                    )}
                  </React.Fragment>
                ))}
              </>
            )}
          </ScrollView>
        )}
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: spacing.xl,
  },
  loadingText: {
    color: colors.onSurfaceSecondary,
    marginTop: spacing.md,
    fontSize: fonts.size.sm,
    letterSpacing: 1,
  },
  header: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  headerTop: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  backBtn: { padding: 4 },
  headerTitleBlock: { alignItems: "center", flex: 1 },
  headerTitle: {
    color: colors.onSurface,
    fontSize: fonts.size.lg,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
  headerSubtitle: {
    color: colors.onSurfaceTertiary,
    fontSize: 11,
    marginTop: 2,
    letterSpacing: 0.5,
  },
  headerMetaRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    marginTop: spacing.sm,
    flexWrap: "wrap",
  },
  modeBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 4,
    backgroundColor: colors.surfaceTertiary,
    borderWidth: 1,
    borderColor: colors.border,
  },
  modeBadgeText: {
    color: colors.onSurfaceSecondary,
    fontSize: 10,
    fontWeight: "700",
    letterSpacing: 0.5,
  },
  memoryNotice: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    padding: spacing.sm,
    backgroundColor: "rgba(210,153,34,0.08)",
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.warning + "59",
    flex: 1,
  },
  memoryNoticeText: {
    color: colors.warning,
    fontSize: 11,
    fontWeight: "500",
  },
  exportRow: {
    marginHorizontal: spacing.lg,
    marginTop: spacing.md,
    marginBottom: spacing.sm,
  },
  exportBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingVertical: 12,
    borderRadius: radius.sm,
    backgroundColor: colors.brand,
  },
  exportBtnDisabled: {
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
  },
  exportBtnText: {
    color: "#000",
    fontSize: 12,
    fontWeight: "600",
    letterSpacing: 0.5,
  },
  exportHint: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    textAlign: "center",
    marginTop: 4,
  },
  filterRow: {
    flexDirection: "row",
    gap: spacing.sm,
    paddingHorizontal: spacing.lg,
    marginBottom: spacing.sm,
  },
  filterChip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: radius.pill,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
  },
  filterChipActive: {
    backgroundColor: "rgba(0,240,255,0.08)",
    borderColor: colors.brand,
  },
  filterChipText: {
    color: colors.onSurfaceSecondary,
    fontSize: 11,
    fontWeight: "500",
  },
  filterChipTextActive: {
    color: colors.brand,
    fontWeight: "600",
  },
  successBanner: {
    marginHorizontal: spacing.lg,
    marginBottom: spacing.sm,
    padding: spacing.sm,
    backgroundColor: "rgba(35,197,94,0.08)",
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.success + "59",
  },
  successBannerText: {
    color: colors.success,
    fontSize: 11,
    fontWeight: "600",
    textAlign: "center",
  },
  empty: {
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: spacing.xxxl,
  },
  emptyText: {
    color: colors.onSurfaceTertiary,
    fontSize: fonts.size.sm,
    marginTop: spacing.sm,
  },
  errorText: {
    color: colors.error,
    fontSize: fonts.size.sm,
    textAlign: "center",
    marginTop: spacing.md,
    marginBottom: spacing.md,
  },
  retryBtn: {
    paddingHorizontal: spacing.xl,
    paddingVertical: spacing.sm,
    borderRadius: radius.md,
    backgroundColor: colors.brand,
  },
  retryBtnText: {
    color: "#000",
    fontSize: 12,
    fontWeight: "600",
  },
});

import React, { useState, useEffect, useRef } from "react";
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  ActivityIndicator,
  ScrollView,
  Animated,
  StatusBar,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import * as Haptics from "expo-haptics";
import { colors, spacing, radius, fonts } from "@/src/theme";
import { demoReplayApi } from "@/src/api/demoReplay";
import type {
  DemoReplayStatus,
  DemoReplaySample,
  DemoReplayInferenceResponse,
} from "@/src/types/demoReplay";
import ReplayImagePair from "@/src/components/replay/ReplayImagePair";
import ReplayPredictionPanel from "@/src/components/replay/ReplayPredictionPanel";
import ReplayWarningPanel from "@/src/components/replay/ReplayWarningPanel";

export default function DemoReplayScreen() {
  const router = useRouter();

  // Status and current sample state
  const [status, setStatus] = useState<DemoReplayStatus | null>(null);
  const [currentSample, setCurrentSample] = useState<DemoReplaySample | null>(null);
  const [sampleCount, setSampleCount] = useState(0);
  const [currentIndex, setCurrentIndex] = useState(0);

  // UI state
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [inferenceLoading, setInferenceLoading] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [loopNotification, setLoopNotification] = useState<string | null>(null);

  // Inference state
  const [inferenceResult, setInferenceResult] = useState<DemoReplayInferenceResponse | null>(null);

  // Animation values for Advance transition
  const progressAnim = useRef(new Animated.Value(0)).current;
  const [isAdvancing, setIsAdvancing] = useState(false);

  const isMounted = useRef(true);
  const advanceTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const getErrorMessage = (err: unknown): string => {
    if (err instanceof Error) return err.message;
    if (err && typeof err === "object" && "message" in err) {
      return String((err as Record<string, unknown>).message);
    }
    return String(err);
  };

  useEffect(() => {
    isMounted.current = true;
    return () => {
      isMounted.current = false;
      if (advanceTimeoutRef.current) {
        clearTimeout(advanceTimeoutRef.current);
      }
    };
  }, []);

  // Fetch status and current sample
  const loadData = async () => {
    setLoading(true);
    setErrorText(null);
    try {
      const statusRes = await demoReplayApi.getStatus();
      if (!isMounted.current) return;
      setStatus(statusRes);

      if (statusRes.status === "ready") {
        const currentRes = await demoReplayApi.getCurrent();
        if (!isMounted.current) return;
        if (currentRes) {
          setCurrentSample(currentRes.sample);
          setSampleCount(currentRes.sampleCount);
          setCurrentIndex(currentRes.currentIndex);
        }
      }
    } catch (err: unknown) {
      if (isMounted.current) {
        setErrorText(getErrorMessage(err));
      }
    } finally {
      if (isMounted.current) {
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleRunInference = async () => {
    if (!currentSample || inferenceLoading || isAdvancing || actionLoading) return;
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    setInferenceLoading(true);
    setErrorText(null);
    try {
      const res = await demoReplayApi.infer(currentSample.sampleId, true);
      if (isMounted.current) {
        setInferenceResult(res);
      }
    } catch (err: unknown) {
      if (isMounted.current) {
        setErrorText(getErrorMessage(err));
      }
    } finally {
      if (isMounted.current) {
        setInferenceLoading(false);
      }
    }
  };

  const handleAdvance = async () => {
    if (isAdvancing || actionLoading || inferenceLoading) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    setIsAdvancing(true);
    setInferenceResult(null); // Clear previous UI
    setLoopNotification(null);

    // 1-1.5s Transition animation
    progressAnim.setValue(0);
    Animated.timing(progressAnim, {
      toValue: 1,
      duration: 1200,
      useNativeDriver: false,
    }).start();

    if (advanceTimeoutRef.current) {
      clearTimeout(advanceTimeoutRef.current);
    }

    advanceTimeoutRef.current = setTimeout(async () => {
      try {
        const res = await demoReplayApi.advance();
        if (!isMounted.current) return;
        if (res) {
          setCurrentSample(res.sample);
          setCurrentIndex(res.currentIndex);
          setSampleCount(res.sampleCount);

          if (res.looped) {
            setLoopNotification("ROUTE LOOP COMPLETE — RETURNED TO SAMPLE 1");
          }
        }
      } catch (err: unknown) {
        if (isMounted.current) {
          setErrorText(getErrorMessage(err));
        }
      } finally {
        if (isMounted.current) {
          setIsAdvancing(false);
        }
      }
    }, 1200);
  };

  const handleReset = async () => {
    if (actionLoading || isAdvancing || inferenceLoading) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
    setActionLoading(true);
    setInferenceResult(null);
    setLoopNotification(null);
    try {
      const res = await demoReplayApi.reset();
      if (!isMounted.current) return;
      if (res) {
        setCurrentSample(res.sample);
        setCurrentIndex(res.currentIndex);
        setSampleCount(res.sampleCount);
      }
    } catch (err: unknown) {
      if (isMounted.current) {
        setErrorText(getErrorMessage(err));
      }
    } finally {
      if (isMounted.current) {
        setActionLoading(false);
      }
    }
  };

  // Helper for rendering image URLs safely
  const getImageUrl = (view: "dashcam" | "topview") => {
    if (!currentSample) return null;
    return view === "dashcam"
      ? demoReplayApi.getDashcamUrl(currentSample.sampleId)
      : demoReplayApi.getTopviewUrl(currentSample.sampleId);
  };

  const renderContent = () => {
    if (loading) {
      return (
        <View style={styles.centerContainer}>
          <ActivityIndicator size="large" color={colors.brand} />
          <Text style={styles.loadingText}>Initializing replay engine...</Text>
        </View>
      );
    }

    if (errorText) {
      return (
        <View style={styles.centerContainer} testID="demo-replay-error">
          <MaterialCommunityIcons name="alert-octagon" size={48} color={colors.error} />
          <Text style={styles.errorTitle}>SYSTEM ERROR</Text>
          <Text style={styles.errorDescription}>{errorText}</Text>
          <Pressable style={styles.retryButton} onPress={loadData}>
            <Text style={styles.retryButtonText}>RETRY CONNECTION</Text>
          </Pressable>
        </View>
      );
    }

    if (!status || status.status === "unconfigured") {
      return (
        <View style={styles.centerContainer}>
          <MaterialCommunityIcons name="folder-alert" size={48} color={colors.warning} />
          <Text style={styles.unconfiguredTitle}>REPLAY UNCONFIGURED</Text>
          <Text style={styles.unconfiguredDescription}>
            Replay manifest or research assets are not configured. Copy paired images into:{"\n"}
            <Text style={styles.codeText}>backend/demo_scenarios/sample_001/...</Text>{"\n"}
            and configure a valid <Text style={styles.codeText}>manifest.json</Text>.
          </Text>
          <Pressable style={styles.retryButton} onPress={loadData}>
            <Text style={styles.retryButtonText}>CHECK STATUS AGAIN</Text>
          </Pressable>
        </View>
      );
    }

    if (status.status === "invalid") {
      return (
        <View style={styles.centerContainer} testID="demo-replay-error">
          <MaterialCommunityIcons name="file-document-remove" size={48} color={colors.error} />
          <Text style={styles.errorTitle}>INVALID MANIFEST</Text>
          <Text style={styles.errorDescription}>
            The manifest file is present but failed validation checks (e.g. duplicate indexes, absolute paths, unsafe characters).
          </Text>
          <Pressable style={styles.retryButton} onPress={loadData}>
            <Text style={styles.retryButtonText}>RELOAD MANIFEST</Text>
          </Pressable>
        </View>
      );
    }

    return (
      <View style={styles.contentWrap}>
        {/* Scenario Header */}
        {currentSample && (
          <View style={styles.scenarioCard}>
            <View style={styles.scenarioHeaderRow}>
              <Text style={styles.sampleIndexText}>
                SAMPLE {currentIndex + 1} OF {sampleCount}
              </Text>
              {currentSample.tags.map((tag) => (
                <View key={tag} style={styles.tagPill}>
                  <Text style={styles.tagText}>{tag.toUpperCase()}</Text>
                </View>
              ))}
            </View>
            <Text style={styles.scenarioTitle}>{currentSample.title}</Text>
            <Text style={styles.scenarioDesc}>{currentSample.description}</Text>
          </View>
        )}

        {/* Synchronized views */}
        <ReplayImagePair
          dashcamUrl={getImageUrl("dashcam")}
          topviewUrl={getImageUrl("topview")}
        />

        {/* Loop complete notification */}
        {loopNotification && (
          <View style={styles.loopPill}>
            <MaterialCommunityIcons name="sync" size={16} color={colors.brand} />
            <Text style={styles.loopText}>{loopNotification}</Text>
          </View>
        )}

        {/* Transition Loading State */}
        {isAdvancing && (
          <View style={styles.transitionContainer} testID="demo-replay-inference-loading">
            <Text style={styles.transitionText}>ADVANCING VEHICLE...</Text>
            <View style={styles.progressBarBg}>
              <Animated.View
                style={[
                  styles.progressBarFill,
                  {
                    width: progressAnim.interpolate({
                      inputRange: [0, 1],
                      outputRange: ["0%", "100%"],
                    }),
                  },
                ]}
              />
            </View>
          </View>
        )}

        {/* Run perception loading state */}
        {inferenceLoading && (
          <View style={styles.inferenceLoadingCard} testID="demo-replay-inference-loading">
            <ActivityIndicator size="small" color={colors.brand} />
            <Text style={styles.inferenceLoadingText}>RUNNING STRUCTURED QWEN PERCEPTION...</Text>
          </View>
        )}

        {/* Predictions Panel */}
        {inferenceResult && <ReplayPredictionPanel inference={inferenceResult} />}

        {/* Warning Panel */}
        {inferenceResult && <ReplayWarningPanel inference={inferenceResult} />}

        {/* Action Controls */}
        <View style={styles.controlsGrid}>
          <Pressable
            onPress={handleRunInference}
            disabled={inferenceLoading || isAdvancing || actionLoading}
            style={({ pressed }) => [
              styles.primaryBtn,
              (inferenceLoading || isAdvancing || actionLoading) && styles.disabledBtn,
              pressed && !inferenceLoading && !isAdvancing && !actionLoading && { opacity: 0.85 },
            ]}
            testID="demo-replay-run-inference"
          >
            <MaterialCommunityIcons name="brain" size={18} color="#000" />
            <Text style={styles.primaryBtnText}>RUN SENTINEL PERCEPTION</Text>
          </Pressable>

          <View style={styles.secondaryControlsRow}>
            <Pressable
              onPress={handleAdvance}
              disabled={isAdvancing || actionLoading || inferenceLoading}
              style={({ pressed }) => [
                styles.secondaryBtn,
                (isAdvancing || actionLoading || inferenceLoading) && styles.disabledBtn,
                pressed && !isAdvancing && !actionLoading && !inferenceLoading && { opacity: 0.85 },
              ]}
              testID="demo-replay-advance"
            >
              <MaterialCommunityIcons name="chevron-double-right" size={16} color={colors.onSurface} />
              <Text style={styles.secondaryBtnText}>ADVANCE VEHICLE</Text>
            </Pressable>

            <Pressable
              onPress={handleReset}
              disabled={isAdvancing || actionLoading || inferenceLoading}
              style={({ pressed }) => [
                styles.secondaryBtn,
                (isAdvancing || actionLoading || inferenceLoading) && styles.disabledBtn,
                pressed && !isAdvancing && !actionLoading && !inferenceLoading && { opacity: 0.85 },
              ]}
              testID="demo-replay-reset"
            >
              <MaterialCommunityIcons name="refresh" size={16} color={colors.onSurface} />
              <Text style={styles.secondaryBtnText}>RESET REPLAY</Text>
            </Pressable>
          </View>

          <View style={styles.navRow}>
            <Pressable
              onPress={() => {
                Haptics.selectionAsync().catch(() => {});
                router.push("/ghost-vision");
              }}
              style={({ pressed }) => [styles.navBtn, pressed && { opacity: 0.85 }]}
            >
              <MaterialCommunityIcons name="map-marker-path" size={14} color={colors.brand} />
              <Text style={styles.navBtnText}>VIEW PROVENANCE GRAPH</Text>
            </Pressable>

            <Pressable
              onPress={() => {
                Haptics.selectionAsync().catch(() => {});
                // @ts-expect-error training-data route exists in app directory
                router.push("/training-data");
              }}
              style={({ pressed }) => [styles.navBtn, pressed && { opacity: 0.85 }]}
            >
              <MaterialCommunityIcons name="database-edit" size={14} color={colors.brand} />
              <Text style={styles.navBtnText}>OPEN DATASET LAB</Text>
            </Pressable>
          </View>
        </View>
      </View>
    );
  };

  return (
    <View style={styles.root} testID="demo-replay-screen">
      <StatusBar barStyle="light-content" />
      <SafeAreaView style={{ flex: 1 }} edges={["top", "bottom"]}>
        {/* Header */}
        <View style={styles.header}>
          <Pressable
            onPress={() => {
              Haptics.selectionAsync().catch(() => {});
              router.back();
            }}
            style={({ pressed }) => [styles.backBtn, pressed && { opacity: 0.7 }]}
          >
            <MaterialCommunityIcons name="arrow-left" size={20} color={colors.onSurface} />
          </Pressable>
          <View style={styles.titleContainer}>
            <Text style={styles.titleText}>SENTINEL ROAD REPLAY</Text>
            <View style={styles.modeBadge}>
              <Text style={styles.modeBadgeText}>DATASET REPLAY MODE</Text>
            </View>
          </View>
        </View>

        {/* Scroll Body */}
        <ScrollView
          style={styles.scroll}
          contentContainerStyle={styles.scrollContent}
          showsVerticalScrollIndicator={false}
        >
          {renderContent()}
        </ScrollView>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.surface,
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    backgroundColor: colors.surfaceSecondary,
  },
  backBtn: {
    padding: spacing.xs,
    marginRight: spacing.sm,
  },
  titleContainer: {
    flex: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  titleText: {
    color: colors.onSurface,
    fontSize: fonts.size.base,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 1.5,
  },
  modeBadge: {
    backgroundColor: "rgba(0, 240, 255, 0.1)",
    borderWidth: 1,
    borderColor: colors.brand,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  modeBadgeText: {
    color: colors.brand,
    fontSize: fonts.size.sm - 3,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 0.5,
  },
  scroll: {
    flex: 1,
  },
  scrollContent: {
    padding: spacing.md,
    flexGrow: 1,
  },
  centerContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    padding: spacing.xl,
    gap: spacing.md,
  },
  loadingText: {
    color: colors.onSurfaceSecondary,
    fontSize: fonts.size.base,
    fontFamily: fonts.family,
  },
  errorTitle: {
    color: colors.error,
    fontSize: fonts.size.lg,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 1,
  },
  errorDescription: {
    color: colors.onSurfaceSecondary,
    fontSize: fonts.size.base,
    fontFamily: fonts.family,
    textAlign: "center",
    lineHeight: 20,
  },
  unconfiguredTitle: {
    color: colors.warning,
    fontSize: fonts.size.lg,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 1,
  },
  unconfiguredDescription: {
    color: colors.onSurfaceSecondary,
    fontSize: fonts.size.base,
    fontFamily: fonts.family,
    textAlign: "center",
    lineHeight: 22,
  },
  codeText: {
    fontFamily: "Courier",
    color: colors.onSurface,
    backgroundColor: colors.surfaceSecondary,
    paddingHorizontal: 4,
  },
  retryButton: {
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    borderRadius: radius.md,
    paddingHorizontal: spacing.xl,
    paddingVertical: spacing.md,
    marginTop: spacing.md,
  },
  retryButtonText: {
    color: colors.onSurface,
    fontSize: fonts.size.sm,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 1,
  },
  contentWrap: {
    gap: spacing.md,
    flex: 1,
  },
  scenarioCard: {
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
    padding: spacing.md,
    gap: spacing.xs,
  },
  scenarioHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
  },
  sampleIndexText: {
    color: colors.brand,
    fontSize: fonts.size.sm - 2,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 1,
  },
  tagPill: {
    backgroundColor: colors.surfaceTertiary,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderWidth: 1,
    borderColor: colors.border,
  },
  tagText: {
    color: colors.onSurfaceTertiary,
    fontSize: fonts.size.sm - 3,
    fontFamily: fonts.family,
    fontWeight: "bold",
  },
  scenarioTitle: {
    color: colors.onSurface,
    fontSize: fonts.size.lg,
    fontFamily: fonts.family,
    fontWeight: "bold",
  },
  scenarioDesc: {
    color: colors.onSurfaceSecondary,
    fontSize: fonts.size.base,
    fontFamily: fonts.family,
    lineHeight: 20,
  },
  loopPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: "rgba(0, 240, 255, 0.05)",
    borderWidth: 1,
    borderColor: "rgba(0, 240, 255, 0.2)",
    borderRadius: radius.md,
    padding: spacing.md,
    justifyContent: "center",
  },
  loopText: {
    color: colors.brand,
    fontSize: fonts.size.sm,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 0.5,
    textAlign: "center",
  },
  transitionContainer: {
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.md,
    gap: spacing.sm,
  },
  transitionText: {
    color: colors.onSurface,
    fontSize: fonts.size.sm,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 1,
    textAlign: "center",
  },
  progressBarBg: {
    height: 6,
    backgroundColor: colors.surfaceTertiary,
    borderRadius: radius.pill,
    overflow: "hidden",
  },
  progressBarFill: {
    height: "100%",
    backgroundColor: colors.brand,
  },
  inferenceLoadingCard: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.md,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.md,
  },
  inferenceLoadingText: {
    color: colors.brand,
    fontSize: fonts.size.sm - 1,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 0.5,
  },
  controlsGrid: {
    gap: spacing.sm,
    marginTop: spacing.sm,
    marginBottom: spacing.lg,
  },
  primaryBtn: {
    backgroundColor: colors.brand,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingVertical: spacing.lg,
    borderRadius: radius.md,
    minHeight: 52,
  },
  primaryBtnText: {
    color: "#000",
    fontSize: fonts.size.base,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 1,
  },
  secondaryControlsRow: {
    flexDirection: "row",
    gap: spacing.sm,
  },
  secondaryBtn: {
    flex: 1,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
    minHeight: 46,
  },
  secondaryBtnText: {
    color: colors.onSurface,
    fontSize: fonts.size.sm,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 0.5,
  },
  disabledBtn: {
    opacity: 0.5,
  },
  navRow: {
    flexDirection: "row",
    gap: spacing.sm,
    marginTop: spacing.xs,
  },
  navBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.xs,
    paddingVertical: spacing.sm,
  },
  navBtnText: {
    color: colors.brand,
    fontSize: fonts.size.sm - 1,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 0.5,
  },
});

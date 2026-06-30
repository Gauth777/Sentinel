import React, { useState, useCallback, useRef, useEffect } from "react";
import { View, Text, Pressable, StyleSheet, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { colors, spacing, radius, fonts } from "@/src/theme";
import { mediaApi } from "@/src/api/media";
import { ApiError } from "@/src/api/sentinel";
import { useSentinelLocation } from "@/src/hooks/useSentinelLocation";
import type { CaptureState, CapturedMedia, MediaTelemetry } from "@/src/types/media";
import ObservationCamera from "@/src/components/capture/ObservationCamera";
import CapturePreview from "@/src/components/capture/CapturePreview";

export default function CaptureObservationScreen() {
  const router = useRouter();
  const mountedRef = useRef(true);

  // Use the same location hook as Ghost Vision for real telemetry
  const { location, headingDegrees, speedKmh, mode: locationMode } = useSentinelLocation();

  const [captureState, setCaptureState] = useState<CaptureState>("camera_ready");
  const [capturedMedia, setCapturedMedia] = useState<CapturedMedia | null>(null);
  const [telemetry, setTelemetry] = useState<MediaTelemetry | null>(null);
  const [uploadResult, setUploadResult] = useState<import("@/src/types/media").MediaUploadResponse | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const safeSetState = useCallback((setter: () => void) => {
    if (mountedRef.current) {
      setter();
    }
  }, []);

  const handleCapture = useCallback((media: CapturedMedia) => {
    const hasLiveLocation = locationMode === "live" && location !== null;
    const telem: MediaTelemetry = {
      location: hasLiveLocation
        ? { latitude: location.latitude, longitude: location.longitude }
        : null,
      headingDegrees: headingDegrees ?? null,
      speedKmh: speedKmh && speedKmh > 0 ? speedKmh : null,
      capturedAt: media.capturedAt,
      telemetrySource: hasLiveLocation ? "live" : "unavailable",
    };

    safeSetState(() => {
      setCapturedMedia(media);
      setTelemetry(telem);
      setCaptureState("preview");
    });
  }, [location, headingDegrees, speedKmh, locationMode, safeSetState]);

  const handleRetake = useCallback(() => {
    safeSetState(() => {
      setCapturedMedia(null);
      setUploadResult(null);
      setUploadError(null);
      setCaptureState("camera_ready");
    });
  }, [safeSetState]);

  const handleUpload = useCallback(async () => {
    if (!capturedMedia) return;
    safeSetState(() => {
      setCaptureState("uploading");
      setUploadError(null);
    });

    try {
      const result = await mediaApi.uploadMedia({
        fileUri: capturedMedia.uri,
        mimeType: capturedMedia.mimeType,
        fileName: `sentinel_capture_${Date.now()}.jpg`,
        telemetry,
      });
      if (!mountedRef.current) return;
      safeSetState(() => {
        setUploadResult(result);
        setCaptureState("upload_success");
      });
    } catch (err: unknown) {
      if (!mountedRef.current) return;
      const msg = err instanceof ApiError ? err.message : String(err);
      safeSetState(() => {
        setUploadError(msg);
        setCaptureState("upload_error");
      });
    }
  }, [capturedMedia, telemetry, safeSetState]);

  const handleRetry = useCallback(() => {
    safeSetState(() => {
      setUploadError(null);
      setCaptureState("preview");
    });
  }, [safeSetState]);

  return (
    <View style={styles.root} testID="capture-observation-screen">
      <SafeAreaView style={{ flex: 1 }} edges={["top", "bottom"]}>
        <View style={styles.header}>
          <Pressable
            onPress={() => router.back()}
            style={({ pressed }) => [styles.backBtn, pressed && { opacity: 0.7 }]}
          >
            <MaterialCommunityIcons name="arrow-left" size={20} color={colors.onSurface} />
          </Pressable>
          <Text style={styles.headerTitle}>Capture Road Image</Text>
          <View style={{ width: 28 }} />
        </View>

        {captureState === "camera_ready" && !capturedMedia && (
          <ObservationCamera onCapture={handleCapture} />
        )}

        {captureState === "preview" && capturedMedia && (
          <CapturePreview
            media={capturedMedia}
            telemetry={telemetry}
            onRetake={handleRetake}
            onUse={handleUpload}
          />
        )}

        {captureState === "uploading" && (
          <View style={styles.center} testID="capture-upload-loading">
            <ActivityIndicator size="large" color={colors.brand} />
            <Text style={styles.loadingText}>Uploading image…</Text>
          </View>
        )}

        {captureState === "upload_success" && uploadResult && (
          <View style={styles.center} testID="capture-upload-success">
            <MaterialCommunityIcons name="check-circle" size={48} color={colors.success} />
            <Text style={styles.successTitle}>Media Stored</Text>
            <View style={styles.resultPanel}>
              <Text style={styles.resultLabel}>Media ID</Text>
              <Text style={styles.resultValue}>{uploadResult.mediaId}</Text>
              <Text style={styles.resultLabel}>MIME Type</Text>
              <Text style={styles.resultValue}>{uploadResult.mimeType}</Text>
              <Text style={styles.resultLabel}>Size</Text>
              <Text style={styles.resultValue}>{(uploadResult.sizeBytes / 1024).toFixed(1)} KB</Text>
              <Text style={styles.resultLabel}>Storage</Text>
              <Text style={styles.resultValue}>{uploadResult.storageMode}</Text>
            </View>
            <Text style={styles.successHint}>
              Image stored and ready for future inference.
            </Text>
            <View style={styles.successActions}>
              <Pressable
                onPress={handleRetake}
                style={({ pressed }) => [styles.btnGhost, pressed && { opacity: 0.85 }]}
              >
                <Text style={styles.btnGhostText}>Capture Another</Text>
              </Pressable>
              <Pressable
                onPress={() => router.back()}
                style={({ pressed }) => [styles.btnPrimary, pressed && { opacity: 0.85 }]}
              >
                <Text style={styles.btnPrimaryText}>Back to Ghost Vision</Text>
              </Pressable>
            </View>
          </View>
        )}

        {captureState === "upload_error" && (
          <View style={styles.center} testID="capture-upload-error">
            <MaterialCommunityIcons name="cloud-off-outline" size={40} color={colors.error} />
            <Text style={styles.errorText}>{uploadError || "Upload failed"}</Text>
            <Pressable
              onPress={handleRetry}
              style={({ pressed }) => [styles.retryBtn, pressed && { opacity: 0.85 }]}
              testID="capture-upload-retry"
            >
              <Text style={styles.retryBtnText}>Retry</Text>
            </Pressable>
          </View>
        )}
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  backBtn: { padding: 4 },
  headerTitle: {
    color: colors.onSurface,
    fontSize: fonts.size.lg,
    fontWeight: "700",
    letterSpacing: 1.5,
  },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: spacing.xl,
  },
  loadingText: {
    color: colors.onSurfaceSecondary,
    marginTop: spacing.md,
    fontSize: 12,
  },
  successTitle: {
    color: colors.onSurface,
    fontSize: 18,
    fontWeight: "700",
    marginTop: spacing.md,
    marginBottom: spacing.lg,
  },
  resultPanel: {
    width: "100%",
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.md,
    marginBottom: spacing.lg,
  },
  resultLabel: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    letterSpacing: 0.5,
    marginTop: spacing.xs,
  },
  resultValue: {
    color: colors.onSurface,
    fontSize: 12,
    fontWeight: "500",
    marginBottom: 2,
  },
  successHint: {
    color: colors.onSurfaceTertiary,
    fontSize: 11,
    textAlign: "center",
    marginBottom: spacing.lg,
  },
  successActions: {
    flexDirection: "row",
    gap: spacing.sm,
    width: "100%",
  },
  errorText: {
    color: colors.error,
    fontSize: 12,
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
  btnGhost: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: radius.sm,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: colors.border,
  },
  btnGhostText: {
    color: colors.onSurfaceSecondary,
    fontSize: 12,
    fontWeight: "600",
  },
  btnPrimary: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: radius.sm,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.brand,
  },
  btnPrimaryText: {
    color: "#000",
    fontSize: 12,
    fontWeight: "600",
  },
});

import React, { useRef, useCallback, useState, useEffect } from "react";
import { View, Text, Pressable, StyleSheet, ActivityIndicator, Linking } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, spacing, radius } from "@/src/theme";
import type { CapturedMedia } from "@/src/types/media";

type Props = {
  onCapture: (media: CapturedMedia) => void;
  disabled?: boolean;
};

export default function ObservationCamera({ onCapture, disabled }: Props) {
  const [permission, requestPermission] = useCameraPermissions();
  const [captureError, setCaptureError] = useState<string | null>(null);
  const [isCapturing, setIsCapturing] = useState(false);
  const [permissionError, setPermissionError] = useState<string | null>(null);
  const cameraRef = useRef<CameraView>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const handleCapture = useCallback(async () => {
    if (isCapturing || disabled || !cameraRef.current) return;
    setCaptureError(null);
    setIsCapturing(true);
    try {
      const photo = await cameraRef.current.takePictureAsync({
        quality: 0.7,
        base64: false,
      });
      if (!mountedRef.current) return;
      if (photo?.uri) {
        const media: CapturedMedia = {
          uri: photo.uri,
          width: typeof photo.width === "number" ? photo.width : 0,
          height: typeof photo.height === "number" ? photo.height : 0,
          mimeType: "image/jpeg",
          capturedAt: new Date().toISOString(),
        };
        onCapture(media);
      }
    } catch (err: unknown) {
      if (!mountedRef.current) return;
      const msg = err instanceof Error ? err.message : String(err);
      setCaptureError(msg);
      console.warn("[Sentinel] capture failed:", msg);
    } finally {
      if (mountedRef.current) {
        setIsCapturing(false);
      }
    }
  }, [onCapture, disabled, isCapturing]);

  const handleRetry = useCallback(() => {
    setCaptureError(null);
  }, []);

  const handleRequestPermission = useCallback(async () => {
    setPermissionError(null);
    try {
      const result = await requestPermission();
      if (!mountedRef.current) return;
      if (!result.granted) {
        setPermissionError("Camera permission was denied.");
      }
    } catch (err: unknown) {
      if (!mountedRef.current) return;
      const msg = err instanceof Error ? err.message : String(err);
      setPermissionError(`Permission request failed: ${msg}`);
    }
  }, [requestPermission]);

  const handleOpenSettings = useCallback(() => {
    Linking.openSettings().catch(() => {});
  }, []);

  // Permission loading
  if (!permission) {
    return (
      <View style={styles.center} testID="capture-camera">
        <ActivityIndicator color={colors.brand} />
        <Text style={styles.loadingText}>Checking camera access…</Text>
      </View>
    );
  }

  // Permission denied
  if (!permission.granted) {
    const canAskAgain = permission.canAskAgain;
    return (
      <View style={styles.center} testID="capture-permission-denied">
        <MaterialCommunityIcons name="camera-off" size={40} color={colors.onSurfaceTertiary} />
        <Text style={styles.permissionTitle}>Camera access required</Text>
        <Text style={styles.permissionText}>
          Camera access is needed to capture road evidence for the dataset.
        </Text>
        {permissionError && (
          <Text style={styles.permissionError}>{permissionError}</Text>
        )}
        {canAskAgain ? (
          <Pressable
            onPress={handleRequestPermission}
            style={({ pressed }) => [styles.permissionBtn, pressed && { opacity: 0.85 }]}
            testID="capture-request-permission"
          >
            <Text style={styles.permissionBtnText}>Request Camera Access</Text>
          </Pressable>
        ) : (
          <Pressable
            onPress={handleOpenSettings}
            style={({ pressed }) => [styles.permissionBtn, pressed && { opacity: 0.85 }]}
            testID="capture-open-settings"
          >
            <Text style={styles.permissionBtnText}>Open Settings</Text>
          </Pressable>
        )}
      </View>
    );
  }

  return (
    <View style={styles.container} testID="capture-camera">
      <CameraView style={styles.camera} ref={cameraRef} facing="back" mode="picture" />
      <View style={styles.overlay}>
        <View style={styles.topBar}>
          <Text style={styles.topBarText}>Rear camera · tap shutter to capture</Text>
        </View>
        <View style={styles.bottomBar}>
          {isCapturing ? (
            <View style={styles.capturingIndicator}>
              <ActivityIndicator color={colors.brand} />
              <Text style={styles.capturingText}>Capturing image…</Text>
            </View>
          ) : captureError ? (
            <View style={styles.errorContainer} testID="capture-camera-error">
              <MaterialCommunityIcons name="alert-circle" size={20} color={colors.error} />
              <Text style={styles.errorText}>{captureError}</Text>
              <Pressable
                onPress={handleRetry}
                style={({ pressed }) => [styles.retryBtn, pressed && { opacity: 0.85 }]}
                testID="capture-camera-retry"
              >
                <Text style={styles.retryBtnText}>Retry</Text>
              </Pressable>
            </View>
          ) : (
            <Pressable
              onPress={handleCapture}
              disabled={disabled}
              style={({ pressed }) => [
                styles.shutterBtn,
                pressed && !disabled && { opacity: 0.85 },
                disabled && styles.shutterBtnDisabled,
              ]}
              testID="capture-shutter"
            >
              <View style={styles.shutterInner} />
            </Pressable>
          )}
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  camera: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
  },
  overlay: {
    flex: 1,
    justifyContent: "space-between",
    padding: spacing.lg,
  },
  topBar: {
    alignItems: "center",
    paddingTop: spacing.sm,
  },
  topBarText: {
    color: "rgba(255,255,255,0.7)",
    fontSize: 11,
    fontWeight: "500",
    textShadowColor: "rgba(0,0,0,0.5)",
    textShadowOffset: { width: 0, height: 1 },
    textShadowRadius: 2,
  },
  bottomBar: {
    alignItems: "center",
    paddingBottom: spacing.xl,
  },
  shutterBtn: {
    width: 64,
    height: 64,
    borderRadius: 32,
    borderWidth: 4,
    borderColor: "#fff",
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255,255,255,0.2)",
  },
  shutterBtnDisabled: { opacity: 0.5 },
  shutterInner: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: "#fff",
  },
  capturingIndicator: {
    alignItems: "center",
    gap: spacing.sm,
  },
  capturingText: {
    color: "#fff",
    fontSize: 12,
    fontWeight: "500",
    textShadowColor: "rgba(0,0,0,0.5)",
    textShadowOffset: { width: 0, height: 1 },
    textShadowRadius: 2,
  },
  errorContainer: {
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: "rgba(0,0,0,0.6)",
    padding: spacing.md,
    borderRadius: radius.md,
  },
  errorText: {
    color: colors.error,
    fontSize: 12,
    textAlign: "center",
  },
  retryBtn: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    borderRadius: radius.sm,
    backgroundColor: colors.brand,
  },
  retryBtnText: {
    color: "#000",
    fontSize: 12,
    fontWeight: "600",
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
  permissionTitle: {
    color: colors.onSurface,
    fontSize: 16,
    fontWeight: "600",
    marginTop: spacing.md,
    textAlign: "center",
  },
  permissionText: {
    color: colors.onSurfaceTertiary,
    fontSize: 12,
    textAlign: "center",
    marginTop: spacing.sm,
    marginBottom: spacing.lg,
  },
  permissionError: {
    color: colors.error,
    fontSize: 11,
    textAlign: "center",
    marginBottom: spacing.md,
  },
  permissionBtn: {
    backgroundColor: colors.brand,
    paddingHorizontal: spacing.xl,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
  },
  permissionBtnText: {
    color: "#000",
    fontSize: 13,
    fontWeight: "600",
  },
});

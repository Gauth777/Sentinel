import React, { useRef, useCallback } from "react";
import { View, Text, Pressable, StyleSheet, ActivityIndicator } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, spacing, radius } from "@/src/theme";

type Props = {
  onCapture: (uri: string) => void;
  disabled?: boolean;
};

export default function ObservationCamera({ onCapture, disabled }: Props) {
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);
  const isCapturing = useRef(false);

  const handleCapture = useCallback(async () => {
    if (isCapturing.current || disabled || !cameraRef.current) return;
    isCapturing.current = true;
    try {
      const photo = await cameraRef.current.takePictureAsync({
        quality: 0.7,
        base64: false,
      });
      if (photo?.uri) {
        onCapture(photo.uri);
      }
    } catch (err: unknown) {
      console.warn("[Sentinel] capture failed:", getErrorMessage(err));
    } finally {
      isCapturing.current = false;
    }
  }, [onCapture, disabled]);

  if (!permission) {
    return (
      <View style={styles.center} testID="capture-camera">
        <ActivityIndicator color={colors.brand} />
        <Text style={styles.loadingText}>Checking camera access…</Text>
      </View>
    );
  }

  if (!permission.granted) {
    return (
      <View style={styles.center} testID="capture-permission-denied">
        <MaterialCommunityIcons name="camera-off" size={40} color={colors.onSurfaceTertiary} />
        <Text style={styles.permissionTitle}>Camera access required</Text>
        <Text style={styles.permissionText}>
          Camera access is needed to capture road evidence for the dataset.
        </Text>
        <Pressable
          onPress={requestPermission}
          style={({ pressed }) => [styles.permissionBtn, pressed && { opacity: 0.85 }]}
          testID="capture-request-permission"
        >
          <Text style={styles.permissionBtnText}>Request Camera Access</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.container} testID="capture-camera">
      <CameraView style={styles.camera} ref={cameraRef} facing="back" mode="picture">
        <View style={styles.overlay}>
          <View style={styles.topBar}>
            <Text style={styles.topBarText}>Rear camera · tap shutter to capture</Text>
          </View>
          <View style={styles.bottomBar}>
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
          </View>
        </View>
      </CameraView>
    </View>
  );
}

function getErrorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  camera: { flex: 1 },
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

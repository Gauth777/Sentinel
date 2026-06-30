import React from "react";
import { View, Text, Pressable, StyleSheet, Image } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, spacing, radius } from "@/src/theme";
import type { CapturedMedia, MediaTelemetry } from "@/src/types/media";

type Props = {
  media: CapturedMedia;
  telemetry?: MediaTelemetry | null;
  onRetake: () => void;
  onUse: () => void;
  loading?: boolean;
};

export default function CapturePreview({ media, telemetry, onRetake, onUse, loading }: Props) {
  return (
    <View style={styles.container} testID="capture-preview">
      <Image source={{ uri: media.uri }} style={styles.image} resizeMode="contain" />

      <View style={styles.metaPanel}>
        <Text style={styles.metaTitle}>Image Preview</Text>
        <View style={styles.metaRow}>
          <Text style={styles.metaLabel}>Size</Text>
          <Text style={styles.metaValue}>{media.width} × {media.height}</Text>
        </View>

        {telemetry ? (
          <>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>GPS</Text>
              <Text style={styles.metaValue}>
                {telemetry.location
                  ? `${telemetry.location.latitude.toFixed(4)}, ${telemetry.location.longitude.toFixed(4)}`
                  : "Unavailable"}
              </Text>
            </View>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>Heading</Text>
              <Text style={styles.metaValue}>
                {typeof telemetry.headingDegrees === "number" ? `${telemetry.headingDegrees}°` : "Unavailable"}
              </Text>
            </View>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>Speed</Text>
              <Text style={styles.metaValue}>
                {typeof telemetry.speedKmh === "number" ? `${telemetry.speedKmh} km/h` : "Unavailable"}
              </Text>
            </View>
            <View style={styles.metaRow}>
              <Text style={styles.metaLabel}>Telemetry</Text>
              <Text style={styles.metaValue}>{telemetry.telemetrySource}</Text>
            </View>
          </>
        ) : (
          <View style={styles.metaRow}>
            <Text style={styles.metaLabel}>Telemetry</Text>
            <Text style={styles.metaValue}>Unavailable</Text>
          </View>
        )}

        <View style={styles.privacyNotice}>
          <MaterialCommunityIcons name="shield-alert" size={14} color={colors.warning} />
          <Text style={styles.privacyText}>
            Road images may contain faces, licence plates or other identifying details. Review the image before uploading.
          </Text>
        </View>
      </View>

      <View style={styles.actions}>
        <Pressable
          onPress={onRetake}
          style={({ pressed }) => [styles.btnGhost, pressed && { opacity: 0.85 }]}
          disabled={loading}
          testID="capture-retake"
        >
          <MaterialCommunityIcons name="camera-retake" size={16} color={colors.onSurfaceSecondary} />
          <Text style={styles.btnGhostText}>Retake</Text>
        </Pressable>
        <Pressable
          onPress={onUse}
          style={({ pressed }) => [styles.btnPrimary, pressed && { opacity: 0.85 }]}
          disabled={loading}
          testID="capture-use-photo"
        >
          <MaterialCommunityIcons name="upload" size={16} color="#000" />
          <Text style={styles.btnPrimaryText}>Use Photo</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  image: {
    flex: 1,
    backgroundColor: colors.surfaceSecondary,
  },
  metaPanel: {
    padding: spacing.lg,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  metaTitle: {
    color: colors.onSurface,
    fontSize: 14,
    fontWeight: "600",
    marginBottom: spacing.sm,
  },
  metaRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 4,
  },
  metaLabel: {
    color: colors.onSurfaceTertiary,
    fontSize: 11,
  },
  metaValue: {
    color: colors.onSurface,
    fontSize: 11,
    fontWeight: "500",
  },
  privacyNotice: {
    flexDirection: "row",
    gap: spacing.sm,
    marginTop: spacing.md,
    padding: spacing.sm,
    backgroundColor: "rgba(210,153,34,0.08)",
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.warning + "59",
  },
  privacyText: {
    color: colors.warning,
    fontSize: 11,
    fontWeight: "500",
    flex: 1,
  },
  actions: {
    flexDirection: "row",
    gap: spacing.sm,
    padding: spacing.lg,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  btnGhost: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingVertical: 12,
    borderRadius: radius.sm,
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
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingVertical: 12,
    borderRadius: radius.sm,
    backgroundColor: colors.brand,
  },
  btnPrimaryText: {
    color: "#000",
    fontSize: 12,
    fontWeight: "600",
  },
});

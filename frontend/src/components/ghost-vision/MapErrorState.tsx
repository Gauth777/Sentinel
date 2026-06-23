// Friendly error / fallback panel shown when GPS or backend is unavailable.
import React from "react";
import { View, Text, StyleSheet, Pressable } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, spacing, radius, fonts } from "@/src/theme";

export default function MapErrorState({
  title,
  message,
  onRetry,
  onUseDemo,
}: {
  title: string;
  message: string;
  onRetry?: () => void;
  onUseDemo?: () => void;
}) {
  return (
    <View style={styles.wrap} testID="map-error-state">
      <MaterialCommunityIcons name="map-marker-alert" size={26} color={colors.warning} />
      <Text style={styles.title}>{title}</Text>
      <Text style={styles.msg}>{message}</Text>
      <View style={styles.actions}>
        {onRetry && (
          <Pressable
            onPress={onRetry}
            style={[styles.btn, styles.btnPrimary]}
            android_ripple={{ color: "#003844" }}
            testID="map-error-retry-button"
          >
            <MaterialCommunityIcons name="refresh" size={16} color="#000" />
            <Text style={[styles.btnText, { color: "#000" }]}>Retry</Text>
          </Pressable>
        )}
        {onUseDemo && (
          <Pressable
            onPress={onUseDemo}
            style={styles.btn}
            android_ripple={{ color: "#1F2937" }}
            testID="map-error-demo-button"
          >
            <MaterialCommunityIcons name="television-play" size={16} color={colors.onSurface} />
            <Text style={styles.btnText}>Open Demo Scenario</Text>
          </Pressable>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    backgroundColor: colors.surfaceSecondary,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radius.lg,
    padding: spacing.lg,
    margin: spacing.lg,
    alignItems: "center",
    gap: spacing.sm,
  },
  title: { color: colors.onSurface, fontSize: fonts.size.lg, fontWeight: "500" },
  msg: { color: colors.onSurfaceSecondary, fontSize: fonts.size.sm, textAlign: "center" },
  actions: { flexDirection: "row", gap: spacing.sm, marginTop: spacing.sm, flexWrap: "wrap", justifyContent: "center" },
  btn: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceTertiary,
    minHeight: 48,
  },
  btnPrimary: { backgroundColor: colors.brand, borderColor: colors.brand },
  btnText: { color: colors.onSurface, fontSize: fonts.size.sm, fontWeight: "500", letterSpacing: 1 },
});

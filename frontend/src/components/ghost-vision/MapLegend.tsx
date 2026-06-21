// Compact legend showing what each visual symbol means.
import React from "react";
import { View, Text, StyleSheet } from "react-native";
import { colors, spacing, radius } from "@/src/theme";

const Items: { color: string; label: string; dashed?: boolean }[] = [
  { color: colors.brand, label: "You" },
  { color: colors.brandSecondary, label: "Sentinel" },
  { color: colors.warning, label: "Obstruction" },
  { color: colors.error, label: "Ghost hazard", dashed: true },
  { color: "#3A434F", label: "Unknown", dashed: true },
];

export default function MapLegend() {
  return (
    <View style={styles.wrap} testID="map-legend">
      {Items.map((it) => (
        <View key={it.label} style={styles.item}>
          <View
            style={[
              styles.swatch,
              { backgroundColor: it.dashed ? "transparent" : it.color, borderColor: it.color },
              it.dashed && styles.swatchDashed,
            ]}
          />
          <Text style={styles.text}>{it.label}</Text>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    position: "absolute",
    bottom: spacing.md,
    left: spacing.md,
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.sm,
    maxWidth: 230,
    backgroundColor: "rgba(9,10,12,0.78)",
    paddingHorizontal: spacing.sm,
    paddingVertical: 6,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
  },
  item: { flexDirection: "row", alignItems: "center", gap: 4 },
  swatch: {
    width: 10,
    height: 10,
    borderRadius: 2,
    borderWidth: 1,
  },
  swatchDashed: { borderStyle: "dashed" },
  text: { color: colors.onSurfaceSecondary, fontSize: 10, letterSpacing: 0.5 },
});

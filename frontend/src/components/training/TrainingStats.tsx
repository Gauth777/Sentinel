import React from "react";
import { View, Text, StyleSheet } from "react-native";
import { colors, spacing, radius, fonts } from "@/src/theme";
import type { TrainingStats as TrainingStatsType } from "@/src/types/training";

type Props = {
  stats: TrainingStatsType;
};

export default function TrainingStats({ stats }: Props) {
  const cards = [
    { label: "Total", value: stats.total, color: colors.onSurface },
    { label: "Pending", value: stats.pending, color: colors.warning },
    { label: "Verified", value: stats.verified, color: colors.success },
    { label: "Rejected", value: stats.rejected, color: colors.error },
    { label: "Corrected", value: stats.corrected, color: colors.brandSecondary },
    { label: "Exportable", value: stats.exportable, color: colors.brand },
  ];

  return (
    <View style={styles.container} testID="training-data-stats">
      <Text style={styles.title}>Dataset Statistics</Text>
      <View style={styles.grid}>
        {cards.map((c) => (
          <View key={c.label} style={styles.card}>
            <Text style={[styles.value, { color: c.color }]}>{c.value}</Text>
            <Text style={styles.label}>{c.label}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginHorizontal: spacing.lg,
    marginTop: spacing.md,
  },
  title: {
    color: colors.onSurfaceSecondary,
    fontSize: 11,
    fontWeight: "600",
    letterSpacing: 1.2,
    marginBottom: spacing.sm,
  },
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.sm,
  },
  card: {
    width: "30%",
    flexGrow: 1,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.md,
    alignItems: "center",
  },
  value: {
    fontSize: fonts.size.xxl,
    fontWeight: "600",
    letterSpacing: -0.5,
  },
  label: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    fontWeight: "500",
    letterSpacing: 0.5,
    marginTop: 2,
  },
});

import React, { useState } from "react";
import { View, Text, StyleSheet, Image, ActivityIndicator } from "react-native";
import { colors, spacing, radius, fonts } from "@/src/theme";
import { MaterialCommunityIcons } from "@expo/vector-icons";

type ReplayImagePairProps = {
  dashcamUrl: string | null;
  topviewUrl: string | null;
  testID?: string;
};

export default function ReplayImagePair({
  dashcamUrl,
  topviewUrl,
}: ReplayImagePairProps) {
  const [dashcamError, setDashcamError] = useState(false);
  const [topviewError, setTopviewError] = useState(false);
  const [dashcamLoading, setDashcamLoading] = useState(true);
  const [topviewLoading, setTopviewLoading] = useState(true);

  React.useEffect(() => {
    setDashcamError(false);
    setDashcamLoading(true);
  }, [dashcamUrl]);

  React.useEffect(() => {
    setTopviewError(false);
    setTopviewLoading(true);
  }, [topviewUrl]);


  return (
    <View style={styles.container}>
      {/* Dashcam Card */}
      <View style={styles.card}>
        <View style={styles.badgeContainer}>
          <Text style={styles.badgeText}>DASHCAM</Text>
        </View>
        <View style={styles.imageWrapper}>
          {dashcamUrl && !dashcamError ? (
            <>
              <Image
                source={{ uri: dashcamUrl }}
                style={styles.image}
                resizeMode="cover"
                onLoadStart={() => setDashcamLoading(true)}
                onLoadEnd={() => setDashcamLoading(false)}
                onError={() => {
                  setDashcamError(true);
                  setDashcamLoading(false);
                }}
                testID="demo-replay-dashcam"
              />
              {dashcamLoading && (
                <View style={styles.overlay}>
                  <ActivityIndicator size="small" color={colors.brand} />
                </View>
              )}
            </>
          ) : (
            <View style={styles.errorPlaceholder} testID="demo-replay-error">
              <MaterialCommunityIcons name="image-broken-variant" size={32} color={colors.error} />
              <Text style={styles.errorText}>
                {dashcamUrl ? "FAILED TO LOAD IMAGE" : "NO DASHCAM IMAGE"}
              </Text>
            </View>
          )}
        </View>
      </View>

      {/* Top View Card */}
      <View style={styles.card}>
        <View style={styles.badgeContainer}>
          <Text style={styles.badgeText}>TOP VIEW</Text>
        </View>
        <View style={styles.imageWrapper}>
          {topviewUrl && !topviewError ? (
            <>
              <Image
                source={{ uri: topviewUrl }}
                style={styles.image}
                resizeMode="cover"
                onLoadStart={() => setTopviewLoading(true)}
                onLoadEnd={() => setTopviewLoading(false)}
                onError={() => {
                  setTopviewError(true);
                  setTopviewLoading(false);
                }}
                testID="demo-replay-topview"
              />
              {topviewLoading && (
                <View style={styles.overlay}>
                  <ActivityIndicator size="small" color={colors.brand} />
                </View>
              )}
            </>
          ) : (
            <View style={styles.errorPlaceholder} testID="demo-replay-error">
              <MaterialCommunityIcons name="map-marker-off" size={32} color={colors.error} />
              <Text style={styles.errorText}>
                {topviewUrl ? "FAILED TO LOAD MAP" : "NO TOP VIEW MAP"}
              </Text>
            </View>
          )}
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    gap: spacing.md,
    width: "100%",
  },
  card: {
    flex: 1,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
    overflow: "hidden",
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 6,
    elevation: 4,
  },
  badgeContainer: {
    position: "absolute",
    top: spacing.sm,
    left: spacing.sm,
    backgroundColor: "rgba(9, 10, 12, 0.75)",
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    zIndex: 10,
  },
  badgeText: {
    color: colors.onSurface,
    fontSize: fonts.size.sm - 2,
    fontFamily: fonts.family,
    fontWeight: "bold",
    letterSpacing: 0.5,
  },
  imageWrapper: {
    width: "100%",
    aspectRatio: 1.33,
    backgroundColor: colors.surfaceTertiary,
    justifyContent: "center",
    alignItems: "center",
  },
  image: {
    width: "100%",
    height: "100%",
  },
  overlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(17, 20, 24, 0.6)",
    justifyContent: "center",
    alignItems: "center",
  },
  errorPlaceholder: {
    justifyContent: "center",
    alignItems: "center",
    padding: spacing.md,
    gap: spacing.xs,
  },
  errorText: {
    color: colors.onSurfaceTertiary,
    fontSize: fonts.size.sm - 1,
    fontFamily: fonts.family,
    fontWeight: "bold",
    textAlign: "center",
  },
});

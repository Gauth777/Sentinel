// Contextual expo-location hook. Caller decides WHEN to request — we never request on mount.
// Returns: { mode, location, heading, error, request, switchToDemo }
//   mode: "idle" | "requesting" | "live" | "denied" | "unavailable" | "demo"
// On web (preview) the hook gracefully falls through to "unavailable" without spamming.

import { useCallback, useEffect, useRef, useState } from "react";
import { Platform } from "react-native";
import * as Location from "expo-location";
import type { GeoPoint } from "@/src/types/sentinel";

export type LocationMode =
  | "idle"
  | "requesting"
  | "live"
  | "denied"
  | "unavailable"
  | "demo";

export type LocationState = {
  mode: LocationMode;
  location: GeoPoint | null;
  headingDegrees: number | null;
  error?: string;
};

export function useSentinelLocation() {
  const [state, setState] = useState<LocationState>({
    mode: "idle",
    location: null,
    headingDegrees: null,
  });
  const watchSub = useRef<Location.LocationSubscription | null>(null);
  const headingSub = useRef<Location.LocationSubscription | null>(null);

  const request = useCallback(async () => {
    setState((s) => ({ ...s, mode: "requesting", error: undefined }));
    if (Platform.OS === "web") {
      // Geolocation in a sandboxed iframe is unreliable — surface "unavailable" so UI can offer Demo.
      setState({ mode: "unavailable", location: null, headingDegrees: null,
        error: "Live GPS is unavailable in the web preview. Use the Android development build." });
      return;
    }
    try {
      const services = await Location.hasServicesEnabledAsync();
      if (!services) {
        setState({ mode: "unavailable", location: null, headingDegrees: null,
          error: "Location services are turned off on this device." });
        return;
      }
      const perm = await Location.requestForegroundPermissionsAsync();
      if (perm.status !== "granted") {
        setState({ mode: "denied", location: null, headingDegrees: null,
          error: "Foreground location permission was denied." });
        return;
      }
      const first = await Location.getCurrentPositionAsync({
        accuracy: Location.Accuracy.High,
      });
      setState({
        mode: "live",
        location: { latitude: first.coords.latitude, longitude: first.coords.longitude },
        headingDegrees: first.coords.heading ?? null,
      });

      // Continuous updates (throttled).
      watchSub.current = await Location.watchPositionAsync(
        { accuracy: Location.Accuracy.High, distanceInterval: 3, timeInterval: 1500 },
        (loc) => {
          setState((prev) => ({
            ...prev,
            mode: "live",
            location: { latitude: loc.coords.latitude, longitude: loc.coords.longitude },
            headingDegrees:
              typeof loc.coords.heading === "number" && loc.coords.heading >= 0
                ? loc.coords.heading
                : prev.headingDegrees,
          }));
        }
      );

      // Optional compass heading stream.
      try {
        headingSub.current = await Location.watchHeadingAsync((h) => {
          if (typeof h.trueHeading === "number" && h.trueHeading >= 0) {
            setState((p) => ({ ...p, headingDegrees: h.trueHeading }));
          }
        });
      } catch {
        // Heading sensor not available — keep position-derived heading.
      }
    } catch (err: any) {
      setState({ mode: "unavailable", location: null, headingDegrees: null,
        error: err?.message ?? "Location is unavailable." });
    }
  }, []);

  const switchToDemo = useCallback(() => {
    setState({ mode: "demo", location: null, headingDegrees: null });
  }, []);

  useEffect(() => {
    return () => {
      watchSub.current?.remove();
      headingSub.current?.remove();
    };
  }, []);

  return { ...state, request, switchToDemo };
}

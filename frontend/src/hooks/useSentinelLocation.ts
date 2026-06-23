// Contextual expo-location hook. Caller decides when to request live GPS.
// Returns: { mode, location, headingDegrees, error, request, switchToDemo }.

import { useCallback, useEffect, useRef, useState, type SetStateAction } from "react";
import { Platform } from "react-native";
import * as Location from "expo-location";
import type { GeoPoint } from "@/src/types/sentinel";
import { normalizeHeadingDegrees } from "@/src/utils/ghostVisionLive";

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
  const mountedRef = useRef(true);
  const requestSeq = useRef(0);

  const safeSetState = useCallback((next: SetStateAction<LocationState>) => {
    if (mountedRef.current) {
      setState(next);
    }
  }, []);

  const stopSubscriptions = useCallback(() => {
    watchSub.current?.remove();
    headingSub.current?.remove();
    watchSub.current = null;
    headingSub.current = null;
  }, []);

  const request = useCallback(async () => {
    const seq = requestSeq.current + 1;
    requestSeq.current = seq;
    stopSubscriptions();
    safeSetState((s) => ({ ...s, mode: "requesting", error: undefined }));

    if (Platform.OS === "web") {
      safeSetState({
        mode: "unavailable",
        location: null,
        headingDegrees: null,
        error: "Live GPS is unavailable in the web preview. Use the Android development build.",
      });
      return;
    }

    try {
      const services = await Location.hasServicesEnabledAsync();
      if (!mountedRef.current || seq !== requestSeq.current) return;
      if (!services) {
        safeSetState({
          mode: "unavailable",
          location: null,
          headingDegrees: null,
          error: "Location services are turned off on this device.",
        });
        return;
      }

      const perm = await Location.requestForegroundPermissionsAsync();
      if (!mountedRef.current || seq !== requestSeq.current) return;
      if (perm.status !== "granted") {
        safeSetState({
          mode: "denied",
          location: null,
          headingDegrees: null,
          error: "Foreground location permission was denied.",
        });
        return;
      }

      const first = await Location.getCurrentPositionAsync({
        accuracy: Location.Accuracy.High,
      });
      if (!mountedRef.current || seq !== requestSeq.current) return;
      safeSetState({
        mode: "live",
        location: { latitude: first.coords.latitude, longitude: first.coords.longitude },
        headingDegrees:
          typeof first.coords.heading === "number" && first.coords.heading >= 0
            ? normalizeHeadingDegrees(first.coords.heading)
            : null,
      });

      const positionSub = await Location.watchPositionAsync(
        { accuracy: Location.Accuracy.High, distanceInterval: 3, timeInterval: 1500 },
        (loc) => {
          safeSetState((prev) => ({
            ...prev,
            mode: "live",
            location: { latitude: loc.coords.latitude, longitude: loc.coords.longitude },
            headingDegrees:
              typeof loc.coords.heading === "number" && loc.coords.heading >= 0
                ? normalizeHeadingDegrees(loc.coords.heading)
                : prev.headingDegrees,
          }));
        }
      );
      if (!mountedRef.current || seq !== requestSeq.current) {
        positionSub.remove();
        return;
      }
      watchSub.current = positionSub;

      try {
        const compassSub = await Location.watchHeadingAsync((h) => {
          if (typeof h.trueHeading === "number" && h.trueHeading >= 0) {
            safeSetState((p) => ({ ...p, headingDegrees: normalizeHeadingDegrees(h.trueHeading) }));
          }
        });
        if (!mountedRef.current || seq !== requestSeq.current) {
          compassSub.remove();
          return;
        }
        headingSub.current = compassSub;
      } catch {
        // Heading sensor is optional; keep the GPS-derived heading when unavailable.
      }
    } catch (err: any) {
      if (!mountedRef.current || seq !== requestSeq.current) return;
      safeSetState({
        mode: "unavailable",
        location: null,
        headingDegrees: null,
        error: err?.message ?? "Location is unavailable.",
      });
    }
  }, [safeSetState, stopSubscriptions]);

  const switchToDemo = useCallback(() => {
    requestSeq.current += 1;
    stopSubscriptions();
    safeSetState({ mode: "demo", location: null, headingDegrees: null });
  }, [safeSetState, stopSubscriptions]);

  useEffect(() => {
    return () => {
      mountedRef.current = false;
      requestSeq.current += 1;
      stopSubscriptions();
    };
  }, [stopSubscriptions]);

  return { ...state, request, switchToDemo };
}

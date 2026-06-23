// Contextual expo-location hook. Caller decides when to request live GPS.
// Returns: { mode, location, headingDegrees, speedKmh, error, request, switchToDemo }.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type SetStateAction,
} from "react";
import { Platform } from "react-native";
import * as Location from "expo-location";
import type { GeoPoint } from "@/src/types/sentinel";
import { normalizeHeadingDegrees } from "@/src/utils/ghostVisionLive";

function gpsSpeedToKmh(
  speedMps: number | null | undefined
): number | null {
  if (
    typeof speedMps !== "number" ||
    !Number.isFinite(speedMps) ||
    speedMps < 0
  ) {
    return null;
  }

  return speedMps * 3.6;
}

function angularDifference(a: number, b: number): number {
  return Math.abs(((a - b + 540) % 360) - 180);
}

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
  speedKmh: number;
  error?: string;
};

export function useSentinelLocation() {
  const [state, setState] = useState<LocationState>({
    mode: "idle",
    location: null,
    headingDegrees: null,
    speedKmh: 0,
  });

  const watchSub = useRef<Location.LocationSubscription | null>(null);
  const headingSub = useRef<Location.LocationSubscription | null>(null);
  const mountedRef = useRef(true);
  const requestSeq = useRef(0);

  const safeSetState = useCallback(
    (next: SetStateAction<LocationState>) => {
      if (mountedRef.current) {
        setState(next);
      }
    },
    []
  );

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

    safeSetState((previous) => ({
      ...previous,
      mode: "requesting",
      speedKmh: 0,
      error: undefined,
    }));

    if (Platform.OS === "web") {
      safeSetState({
        mode: "unavailable",
        location: null,
        headingDegrees: null,
        speedKmh: 0,
        error:
          "Live GPS is unavailable in the web preview. Use the Android development build.",
      });
      return;
    }

    try {
      const servicesEnabled =
        await Location.hasServicesEnabledAsync();

      if (!mountedRef.current || seq !== requestSeq.current) {
        return;
      }

      if (!servicesEnabled) {
        safeSetState({
          mode: "unavailable",
          location: null,
          headingDegrees: null,
          speedKmh: 0,
          error: "Location services are turned off on this device.",
        });
        return;
      }

      const permission =
        await Location.requestForegroundPermissionsAsync();

      if (!mountedRef.current || seq !== requestSeq.current) {
        return;
      }

      if (permission.status !== "granted") {
        safeSetState({
          mode: "denied",
          location: null,
          headingDegrees: null,
          speedKmh: 0,
          error: "Foreground location permission was denied.",
        });
        return;
      }

      const first = await Location.getCurrentPositionAsync({
        accuracy: Location.Accuracy.High,
      });

      if (!mountedRef.current || seq !== requestSeq.current) {
        return;
      }

      const initialSpeedKmh =
        gpsSpeedToKmh(first.coords.speed) ?? 0;

      safeSetState({
        mode: "live",
        location: {
          latitude: first.coords.latitude,
          longitude: first.coords.longitude,
        },
        headingDegrees:
          typeof first.coords.heading === "number" &&
          first.coords.heading >= 0
            ? normalizeHeadingDegrees(first.coords.heading)
            : null,
        speedKmh: initialSpeedKmh < 1 ? 0 : initialSpeedKmh,
      });

      const positionSub = await Location.watchPositionAsync(
        {
          accuracy: Location.Accuracy.High,
          distanceInterval: 3,
          timeInterval: 1500,
        },
        (locationUpdate) => {
          const measuredSpeedKmh = gpsSpeedToKmh(
            locationUpdate.coords.speed
          );

          safeSetState((previous) => {
            let nextSpeedKmh = previous.speedKmh;

            if (measuredSpeedKmh !== null) {
              nextSpeedKmh =
                previous.speedKmh === 0
                  ? measuredSpeedKmh
                  : previous.speedKmh * 0.65 +
                    measuredSpeedKmh * 0.35;

              // Ignore low-speed GPS noise while stationary.
              if (nextSpeedKmh < 1) {
                nextSpeedKmh = 0;
              }
            }

            return {
              ...previous,
              mode: "live",
              location: {
                latitude: locationUpdate.coords.latitude,
                longitude: locationUpdate.coords.longitude,
              },
              headingDegrees:
                typeof locationUpdate.coords.heading === "number" &&
                locationUpdate.coords.heading >= 0
                  ? normalizeHeadingDegrees(
                      locationUpdate.coords.heading
                    )
                  : previous.headingDegrees,
              speedKmh: nextSpeedKmh,
              error: undefined,
            };
          });
        }
      );

      if (!mountedRef.current || seq !== requestSeq.current) {
        positionSub.remove();
        return;
      }

      watchSub.current = positionSub;

      try {
        const compassSub = await Location.watchHeadingAsync(
          (headingUpdate) => {
            if (
              typeof headingUpdate.trueHeading !== "number" ||
              headingUpdate.trueHeading < 0
            ) {
              return;
            }

            const nextHeading = normalizeHeadingDegrees(
              headingUpdate.trueHeading
            );

            safeSetState((previous) => {
              if (
                typeof previous.headingDegrees === "number" &&
                angularDifference(
                  nextHeading,
                  previous.headingDegrees
                ) < 2
              ) {
                return previous;
              }

              return {
                ...previous,
                headingDegrees: nextHeading,
              };
            });
          }
        );

        if (!mountedRef.current || seq !== requestSeq.current) {
          compassSub.remove();
          return;
        }

        headingSub.current = compassSub;
      } catch {
        // Heading sensor is optional. GPS-derived heading remains available.
      }
    } catch (error: any) {
      if (!mountedRef.current || seq !== requestSeq.current) {
        return;
      }

      safeSetState({
        mode: "unavailable",
        location: null,
        headingDegrees: null,
        speedKmh: 0,
        error: error?.message ?? "Location is unavailable.",
      });
    }
  }, [safeSetState, stopSubscriptions]);

  const switchToDemo = useCallback(() => {
    requestSeq.current += 1;
    stopSubscriptions();

    safeSetState({
      mode: "demo",
      location: null,
      headingDegrees: null,
      speedKmh: 0,
    });
  }, [safeSetState, stopSubscriptions]);

  useEffect(() => {
    return () => {
      mountedRef.current = false;
      requestSeq.current += 1;
      stopSubscriptions();
    };
  }, [stopSubscriptions]);

  return {
    ...state,
    request,
    switchToDemo,
  };
}

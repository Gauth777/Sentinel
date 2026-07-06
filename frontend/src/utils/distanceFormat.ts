/**
 * Safe distance formatting utility for Sentinel.
 */

export function formatDistance(distanceMeters: number): string {
  if (
    typeof distanceMeters !== "number" ||
    isNaN(distanceMeters) ||
    !isFinite(distanceMeters) ||
    distanceMeters < 0
  ) {
    return "\u2014";
  }

  if (distanceMeters < 1000) {
    const rounded = Math.round(distanceMeters);
    return `${rounded} m`;
  } else {
    const km = distanceMeters / 1000;
    const roundedKm = Math.round(km * 10) / 10;
    return `${roundedKm.toFixed(1)} km`;
  }
}

export function formatDistanceForSpeech(distanceMeters: number): string {
  if (
    typeof distanceMeters !== "number" ||
    isNaN(distanceMeters) ||
    !isFinite(distanceMeters) ||
    distanceMeters < 0
  ) {
    return "unknown distance";
  }

  if (distanceMeters < 1000) {
    const rounded = Math.round(distanceMeters);
    return `${rounded} metres`;
  } else {
    const km = distanceMeters / 1000;
    const roundedKm = Math.round(km * 10) / 10;
    return `${roundedKm.toFixed(1)} kilometres`;
  }
}

// Web-Mercator-ish projection used by the structured WorldMap.
// Converts geographic coordinates into SVG pixel space for the current viewport bounds.
// Kept intentionally lightweight — we don't need real tile-server precision here,
// just a stable linear projection over a small local extent (< 2 km).

import type { GeoPoint } from "@/src/types/sentinel";

export type Bounds = { southWest: GeoPoint; northEast: GeoPoint };

export type Projector = {
  /** Convert lat/lon → x/y in pixel space for the configured viewport. */
  project: (p: GeoPoint) => { x: number; y: number };
  width: number;
  height: number;
  /** Approximate metres-per-pixel along the latitude axis. */
  metersPerPixel: number;
};

export function makeProjector(
  bounds: Bounds,
  width: number,
  height: number
): Projector {
  const { southWest, northEast } = bounds;
  const lonSpan = northEast.longitude - southWest.longitude;
  const latSpan = northEast.latitude - southWest.latitude;

  const project = (p: GeoPoint) => {
    const nx = (p.longitude - southWest.longitude) / lonSpan;
    const ny = (p.latitude - southWest.latitude) / latSpan;
    return { x: nx * width, y: (1 - ny) * height };
  };

  // Metres-per-pixel (approx) — latitude is 111_111 m / deg.
  const metersPerPixel = (latSpan * 111_111) / height;
  return { project, width, height, metersPerPixel };
}

/** Approximate ground distance between two GeoPoints (Haversine). */
export function haversineMeters(a: GeoPoint, b: GeoPoint): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const R = 6_371_000;
  const dLat = toRad(b.latitude - a.latitude);
  const dLon = toRad(b.longitude - a.longitude);
  const lat1 = toRad(a.latitude);
  const lat2 = toRad(b.latitude);
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

/** Build a tight viewport (bounds) around an ego point with the requested radius in metres. */
export function boundsAround(center: GeoPoint, radiusMeters: number): Bounds {
  const dLat = radiusMeters / 111_111;
  const dLon = radiusMeters / (111_111 * Math.cos((center.latitude * Math.PI) / 180));
  return {
    southWest: { latitude: center.latitude - dLat, longitude: center.longitude - dLon },
    northEast: { latitude: center.latitude + dLat, longitude: center.longitude + dLon },
  };
}

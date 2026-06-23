import math
from typing import Dict, Tuple

EARTH_RADIUS_M = 6_371_000.0
METERS_PER_DEGREE_LAT = 111_111.0


def haversine_meters(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Great-circle distance between two WGS84 coordinates."""
    lat1 = math.radians(a_lat)
    lat2 = math.radians(b_lat)
    d_lat = math.radians(b_lat - a_lat)
    d_lon = math.radians(b_lon - a_lon)
    h = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def destination_point(
    latitude: float,
    longitude: float,
    bearing_degrees: float,
    distance_meters: float,
) -> Dict[str, float]:
    """Return the coordinate reached by travelling a bearing and distance."""
    bearing = math.radians(bearing_degrees)
    lat1 = math.radians(latitude)
    lon1 = math.radians(longitude)
    angular_distance = distance_meters / EARTH_RADIUS_M

    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular_distance)
        + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(lat1),
        math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
    )

    return {
        "latitude": math.degrees(lat2),
        "longitude": ((math.degrees(lon2) + 540) % 360) - 180,
    }


def offset_point(latitude: float, longitude: float, north_m: float, east_m: float) -> Dict[str, float]:
    """Small-distance local north/east offset conversion."""
    lat = latitude + north_m / METERS_PER_DEGREE_LAT
    lon = longitude + east_m / (
        METERS_PER_DEGREE_LAT * max(0.01, math.cos(math.radians(latitude)))
    )
    return {"latitude": lat, "longitude": lon}


def local_offsets_meters(origin: Dict[str, float], point: Dict[str, float]) -> Tuple[float, float]:
    """Return north/east offsets from origin to point in metres."""
    north = (point["latitude"] - origin["latitude"]) * METERS_PER_DEGREE_LAT
    east = (
        (point["longitude"] - origin["longitude"])
        * METERS_PER_DEGREE_LAT
        * max(0.01, math.cos(math.radians(origin["latitude"])))
    )
    return north, east

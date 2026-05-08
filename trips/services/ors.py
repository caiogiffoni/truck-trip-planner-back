"""
OpenRouteService integration.

Provides geocoding (city/state → lat/lng) and HGV truck routing
(distance in miles, duration in hours) per leg.
"""

import logging
import math

import requests
from config.settings import URL_BASE, API_KEY
from trips.models.route_request import RouteRequest


logger = logging.getLogger(__name__)

METERS_PER_MILE = 1609.344
EARTH_RADIUS_MILES = 3958.8


def _haversine_miles(p1: list, p2: list) -> float:
    """Great-circle distance in miles between two [lng, lat] points."""
    lng1, lat1 = math.radians(p1[0]), math.radians(p1[1])
    lng2, lat2 = math.radians(p2[0]), math.radians(p2[1])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return EARTH_RADIUS_MILES * 2 * math.asin(math.sqrt(a))


def interpolate_along_polyline(polyline: list, target_miles: float) -> list:
    """
    Return the [lng, lat] coordinate at target_miles along a polyline.

    Walks segment by segment accumulating Haversine distance until the
    target is reached, then linearly interpolates within that segment.
    Returns the first point if target <= 0, last point if target exceeds
    the total polyline length.
    """
    if not polyline:
        return None
    if target_miles <= 0:
        return polyline[0]

    cumulative = 0.0
    for i in range(len(polyline) - 1):
        p1, p2 = polyline[i], polyline[i + 1]
        seg_miles = _haversine_miles(p1, p2)
        if cumulative + seg_miles >= target_miles:
            t = (target_miles - cumulative) / seg_miles if seg_miles > 0 else 0.0
            lng = p1[0] + t * (p2[0] - p1[0])
            lat = p1[1] + t * (p2[1] - p1[1])
            return [round(lng, 6), round(lat, 6)]
        cumulative += seg_miles

    return polyline[-1]


def reverse_geocode(lng: float, lat: float) -> str:
    """Return a 'City, State' label for a [lng, lat] coordinate."""
    resp = requests.get(
        f"{URL_BASE}/geocode/reverse",
        params={"api_key": API_KEY, "point.lon": lng, "point.lat": lat, "size": 1},
        timeout=10,
    )
    resp.raise_for_status()
    features = resp.json().get("features", [])
    if not features:
        return ""
    props = features[0].get("properties", {})
    locality = props.get("locality") or props.get("name") or ""
    region = props.get("region_a") or props.get("region") or ""
    if locality and region:
        return f"{locality}, {region}"
    return props.get("label", "")


def geocode(location: str) -> tuple[float, float]:
    """Return (latitude, longitude) for a city/state string."""
    logger.debug(f"Geocoding location: {location}")
    resp = requests.get(
        f"{URL_BASE}/geocode/search",
        params={"api_key": API_KEY, "text": location, "size": 1},
        timeout=10,
    )
    resp.raise_for_status()
    features = resp.json().get("features", [])
    if not features:
        logger.warning(f"No geocoding results for: {location}")
        raise ValueError(f"Could not geocode location: '{location}'")
    # returns [longitude, latitude]
    lng, lat = features[0]["geometry"]["coordinates"]
    logger.debug(f"Geocoded '{location}' → lat={lat:.6f}, lng={lng:.6f}")
    return lat, lng


def get_route_leg(
    origin: tuple[float, float], destination: tuple[float, float]
) -> dict:
    """
    Request driving-hgv (heavy goods vehicle) directions between two points.

    Args:
        origin: (latitude, longitude)
        destination: (latitude, longitude)

    Returns:
        {"miles": float, "drive_hrs": float, "polyline": [[lng, lat], ...]}
    """
    logger.debug(f"Requesting route leg: {origin} - {destination}")
    # directions expect [longitude, latitude] order
    coords = [
        [origin[1], origin[0]],
        [destination[1], destination[0]],
    ]
    resp = requests.post(
        f"{URL_BASE}/v2/directions/driving-hgv/geojson",
        headers={"Authorization": API_KEY, "Content-Type": "application/json"},
        json={"coordinates": coords},
        timeout=15,
    )
    resp.raise_for_status()
    feature = resp.json()["features"][0]
    summary = feature["properties"]["summary"]
    miles = round(summary["distance"] / METERS_PER_MILE, 2)
    drive_hrs = round(summary["duration"] / 3600, 2)
    polyline = feature["geometry"]["coordinates"]  # [[lng, lat], ...]
    logger.debug(f"Leg result: {miles:.2f} miles, {drive_hrs:.2f} hours")
    return {"miles": miles, "drive_hrs": drive_hrs, "polyline": polyline}


def plan_route(payload: RouteRequest) -> dict:
    """
    Geocode all three stops and compute per-leg route metrics.

    Returns:
        {
            "total_miles": float,
            "total_drive_time_hrs": float,
            "polyline": [[lng, lat], ...],
            "legs": [
                {"from": str, "to": str, "miles": float, "drive_hrs": float},
                ...
            ],
        }
    """
    current_location=payload.current_location.capitalize()
    pickup_location=payload.pickup_location.capitalize()
    dropoff_location=payload.dropoff_location.capitalize()
    logger.info(
        f"Planning trip: {current_location} → {pickup_location} → {dropoff_location}"
    )
    stops = [current_location, pickup_location, dropoff_location]
    # geocode returns (lat, lng) — store as [lng, lat] for GeoJSON consistency
    raw_coords = [geocode(stop) for stop in stops]
    waypoint_coords = [[lng, lat] for lat, lng in raw_coords]

    legs = []
    full_polyline = []
    for i in range(len(stops) - 1):
        leg_data = get_route_leg(raw_coords[i], raw_coords[i + 1])
        legs.append(
            {
                "from": stops[i],
                "to": stops[i + 1],
                "miles": leg_data["miles"],
                "drive_hrs": leg_data["drive_hrs"],
            }
        )
        # Avoid duplicate junction point between legs
        leg_coords = leg_data["polyline"]
        if full_polyline:
            leg_coords = leg_coords[1:]
        full_polyline.extend(leg_coords)

    total_miles = round(sum(l["miles"] for l in legs), 2)
    total_drive_time_hrs = round(sum(l["drive_hrs"] for l in legs), 2)

    logger.info(f"Trip total: {total_miles:.2f} miles, {total_drive_time_hrs:.2f} hours")
    return {
        "total_miles": total_miles,
        "total_drive_time_hrs": total_drive_time_hrs,
        "polyline": full_polyline,
        "waypoints": waypoint_coords,   # [start, pickup, dropoff] as [lng, lat]
        "legs": legs,
    }

"""
OpenRouteService integration.

Provides geocoding (address → [lng, lat]) and HGV truck routing:
- distance in miles per leg
- drive time in hours per leg
- GeoJSON polyline for the full route
"""

import logging

import requests
from config.settings import URL_BASE, API_KEY


logger = logging.getLogger(__name__)

METERS_PER_MILE = 1609.344


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


def get_route(
    stop_names: list[str],
    coords: list[tuple[float, float]],
) -> dict:
    """
    Request a multi-waypoint HGV route from ORS.

    Args:
        stop_names: human-readable names for each waypoint
        coords: list of (latitude, longitude) per waypoint

    Returns route dict matching the spec:
    {
        "total_miles": float,
        "total_drive_time_hrs": float,
        "polyline": [[lng, lat], ...],
        "legs": [
            {"from": str, "to": str, "miles": float, "drive_hrs": float},
            ...
        ]
    }
    """
    # ORS expects [longitude, latitude]
    ors_coords = [[lat_lng[1], lat_lng[0]] for lat_lng in coords]

    logger.info(f"Requesting HGV route for {len(coords)} waypoints")
    resp = requests.post(
        f"{URL_BASE}/v2/directions/driving-hgv/json",
        headers={"Authorization": API_KEY, "Content-Type": "application/json"},
        json={"coordinates": coords},
        timeout=15,
    )
    resp.raise_for_status()

    data = resp.json()
    route = data["routes"][0]
    summary = route["summary"]
    segments = route.get("legs", [])
    polyline = route["geometry"]["coordinates"]  # [[lng, lat], ...]

    legs = []
    for i, seg in enumerate(segments):
        seg_summary = seg["summary"]
        legs.append({
            "from": stop_names[i],
            "to": stop_names[i + 1],
            "miles": round(seg_summary["distance"] / METERS_PER_MILE, 2),
            "drive_hrs": round(seg_summary["duration"] / 3600, 2),
        })
        logger.debug(
            f"Leg {i + 1}: {stop_names[i]} → {stop_names[i+1]} "
            f"— {legs[-1]['miles']} mi, {legs[-1]['drive_hrs']} hrs"
        )

    total_miles = round(summary["distance"] / METERS_PER_MILE, 2)
    total_hrs = round(summary["duration"] / 3600, 2)
    logger.info(f"Route total: {total_miles} miles, {total_hrs} hrs")

    return {
        "total_miles": total_miles,
        "total_drive_time_hrs": total_hrs,
        "polyline": polyline,
        "legs": legs,
    }


def plan_route(
    current_location: str,
    pickup_location: str,
    dropoff_location: str,
) -> dict:
    """
    Geocode all three stops and return the full route object.
    """
    stop_names = [current_location, pickup_location, dropoff_location]
    logger.info(f"Planning route: {' → '.join(stop_names)}")

    coords = [geocode(name) for name in stop_names]
    return get_route(stop_names, coords)

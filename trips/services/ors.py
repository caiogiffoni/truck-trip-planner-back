"""
OpenRouteService integration.

Provides geocoding (city/state → lat/lng) and HGV truck routing
(distance in miles, duration in hours) per leg.
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


def plan_route(
    current_location: str,
    pickup_location: str,
    dropoff_location: str,
) -> dict:
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
    logger.info(
        f"Planning trip: {current_location} → {pickup_location} → {dropoff_location}"
    )
    stops = [current_location, pickup_location, dropoff_location]
    coords = [geocode(stop) for stop in stops]

    legs = []
    full_polyline = []
    for i in range(len(stops) - 1):
        leg_data = get_route_leg(coords[i], coords[i + 1])
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
        "legs": legs,
    }

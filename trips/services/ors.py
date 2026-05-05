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
        {"distance_miles": float, "duration_hours": float}
    """
    logger.debug(f"Requesting route leg: {origin} - {destination}")
    # directions expect [longitude, latitude] order
    coords = [
        [origin[1], origin[0]],
        [destination[1], destination[0]],
    ]
    resp = requests.post(
        f"{URL_BASE}/v2/directions/driving-hgv/json",
        headers={"Authorization": API_KEY, "Content-Type": "application/json"},
        json={"coordinates": coords},
        timeout=15,
    )
    resp.raise_for_status()
    summary = resp.json()["routes"][0]["summary"]
    distance_miles = round(summary["distance"] / METERS_PER_MILE, 2)
    duration_hours = round(summary["duration"] / 3600, 2)
    logger.debug(f"Leg result: {distance_miles:.2f} miles, {duration_hours:.2f} hours")
    return {"distance_miles": distance_miles, "duration_hours": duration_hours}


def plan_route(
    current_location: str,
    pickup_location: str,
    dropoff_location: str,
) -> dict:
    """
    Geocode all three stops and compute per-leg route metrics.

    Returns:
        {
            "legs": [
                {"from": str, "to": str, "distance_miles": float, "duration_hours": float},
                ...
            ],
            "total_distance_miles": float,
            "total_duration_hours": float,
        }
    """
    logger.info(
        f"Planning trip: {current_location} → {pickup_location} → {dropoff_location}"
    )
    stops = [current_location, pickup_location, dropoff_location]
    coords = [geocode(stop) for stop in stops]

    legs = []
    for i in range(len(stops) - 1):
        leg_data = get_route_leg(coords[i], coords[i + 1])
        legs.append(
            {
                "from": stops[i],
                "to": stops[i + 1],
                "distance_miles": leg_data["distance_miles"],
                "duration_hours": leg_data["duration_hours"],
            }
        )

    total_miles = round(sum(l["distance_miles"] for l in legs), 2)
    total_hours = round(sum(l["duration_hours"] for l in legs), 2)

    logger.info(f"Trip total: {total_miles:.2f} miles, {total_hours:.2f} hours")
    return {
        "legs": legs,
        "total_distance_miles": total_miles,
        "total_duration_hours": total_hours,
    }

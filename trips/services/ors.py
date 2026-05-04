"""
OpenRouteService integration.

Provides geocoding (city/state → lat/lng) and HGV truck routing
(distance in miles, duration in hours) per leg.
"""

import requests
from config.settings import URL_BASE, ORS_API_KEY


METERS_PER_MILE = 1609.344



def geocode(location: str) -> tuple[float, float]:
    """Return (latitude, longitude) for a city/state string."""
    resp = requests.get(
        f"{URL_BASE}/geocode/search",
        params={"api_key": ORS_API_KEY, "text": location, "size": 1},
        timeout=10,
    )
    resp.raise_for_status()
    features = resp.json().get("features", [])
    if not features:
        raise ValueError(f"Could not geocode location: '{location}'")
    # ORS returns [longitude, latitude]
    lng, lat = features[0]["geometry"]["coordinates"]
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
    # ORS directions expect [longitude, latitude] order
    coords = [
        [origin[1], origin[0]],
        [destination[1], destination[0]],
    ]
    resp = requests.post(
        f"{URL_BASE}/v2/directions/driving-hgv/json",
        headers={"Authorization": ORS_API_KEY, "Content-Type": "application/json"},
        json={"coordinates": coords},
        timeout=15,
    )
    resp.raise_for_status()
    summary = resp.json()["routes"][0]["summary"]
    distance_miles = round(summary["distance"] / METERS_PER_MILE, 2)
    duration_hours = round(summary["duration"] / 3600, 2)
    return {"distance_miles": distance_miles, "duration_hours": duration_hours}


def plan_trip(
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

    return {
        "legs": legs,
        "total_distance_miles": total_miles,
        "total_duration_hours": total_hours,
    }

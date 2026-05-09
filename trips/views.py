import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from ratelimit.decorators import ratelimit
from pydantic import ValidationError

from trips.services.ors import plan_route, enrich_trip_stops
from trips.utils.hos_calculator import calculate_trip
from .models.route_request import RouteRequest



@require_http_methods(["GET"])
def health_check(request):
    return JsonResponse({"status": "ok"})


@ratelimit(key="ip", rate="10/m", method="POST", block=True)
@csrf_exempt
@require_http_methods(["POST"])
def plan(request):
    try:
        payload = RouteRequest.model_validate_json(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)
    except ValidationError as exc:
        errors = {e["loc"][0]: e["msg"] for e in exc.errors()}
        return JsonResponse({"error": "Validation failed", "details": errors}, status=400)

    try:
        route_data = plan_route(payload)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"error": f"Routing failed: {exc}"}, status=502)

    try:
        trip_result = calculate_trip(
            legs=route_data["legs"],
            pickup_location=payload.pickup_location.capitalize(),
            dropoff_location=payload.dropoff_location.capitalize(),
            current_cycle_used=payload.current_cycle_used,
            has_curfew=payload.has_curfew,
        )
    except Exception as exc:
        return JsonResponse({"error": f"HOS calculation failed: {exc}"}, status=500)

    waypoints = route_data["waypoints"]
    trip_dict = trip_result.to_dict()
    enrich_trip_stops(
        trip_dict,
        polyline=route_data["polyline"],
        named_coords={
            "start":   waypoints[0],
            "pickup":  waypoints[1],
            "dropoff": waypoints[2],
        },
        named_locations={
            "start":   payload.current_location.capitalize(),
            "pickup":  payload.pickup_location.capitalize(),
            "dropoff": payload.dropoff_location.capitalize(),
        },
    )

    return JsonResponse({
        "route": route_data,
        **trip_dict,
    })

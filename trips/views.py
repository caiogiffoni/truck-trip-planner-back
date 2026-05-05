import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from pydantic import ValidationError

from trips.services.ors import plan_route
from trips.utils.hos_calculator import calculate_trip
from .models.route_request import RouteRequest



@require_http_methods(["GET"])
def health_check(request):
    return JsonResponse({"status": "ok"})


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
            pickup_location=payload.pickup_location,
            dropoff_location=payload.dropoff_location,
            current_cycle_used=payload.current_cycle_used,
        )
    except Exception as exc:
        return JsonResponse({"error": f"HOS calculation failed: {exc}"}, status=500)

    return JsonResponse({
        "route": route_data,
        **trip_result.to_dict(),
    })

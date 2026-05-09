# ELD Trip Planner — Backend

Django REST API that takes a truck driver's route inputs, calculates an HOS-compliant
schedule, and returns structured daily log data for the frontend to render.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | Django 5.2 + Django REST Framework |
| Validation | Pydantic v2 |
| Routing / Geocoding | OpenRouteService API |
| Server | Gunicorn |
| Rate limiting | django-ratelimit |
| Package manager | uv |
| Testing | pytest |

---

## Project Structure

```
config/
  settings.py          # Django settings (env-driven)
  urls.py              # Root URL config

trips/
  models/
    route_request.py   # Pydantic request model (validation)
  services/
    ors.py             # OpenRouteService client (geocoding, routing, stop enrichment)
  utils/
    hos_calculator.py  # HOS scheduling algorithm (pure logic, no geo dependencies)
    test_hos_calculator.py
  views.py             # API views (thin: parse → route → HOS → enrich → respond)
  urls.py              # Trip-scoped URL config

main.py                # Entry point (manage.py equivalent)
pyproject.toml         # Dependencies
.env.example           # Environment variable template
```

---

## API Endpoints

### `GET /api/health/`

Health check.

```json
{ "status": "ok" }
```

---

### `POST /api/trip/plan/`

Geocodes all three locations, fetches routing data from OpenRouteService, runs the
HOS scheduling algorithm, and returns a complete trip plan.

**Request body:**

```json
{
  "current_location": "Chicago, IL",
  "pickup_location": "St. Louis, MO",
  "dropoff_location": "Nashville, TN",
  "current_cycle_used": 24.5,
  "has_curfew": true
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `current_location` | string | — | Driver's starting point |
| `pickup_location` | string | — | Where the load is picked up |
| `dropoff_location` | string | — | Final delivery destination |
| `current_cycle_used` | float (0–70) | — | Hours already used in the driver's current 8-day HOS cycle |
| `has_curfew` | boolean | `true` | Enforce the custom 23:00–05:00 no-drive window |

**Response:**

```json
{
  "route": {
    "total_miles": 542.0,
    "total_drive_time_hrs": 8.2,
    "polyline": [[lng, lat], "..."],
    "waypoints": [[-87.6298, 41.8781], [-90.1994, 38.6270], [-86.7816, 36.1627]],
    "legs": [
      { "from": "Chicago, IL", "to": "St. Louis, MO", "miles": 297.0, "drive_hrs": 4.5 },
      { "from": "St. Louis, MO", "to": "Nashville, TN", "miles": 245.0, "drive_hrs": 3.7 }
    ]
  },
  "days": [
    {
      "day": 1,
      "date_offset_days": 0,
      "total_miles": 350.0,
      "events": [
        { "status": "off_duty", "start": 0.0,  "end": 6.0,  "remark": "Off duty", "miles": 0.0 },
        { "status": "on_duty",  "start": 6.0,  "end": 6.5,  "remark": "Pre-trip inspection", "miles": 0.0 },
        { "status": "driving",  "start": 6.5,  "end": 14.5, "remark": "Driving to St. Louis, MO", "miles": 346.7 },
        { "status": "off_duty", "start": 14.5, "end": 24.0, "remark": "10-hr rest - 11-hr drive limit", "miles": 0.0 }
      ]
    }
  ],
  "stops": [
    { "type": "start",   "remark": "Start at Chicago, IL",            "day": 1, "time_start": "06:00", "time_end": "06:00", "leg_index": 0, "cumulative_miles": 0.0,   "coords": [-87.6298, 41.8781], "location": "Chicago, IL" },
    { "type": "pickup",  "remark": "Pickup at St. Louis, MO",         "day": 1, "time_start": "11:30", "time_end": "12:30", "leg_index": 0, "cumulative_miles": 297.0,  "coords": [-90.1994, 38.6270], "location": "St. Louis, MO" },
    { "type": "rest",    "remark": "10-hr rest - 11-hr drive limit",  "day": 1, "time_start": "14:30", "time_end": "24:30", "leg_index": 0, "cumulative_miles": 346.7,  "coords": [-91.2345, 37.1234], "location": "Rolla, MO" },
    { "type": "dropoff", "remark": "Dropoff at Nashville, TN",        "day": 2, "time_start": "10:00", "time_end": "11:00", "leg_index": 1, "cumulative_miles": 542.0,  "coords": [-86.7816, 36.1627], "location": "Nashville, TN" }
  ],
  "total_hours": 42.0,
  "violations": []
}
```

**Event statuses:** `off_duty` | `sleeper_berth` | `driving` | `on_duty`

**Stop types:** `start` | `pickup` | `dropoff` | `fuel` | `break` | `rest`

**Stop fields:**

| Field | Description |
|-------|-------------|
| `type` | Stop category |
| `remark` | Human-readable label |
| `day` | Trip day number (1-based) |
| `time_start` | Start time of the stop as `HH:MM` |
| `time_end` | End time of the stop as `HH:MM` |
| `leg_index` | Which route leg the stop belongs to |
| `cumulative_miles` | Total miles driven from trip start to this stop |
| `coords` | `[lng, lat]` map coordinate for this stop |
| `location` | Reverse-geocoded city name for this stop |

**How stop coordinates and locations are resolved (`enrich_trip_stops` in `trips/services/ors.py`):**

- `start`, `pickup`, `dropoff` — exact geocoded coordinates from OpenRouteService; location taken directly from the request payload
- `fuel`, `break`, `rest`, `34-hr restart` — coordinates interpolated along the full route polyline using `cumulative_miles`; location resolved via `reverse_geocode` in `trips/services/ors.py`

---

## HOS Rules Implemented (FMCSA 49 CFR Part 395)

The algorithm in `trips/utils/hos_calculator.py` enforces the full 70-hour/8-day
property carrier ruleset using a 30-minute slot-based scheduler.

| Rule | Implementation |
|------|---------------|
| 11-hour driving limit | Clock 2: `drive_slots_today` caps at 22 slots (11 hrs) per shift |
| 14-hour on-duty window | Clock 1: no driving allowed 14 hrs after first duty of shift |
| 30-minute break | Required after 8 cumulative driving hours; on-duty stops reset the counter |
| 10-hour off-duty rest | Inserted automatically when Clock 1 or Clock 2 is exhausted |
| 70-hour / 8-day cycle | Clock 3: `cycle_slots` accumulates all on-duty + driving time; resets after 34-hr restart |
| Fueling | Stop every 975 miles (slightly early to avoid boundary collisions), 30 min on-duty |
| Pickup / Dropoff | 1 hour each, on-duty not driving |

**Custom rules (beyond FMCSA minimum):**
- Driver starts at 06:00 each day
- No driving between 23:00 and 05:00 (curfew rest inserted automatically)

### How the scheduler works

Everything is measured in 30-minute slots. The scheduler walks through an ordered
list of raw work segments (pre-trip, drive chunks, fuel stops, pickup, dropoff,
post-trip) and places them onto a flat timeline slot by slot, inserting rests and
breaks whenever any clock is exhausted. The flat timeline is then sliced into
24-hour `DaySheet` objects for the response.

### `current_cycle_used`

This field seeds Clock 3 (the 70-hr cycle). It represents on-duty hours the driver
has already accumulated before this trip starts.

```
cycle_slots = round(current_cycle_used / 0.5)
```

Every on-duty and driving slot increments `cycle_slots` by 1. When it reaches 140
(70 hrs × 2 slots/hr), a 34-hour restart is inserted and the counter resets to 0.

> **Note:** The current implementation is a monotonic accumulator seeded from
> `current_cycle_used`, not a true sliding 8-day window. For trips under 8 days
> this is equivalent. Longer trips without a restart may trigger the 34-hr rest
> slightly earlier than the regulation strictly requires.

---

## Local Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- OpenRouteService API key (free at https://openrouteservice.org/dev/#/signup)

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd truck-trip-planner-back
uv sync
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
SECRET_KEY=your-django-secret-key
DEBUG=True
API_KEY=your-openrouteservice-api-key
URL_BASE=https://api.openrouteservice.org
CORS_ALLOWED_ORIGINS=http://localhost:5173
```

### 3. Start the dev server

```bash
uv run python manage.py runserver
```

The API is now available at `http://localhost:8000`.

---

## Running Tests

Tests cover `build_raw_segments()` and `schedule_drive_segment()` with 13 test cases
including fuel stop logic, break insertion, rest triggers, and curfew behavior.

```bash
uv run pytest trips/utils/test_hos_calculator.py -v
```

---

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | Django secret key |
| `DEBUG` | No | `True` for development, `False` for production |
| `ALLOWED_HOSTS` | No | Comma-separated allowed hostnames (default: `localhost,127.0.0.1`) |
| `API_KEY` | Yes | OpenRouteService API key |
| `URL_BASE` | Yes | ORS base URL (`https://api.openrouteservice.org`) |
| `CORS_ALLOWED_ORIGINS` | No | Comma-separated allowed frontend origins (default: `http://localhost:5173`) |

---

## Deployment (Railway)

The app is containerised. Railway will build from the `Dockerfile` and run Gunicorn.

Set the following environment variables in the Railway dashboard:

```
SECRET_KEY=<long random string>
DEBUG=False
ALLOWED_HOSTS=<your-app>.up.railway.app
API_KEY=<openrouteservice key>
URL_BASE=https://api.openrouteservice.org
CORS_ALLOWED_ORIGINS=https://<your-frontend>.vercel.app
```

No database or migrations are required — the API is stateless.
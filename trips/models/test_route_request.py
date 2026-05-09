"""
Tests for RouteRequest input validation and sanitization.
All three location fields share the same sanitize_location validator.
Tests use current_location as the representative field unless noted.
"""
import pytest
from pydantic import ValidationError
from trips.models.route_request import RouteRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_request(**overrides) -> RouteRequest:
    """Return a valid RouteRequest, optionally overriding any field."""
    defaults = {
        "current_location":  "Chicago, IL",
        "pickup_location":   "St. Louis, MO",
        "dropoff_location":  "Nashville, TN",
        "current_cycle_used": 0.0,
    }
    defaults.update(overrides)
    return RouteRequest(**defaults)


# ---------------------------------------------------------------------------
# Test 1 — Single quotes are removed
# ---------------------------------------------------------------------------

def test_single_quote_removed():
    """
    Apostrophes in city names (e.g. O'Hare) must be stripped from all
    three location fields.
    """
    r = make_request(
        current_location="O'Hare, IL",
        pickup_location="St. Mary's, MO",
        dropoff_location="King's Landing, TN",
    )
    assert r.current_location  == "OHare, IL"
    assert r.pickup_location   == "St. Marys, MO"
    assert r.dropoff_location  == "Kings Landing, TN"


# ---------------------------------------------------------------------------
# Test 2 — Slash is replaced with ", "
# ---------------------------------------------------------------------------

def test_slash_replaced_with_comma_space():
    """
    A forward slash used as a separator (e.g. Chicago/IL) must become ", ".
    Applies to all three location fields.
    """
    r = make_request(
        current_location="Chicago/IL",
        pickup_location="St. Louis/MO",
        dropoff_location="Nashville/TN",
    )
    assert r.current_location  == "Chicago, IL"
    assert r.pickup_location   == "St. Louis, MO"
    assert r.dropoff_location  == "Nashville, TN"


# ---------------------------------------------------------------------------
# Test 3 — Quote and slash together
# ---------------------------------------------------------------------------

def test_single_quote_and_slash_combined():
    """
    Input containing both a quote and a slash must have both sanitized
    in a single pass.
    """
    r = make_request(current_location="O'Hare/IL")
    assert r.current_location == "OHare, IL"


# ---------------------------------------------------------------------------
# Test 4 — Clean input passes through unchanged
# ---------------------------------------------------------------------------

def test_clean_input_unchanged():
    """
    Well-formed locations that need no sanitization must not be modified.
    """
    r = make_request(
        current_location="Chicago, IL",
        pickup_location="St. Louis, MO",
        dropoff_location="Nashville, TN",
    )
    assert r.current_location  == "Chicago, IL"
    assert r.pickup_location   == "St. Louis, MO"
    assert r.dropoff_location  == "Nashville, TN"


# ---------------------------------------------------------------------------
# Test 5 — current_cycle_used rejects negative values
# ---------------------------------------------------------------------------

def test_cycle_used_rejects_negative():
    with pytest.raises(ValidationError) as exc_info:
        make_request(current_cycle_used=-1.0)
    assert "current_cycle_used" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 6 — current_cycle_used rejects values above 70
# ---------------------------------------------------------------------------

def test_cycle_used_rejects_above_70():
    with pytest.raises(ValidationError) as exc_info:
        make_request(current_cycle_used=70.1)
    assert "current_cycle_used" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 7 ⚠ GAP — leading/trailing whitespace is NOT stripped
# ---------------------------------------------------------------------------

def test_leading_trailing_whitespace_stripped():
    """
    Users often copy-paste locations with surrounding spaces.
    strip() removes them before any other sanitization runs.
    """
    r = make_request(current_location="  Chicago, IL  ")
    assert r.current_location == "Chicago, IL"


# ---------------------------------------------------------------------------
# Test 8 — tabs and newlines from copy-paste are replaced with a space
# ---------------------------------------------------------------------------

def test_tabs_and_newlines_replaced_with_space():
    """
    Copy-pasting from a spreadsheet or browser can inject \\t or \\n.
    Both are replaced with a regular space.
    """
    r = make_request(current_location="Chicago\tIL")
    assert r.current_location == "Chicago IL"

    r2 = make_request(current_location="Chicago\nIL")
    assert r2.current_location == "Chicago IL"


# ---------------------------------------------------------------------------
# Test 9 — consecutive slashes collapse to a single ", "
# ---------------------------------------------------------------------------

def test_consecutive_slashes_collapse():
    """
    Chicago//IL must not produce the malformed "Chicago, , IL".
    The while-loop in sanitize_location collapses repeated ", , " sequences.
    """
    r = make_request(current_location="Chicago//IL")
    assert r.current_location == "Chicago, IL"

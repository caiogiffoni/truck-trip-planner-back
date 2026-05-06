"""
Tests for build_raw_segments() and schedule_drive_segment() in hos_calculator.py
"""
import pytest
from trips.utils.hos_calculator import (
    build_raw_segments,
    schedule_drive_segment,
    State,
    RawSegment,
    Status,
    PRE_TRIP_SLOTS,
    POST_TRIP_SLOTS,
    PICKUP_SLOTS,
    DROPOFF_SLOTS,
    FUEL_SLOTS,
    FUEL_EARLY_MILES,
    MAX_DRIVE_SLOTS,
    BREAK_AFTER_SLOTS,
    REST_SLOTS,
    NO_DRIVE_AFTER_SLOT,
    NO_DRIVE_BEFORE_SLOT,
    SLOT,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def two_leg_trip(leg0_miles=200.0, leg1_miles=200.0):
    """Helper: build a standard two-leg route dict."""
    return [
        {"miles": leg0_miles, "drive_hrs": leg0_miles / 55.0, "from": "Chicago, IL",  "to": "St. Louis, MO"},
        {"miles": leg1_miles, "drive_hrs": leg1_miles / 55.0, "from": "St. Louis, MO", "to": "Nashville, TN"},
    ]


# ---------------------------------------------------------------------------
# Test 1 — Segment order and required stops for a short trip (no fuel)
# ---------------------------------------------------------------------------

def test_segment_order_short_trip():
    """
    For a trip under 975 miles total, there should be no fuel stops.
    Expected segment order:
      pre-trip → drive (leg 0) → pickup → drive (leg 1) → dropoff → post-trip
    """
    legs = two_leg_trip(200.0, 200.0)
    segs = build_raw_segments(legs, "St. Louis, MO", "Nashville, TN")

    seg_types  = [s.seg_type  for s in segs]
    seg_remarks = [s.remark for s in segs]

    # Must begin with pre-trip and end with post-trip
    assert seg_remarks[0]  == "Pre-trip inspection"
    assert seg_remarks[-1] == "Post-trip inspection"

    # Drive segments must be present
    assert "drive" in seg_types

    # Pickup and dropoff must appear (in that order)
    on_duty_remarks = [s.remark for s in segs if s.seg_type == "on_duty"]
    pickup_idx  = next(i for i, r in enumerate(on_duty_remarks) if "Pickup"  in r)
    dropoff_idx = next(i for i, r in enumerate(on_duty_remarks) if "Dropoff" in r)
    assert pickup_idx < dropoff_idx

    # No fuel stops for a 400-mile trip
    assert not any("Fuel" in s.remark for s in segs)


# ---------------------------------------------------------------------------
# Test 2 — Fuel stop is inserted when a leg crosses the 975-mile threshold
# ---------------------------------------------------------------------------

def test_fuel_stop_inserted_on_long_leg():
    """
    A single leg of 1100 miles crosses the FUEL_EARLY_MILES (975 mi) boundary.
    The scheduler must split the drive and insert exactly one fuel stop.
    """
    legs = [
        {"miles": 1100.0, "drive_hrs": 20.0, "from": "Los Angeles, CA", "to": "Dallas, TX"},
        {"miles": 100.0,  "drive_hrs":  1.8, "from": "Dallas, TX",       "to": "Austin, TX"},
    ]
    segs = build_raw_segments(legs, "Dallas, TX", "Austin, TX")

    fuel_segs = [s for s in segs if "Fuel" in s.remark]
    assert len(fuel_segs) == 1, f"Expected 1 fuel stop, got {len(fuel_segs)}"

    # Fuel stop is on-duty not driving
    assert fuel_segs[0].seg_type == "on_duty"
    assert fuel_segs[0].slots    == FUEL_SLOTS


# ---------------------------------------------------------------------------
# Test 3 — Miles are distributed correctly across drive segments
# ---------------------------------------------------------------------------

def test_drive_segment_miles_sum_to_leg_total():
    """
    The sum of miles across all drive segments must equal the total route miles.
    Also verifies that each drive RawSegment carries a positive miles value.
    """
    leg0_miles = 500.0
    leg1_miles = 600.0
    legs = two_leg_trip(leg0_miles, leg1_miles)

    segs = build_raw_segments(legs, "St. Louis, MO", "Nashville, TN")

    drive_segs = [s for s in segs if s.seg_type == "drive"]
    total_drive_miles = sum(s.miles for s in drive_segs)

    assert abs(total_drive_miles - (leg0_miles + leg1_miles)) < 0.01, (
        f"Drive miles {total_drive_miles} != expected {leg0_miles + leg1_miles}"
    )

    # Every drive chunk must carry positive miles
    for s in drive_segs:
        assert s.miles > 0, f"Drive segment has zero miles: {s}"


# ---------------------------------------------------------------------------
# Test 4 — Multiple fuel stops on a very long trip
# ---------------------------------------------------------------------------

def test_multiple_fuel_stops_on_long_trip():
    """
    A 2100-mile trip crosses the 975-mile boundary twice (at ~975 mi and ~1950 mi),
    so exactly 2 fuel stops should be inserted.
    """
    legs = [
        {"miles": 1500.0, "drive_hrs": 27.3, "from": "New York, NY",   "to": "Dallas, TX"},
        {"miles":  600.0, "drive_hrs": 10.9, "from": "Dallas, TX",      "to": "El Paso, TX"},
    ]
    segs = build_raw_segments(legs, "Dallas, TX", "El Paso, TX")

    fuel_segs = [s for s in segs if "Fuel" in s.remark]
    assert len(fuel_segs) == 2, f"Expected 2 fuel stops, got {len(fuel_segs)}"


# ---------------------------------------------------------------------------
# Test 5 — No fuel stop appended after the very last mile
# ---------------------------------------------------------------------------

def test_no_fuel_stop_after_final_mile():
    """
    If the trip ends exactly at a fuel boundary (leg 1 ends right at 975 mi),
    no trailing fuel stop should appear after the dropoff/post-trip.
    The guard `total_miles_remaining > 0.001` prevents a useless stop at the end.
    """
    # 975 miles total — hits the boundary exactly on the last drive slot
    legs = [
        {"miles": FUEL_EARLY_MILES, "drive_hrs": FUEL_EARLY_MILES / 55.0,
         "from": "City A", "to": "City B"},
        {"miles": 1.0, "drive_hrs": 0.02, "from": "City B", "to": "City C"},
    ]
    segs = build_raw_segments(legs, "City B", "City C")

    # The last on-duty segment must be post-trip, not a fuel stop
    assert segs[-1].remark == "Post-trip inspection"

    # Whatever fuel stops exist, none should appear after the dropoff
    dropoff_pos = next(i for i, s in enumerate(segs) if "Dropoff" in s.remark)
    trailing = segs[dropoff_pos + 1:]
    assert not any("Fuel" in s.remark for s in trailing)


# ---------------------------------------------------------------------------
# Test 6 — Leg index is assigned correctly to each segment
# ---------------------------------------------------------------------------

def test_leg_index_on_segments():
    """
    Drive and on-duty segments carry the leg_index of the leg they belong to.
    Leg 0 segments (before pickup) must have leg_index=0.
    Leg 1 segments (after pickup) must have leg_index=1.
    Pre/post-trip segments default to leg_index=0.
    """
    legs = two_leg_trip(300.0, 300.0)
    segs = build_raw_segments(legs, "St. Louis, MO", "Nashville, TN")

    # Find pickup position
    pickup_pos = next(i for i, s in enumerate(segs) if "Pickup" in s.remark)

    # All drive segs before pickup belong to leg 0
    for s in segs[:pickup_pos]:
        if s.seg_type == "drive":
            assert s.leg_index == 0, f"Expected leg 0 before pickup, got {s.leg_index}"

    # All drive segs after pickup belong to leg 1
    for s in segs[pickup_pos + 1:]:
        if s.seg_type == "drive":
            assert s.leg_index == 1, f"Expected leg 1 after pickup, got {s.leg_index}"


# ---------------------------------------------------------------------------
# Test 7 — Pickup and dropoff remarks contain the location names
# ---------------------------------------------------------------------------

def test_pickup_dropoff_remarks_contain_location_names():
    """
    The pickup segment remark must contain the pickup_location string,
    and the dropoff remark must contain the dropoff_location string.
    """
    legs = two_leg_trip(200.0, 200.0)
    segs = build_raw_segments(legs, "St. Louis, MO", "Nashville, TN")

    pickup_seg  = next(s for s in segs if "Pickup"  in s.remark)
    dropoff_seg = next(s for s in segs if "Dropoff" in s.remark)

    assert "St. Louis, MO" in pickup_seg.remark
    assert "Nashville, TN" in dropoff_seg.remark


# ---------------------------------------------------------------------------
# Test 8 — Slot counts are consistent with drive time
# ---------------------------------------------------------------------------

def test_drive_slot_counts_match_drive_hours():
    """
    Each drive RawSegment's slot count must be at least 1 and must roughly
    correspond to the proportional drive time (within ±1 slot rounding error).
    Verifies that `max(1, round(chunk_hours / SLOT))` is applied correctly.
    """
    leg_miles = 110.0
    leg_hrs   = 2.0   # 55 mph → 2 hours → 4 slots exactly
    legs = [
        {"miles": leg_miles, "drive_hrs": leg_hrs, "from": "A", "to": "B"},
        {"miles": 10.0,      "drive_hrs": 0.18,   "from": "B", "to": "C"},
    ]
    segs = build_raw_segments(legs, "B", "C")

    drive_segs = [s for s in segs if s.seg_type == "drive" and s.leg_index == 0]
    total_slots = sum(s.slots for s in drive_segs)

    # 2 hours / 0.5 per slot = 4 slots. Allow ±1 for rounding across chunks.
    expected_slots = round(leg_hrs / 0.5)
    assert abs(total_slots - expected_slots) <= 1, (
        f"Slot count {total_slots} too far from expected {expected_slots}"
    )

    # Every individual segment must have at least 1 slot
    for s in drive_segs:
        assert s.slots >= 1


# ---------------------------------------------------------------------------
# schedule_drive_segment tests
# ---------------------------------------------------------------------------

def make_state() -> State:
    """Fresh State with no prior cycle hours."""
    return State(current_cycle_used=0.0)


def drive_seg(slots: int, miles: float = 100.0) -> RawSegment:
    return RawSegment(seg_type="drive", slots=slots, miles=miles, remark="Driving to X")


# ---------------------------------------------------------------------------
# Test 9 — Basic drive: correct number of DRIVING slots placed
# ---------------------------------------------------------------------------

def test_schedule_drive_places_correct_slots():
    """
    Scheduling a 4-slot drive segment must produce exactly 4 DRIVING slots
    in the timeline, each carrying an equal share of the total miles.
    """
    state = make_state()
    seg   = drive_seg(slots=4, miles=110.0)

    schedule_drive_segment(state, seg)

    driving_slots = [s for s in state.timeline if s.status == Status.DRIVING]
    assert len(driving_slots) == 4

    # Miles should be evenly distributed (110 / 4 = 27.5 each)
    for s in driving_slots:
        assert abs(s.miles - 27.5) < 0.01


# ---------------------------------------------------------------------------
# Test 10 — Mandatory 30-min break is inserted after 8 hours of driving
# ---------------------------------------------------------------------------

def test_mandatory_break_inserted_after_8hrs():
    """
    BREAK_AFTER_SLOTS = 16 (8 hours).  Driving 17 consecutive slots must
    trigger one ON_DUTY break slot before the 17th drive slot is placed.
    """
    state = make_state()
    # Drive 17 slots — break must fire after slot 16
    seg = drive_seg(slots=17, miles=500.0)

    schedule_drive_segment(state, seg)

    statuses = [s.status for s in state.timeline]

    # Must contain at least one ON_DUTY break
    assert Status.ON_DUTY in statuses

    # The break must appear before any driving slot that follows it
    first_break = next(i for i, s in enumerate(state.timeline) if s.status == Status.ON_DUTY)
    drive_after_break = [s for s in state.timeline[first_break + 1:] if s.status == Status.DRIVING]
    assert len(drive_after_break) > 0   # driving continues after the break


# ---------------------------------------------------------------------------
# Test 11 — 10-hr rest is inserted when the 11-hr drive limit is reached
# ---------------------------------------------------------------------------

def test_rest_inserted_when_11hr_drive_limit_hit():
    """
    Preload drive_slots_today to MAX_DRIVE_SLOTS (22 = 11 hrs).
    The very first iteration of schedule_drive_segment must detect
    can_drive() == False and insert a 10-hr off-duty rest (REST_SLOTS = 20)
    before any driving slots are placed.
    """
    state = make_state()
    state.drive_slots_today = MAX_DRIVE_SLOTS   # clock 2 already exhausted

    seg = drive_seg(slots=2, miles=60.0)
    schedule_drive_segment(state, seg)

    off_duty_slots = [s for s in state.timeline if s.status == Status.OFF_DUTY]
    driving_slots  = [s for s in state.timeline if s.status == Status.DRIVING]

    # A rest must have been inserted
    assert len(off_duty_slots) >= REST_SLOTS, (
        f"Expected at least {REST_SLOTS} off-duty slots, got {len(off_duty_slots)}"
    )

    # Driving must still complete after the rest
    assert len(driving_slots) == 2

    # All off-duty slots must precede the first driving slot
    first_drive_idx = next(i for i, s in enumerate(state.timeline) if s.status == Status.DRIVING)
    assert all(s.status == Status.OFF_DUTY for s in state.timeline[:first_drive_idx])


# ---------------------------------------------------------------------------
# Test 12 — Full input/output snapshot for schedule_drive_segment
# ---------------------------------------------------------------------------

def test_schedule_drive_full_output_snapshot(capsys):
    """
    Full snapshot: drive 9 hours (18 slots, 495 miles) from a fresh state.
    At slot 16 the break fires, producing an ON_DUTY slot in the middle.
    Run with -s to see the printed timeline.

    Input:
      State : fresh (no prior hours)
      Segment: 18 slots, 495 miles, "Driving to Nashville, TN"

    Expected timeline (each row = one 30-min slot):
      slots  0-15  → DRIVING  (8 hrs, break counter reaches 16)
      slot  16     → ON_DUTY  "30-min break"
      slots 17-33  → DRIVING  (remaining 17 drive slots)
    """
    state = make_state()
    seg   = RawSegment(seg_type="drive", slots=18, miles=495.0, remark="Driving to Nashville, TN")

    schedule_drive_segment(state, seg)

    # ── Print the full timeline so you can inspect it with -s ────────────────
    print("\n─── schedule_drive_segment: 18-slot drive (9 hrs, 495 mi) ───")
    print(f"{'idx':>4}  {'slot_of_day':>11}  {'status':<12}  {'miles':>7}  remark")
    print("─" * 65)
    for s in state.timeline:
        abs_hour  = s.index * 0.5
        h_of_day  = abs_hour % 24
        time_str  = f"{int(h_of_day):02d}:{int((h_of_day % 1) * 60):02d}"
        print(f"{s.index:>4}  {time_str:>11}  {s.status.value:<12}  {s.miles:>7.2f}  {s.remark}")

    print(f"\nTotal timeline slots : {len(state.timeline)}")
    print(f"Drive slots today    : {state.drive_slots_today}")
    print(f"Break counter        : {state.break_counter}")
    print(f"Cycle slots used     : {state.cycle_slots}")

    # ── Assertions ────────────────────────────────────────────────────────────
    driving  = [s for s in state.timeline if s.status == Status.DRIVING]
    on_duty  = [s for s in state.timeline if s.status == Status.ON_DUTY]

    # Exactly 18 driving slots placed (the segment's full quota)
    assert len(driving) == 18

    # Exactly 1 break slot (break_counter hit 16 after the 16th drive slot)
    assert len(on_duty) == 1
    assert "break" in on_duty[0].remark.lower()

    # Break falls between drive slot 16 and drive slot 17
    break_pos = next(i for i, s in enumerate(state.timeline) if s.status == Status.ON_DUTY)
    assert break_pos == 16   # first 16 slots are driving, then the break

    # Miles are evenly spread across all driving slots (495 / 18 = 27.5 each)
    for s in driving:
        assert abs(s.miles - 27.5) < 0.01

    # Clocks updated correctly
    assert state.drive_slots_today == 18
    assert state.cycle_slots       == 19   # 18 driving + 1 on_duty break


# ---------------------------------------------------------------------------
# Test 13 — Curfew rest ends exactly at 05:00 the next morning
# ---------------------------------------------------------------------------

def test_curfew_rest_ends_at_5am(capsys):
    """
    Manually position the cursor at 23:00 (NO_DRIVE_AFTER_SLOT = 46 within the day),
    then schedule a drive. The curfew must fire immediately and insert exactly
    enough OFF_DUTY slots to land at 05:00 the next day.

    23:00 → 05:00 next day = 6 hours = 12 slots.

    After the rest the cursor must sit at slot_of_day == NO_DRIVE_BEFORE_SLOT (10 = 05:00)
    and all inserted slots must be OFF_DUTY with a curfew remark.
    """
    state = make_state()
    # Jump the cursor to 23:00 on Day 1  (slot 46 within day 0 = absolute slot 46)
    state.cursor = NO_DRIVE_AFTER_SLOT          # slot 46 = 23:00

    seg = drive_seg(slots=4, miles=110.0)
    schedule_drive_segment(state, seg)

    # ── Print for inspection (run with -s) ──────────────────────────────────
    print("\n─── Curfew rest: cursor starts at 23:00, drives 4 slots ───")
    print(f"{'abs':>4}  {'time':>5}  {'status':<12}  remark")
    print("─" * 55)
    for s in state.timeline:
        abs_hour = s.index * SLOT
        h = abs_hour % 24
        print(f"{s.index:>4}  {int(h):02d}:{int((h%1)*60):02d}  {s.status.value:<12}  {s.remark}")

    # Find the curfew block
    curfew_slots = [s for s in state.timeline if "Curfew" in s.remark]
    drive_slots  = [s for s in state.timeline if s.status == Status.DRIVING]

    # 23:00 → 05:00 next day = 12 slots
    expected_curfew_slots = (48 - NO_DRIVE_AFTER_SLOT) + NO_DRIVE_BEFORE_SLOT
    assert len(curfew_slots) == expected_curfew_slots, (
        f"Expected {expected_curfew_slots} curfew slots, got {len(curfew_slots)}"
    )

    # All curfew slots must be OFF_DUTY
    assert all(s.status == Status.OFF_DUTY for s in curfew_slots)

    # First drive slot must happen at or after 05:00 (slot_of_day >= 10)
    first_drive = drive_slots[0]
    assert first_drive.index % 48 >= NO_DRIVE_BEFORE_SLOT, (
        f"Driving started before 05:00: slot_of_day={first_drive.index % 48}"
    )

    # All 4 drive slots must eventually be placed
    assert len(drive_slots) == 4

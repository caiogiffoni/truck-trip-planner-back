"""
Tests for build_raw_segments(), schedule_drive_segment(), schedule_on_duty_segment(),
build_day_sheets(), State helpers, and calculate_trip() in hos_calculator.py
"""
import pytest
from trips.utils.hos_calculator import (
    build_raw_segments,
    schedule_drive_segment,
    schedule_on_duty_segment,
    build_day_sheets,
    calculate_trip,
    State,
    RawSegment,
    DaySheet,
    Slot,
    Stop,
    TripResult,
    Status,
    PRE_TRIP_SLOTS,
    POST_TRIP_SLOTS,
    PICKUP_SLOTS,
    DROPOFF_SLOTS,
    FUEL_SLOTS,
    FUEL_EARLY_MILES,
    MAX_DRIVE_SLOTS,
    MAX_WINDOW_SLOTS,
    MAX_CYCLE_SLOTS,
    BREAK_AFTER_SLOTS,
    REST_SLOTS,
    RESTART_SLOTS,
    NO_DRIVE_AFTER_SLOT,
    NO_DRIVE_BEFORE_SLOT,
    SHIFT_START_SLOT,
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


# ---------------------------------------------------------------------------
# Test 14 — schedule_on_duty_segment places ON_DUTY slots and updates clocks
# ---------------------------------------------------------------------------

def test_schedule_on_duty_basic():
    """
    Scheduling a 2-slot on-duty segment (e.g. pickup) must:
      - place exactly 2 ON_DUTY slots in the timeline
      - advance the cycle clock by 2
      - reset the break counter to 0
      - NOT affect drive_slots_today
    """
    state = make_state()
    state.break_counter = 10   # simulate prior driving

    seg = RawSegment(seg_type="on_duty", slots=2, remark="Pickup at City X", leg_index=0)
    schedule_on_duty_segment(state, seg)

    on_duty = [s for s in state.timeline if s.status == Status.ON_DUTY]
    assert len(on_duty) == 2
    assert state.cycle_slots      == 2   # Clock 3 advanced
    assert state.break_counter    == 0   # break counter reset by on-duty work
    assert state.drive_slots_today == 0  # Clock 2 unchanged


# ---------------------------------------------------------------------------
# Test 15 — 14-hr window exhaustion triggers a 10-hr rest mid-drive
# ---------------------------------------------------------------------------

def test_14hr_window_triggers_rest():
    """
    Pre-load the window so only 1 slot remains.  Driving 2 slots must:
      1. Drive 1 slot (window consumed)
      2. Detect window_slots_remaining == 0 → insert 10-hr rest (REST_SLOTS)
      3. Drive the remaining slot after the rest
    """
    state = make_state()
    # Position cursor safely inside daytime to avoid curfew interference.
    # Set window_start so 27 of the 28 window slots are already used.
    state.cursor       = 60   # 12:00 on day 2 (slot_of_day = 12)
    state.window_start = 33   # 60 - 27 = 33  →  window_slots_remaining = 1
    state.shift_active = True

    seg = drive_seg(slots=2, miles=60.0)
    schedule_drive_segment(state, seg)

    off_duty = [s for s in state.timeline if s.status == Status.OFF_DUTY]
    driving  = [s for s in state.timeline if s.status == Status.DRIVING]

    assert len(off_duty) >= REST_SLOTS, (
        f"Expected at least {REST_SLOTS} off-duty slots, got {len(off_duty)}"
    )
    assert len(driving) == 2, f"Expected 2 driving slots, got {len(driving)}"

    # The rest must appear BETWEEN the two drive slots
    first_drive  = next(i for i, s in enumerate(state.timeline) if s.status == Status.DRIVING)
    last_drive   = max(i for i, s in enumerate(state.timeline) if s.status == Status.DRIVING)
    rest_between = [
        s for s in state.timeline[first_drive + 1:last_drive]
        if s.status == Status.OFF_DUTY
    ]
    assert len(rest_between) >= REST_SLOTS


# ---------------------------------------------------------------------------
# Test 16 — calculate_trip end-to-end: correct stop types on a short trip
# ---------------------------------------------------------------------------

def test_calculate_trip_end_to_end_short():
    """
    End-to-end smoke test for a short, rule-compliant trip (400 miles total).
    The result must:
      - contain at least 1 day sheet
      - include a 'start', 'pickup', and 'dropoff' stop
      - report no violations
      - total_hours must be positive
    """
    legs = two_leg_trip(200.0, 200.0)
    result = calculate_trip(legs, "St. Louis, MO", "Nashville, TN", current_cycle_used=0.0)

    assert len(result.days) >= 1

    stop_types = {s.stop_type for s in result.stops}
    assert "start"   in stop_types
    assert "pickup"  in stop_types
    assert "dropoff" in stop_types

    assert result.violations == []

    d = result.to_dict()
    assert d["total_hours"] > 0


# ---------------------------------------------------------------------------
# Test 17 — 70-hr cycle exhaustion triggers a 34-hr restart mid-drive
# ---------------------------------------------------------------------------

def test_70hr_cycle_triggers_34hr_restart():
    """
    Pre-load cycle_slots to MAX_CYCLE_SLOTS (140 = 70 hrs).
    The very first drive iteration must detect can_drive() == False,
    insert a 34-hr restart (RESTART_SLOTS = 68 OFF_DUTY slots), reset
    cycle_slots to 0, then complete the 2-slot drive segment.
    """
    state = make_state()
    # Position cursor in daytime to avoid curfew on the first check.
    # cursor=24 → slot_of_day=24 (12:00 PM on day 1), safe from curfew.
    state.cursor      = 24
    state.cycle_slots = MAX_CYCLE_SLOTS   # Clock 3 already exhausted

    seg = drive_seg(slots=2, miles=60.0)
    schedule_drive_segment(state, seg)

    off_duty = [s for s in state.timeline if s.status == Status.OFF_DUTY]
    driving  = [s for s in state.timeline if s.status == Status.DRIVING]

    # Restart must be present
    assert len(off_duty) >= RESTART_SLOTS, (
        f"Expected at least {RESTART_SLOTS} off-duty slots for restart, got {len(off_duty)}"
    )

    # All drive work must still complete
    assert len(driving) == 2

    # Cycle clock was reset to 0, then incremented by 2 drive slots
    assert state.cycle_slots == 2

    # Every off-duty slot must precede the first driving slot
    first_drive_pos = next(i for i, s in enumerate(state.timeline) if s.status == Status.DRIVING)
    assert all(s.status == Status.OFF_DUTY for s in state.timeline[:first_drive_pos])


# ---------------------------------------------------------------------------
# Test 18 — build_day_sheets gaps are filled with OFF_DUTY
# ---------------------------------------------------------------------------

def test_build_day_sheets_fills_gaps_with_off_duty():
    """
    A timeline with only a handful of slots (not covering the full day) must
    produce a DaySheet where every missing slot is filled as OFF_DUTY.

    Input: one ON_DUTY slot at index 0, nothing else.
    Expected: day 1 with 48 slots total; slot 0 = ON_DUTY, slots 1-47 = OFF_DUTY.
    """
    timeline = [Slot(index=0, status=Status.ON_DUTY, remark="start")]
    days = build_day_sheets(timeline)

    assert len(days) == 1
    day = days[0]
    assert len(day.slots) == 48

    assert day.slots[0].status == Status.ON_DUTY
    assert all(s.status == Status.OFF_DUTY for s in day.slots[1:])


# ---------------------------------------------------------------------------
# Test 19 — calculate_trip with high cycle_used inserts a 34-hr restart
# ---------------------------------------------------------------------------

def test_calculate_trip_high_cycle_used_triggers_restart():
    """
    Starting with 68 hours already used (136 slots — only 4 remaining in the
    70-hr cycle), even a short pre-trip inspection + a few drive slots will
    exhaust the cycle and force a 34-hr restart stop to appear in the result.
    """
    legs = two_leg_trip(200.0, 200.0)
    # 68 hrs used → only 2 hrs (4 slots) left before the 34-hr restart fires
    result = calculate_trip(
        legs, "St. Louis, MO", "Nashville, TN", current_cycle_used=68.0
    )

    restart_stops = [s for s in result.stops if "restart" in s.remark.lower()]
    assert len(restart_stops) >= 1, "Expected at least one 34-hr restart stop"

    # Trip must still complete — pickup and dropoff must both be present
    stop_types = {s.stop_type for s in result.stops}
    assert "pickup"  in stop_types
    assert "dropoff" in stop_types


# ---------------------------------------------------------------------------
# Test 20 — insert_34hr_restart resets all three HOS clocks
# ---------------------------------------------------------------------------

def test_insert_34hr_restart_resets_all_clocks():
    """
    insert_34hr_restart() must:
      - push exactly RESTART_SLOTS (68) OFF_DUTY slots
      - reset cycle_slots to 0   (Clock 3)
      - reset drive_slots_today to 0  (Clock 2)
      - reset break_counter to 0
      - set shift_active to False
    insert_rest() must NOT reset cycle_slots — only the 34-hr restart does.
    """
    state = make_state()
    state.cycle_slots       = 100
    state.drive_slots_today = 18
    state.break_counter     = 12
    state.shift_active      = True

    state.insert_34hr_restart("test reason")

    off_duty = [s for s in state.timeline if s.status == Status.OFF_DUTY]
    assert len(off_duty) == RESTART_SLOTS

    assert state.cycle_slots       == 0     # Clock 3 wiped
    assert state.drive_slots_today == 0     # Clock 2 wiped
    assert state.break_counter     == 0
    assert state.shift_active      is False


# ---------------------------------------------------------------------------
# Test 21 — DaySheet.to_dict() compresses consecutive same-status slots
# ---------------------------------------------------------------------------

def test_daysheet_to_dict_compresses_consecutive_slots():
    """
    Four consecutive DRIVING slots with the same remark must collapse into
    a single event block with correct start/end hours and summed miles.

    Slots 24–27 (absolute) on day 1 (day_offset=0):
      start = 24 * 0.5 = 12.0 hrs
      end   = 28 * 0.5 = 14.0 hrs
      miles = 4 × 27.5 = 110.0
    """
    driving_slots = [
        Slot(index=i, status=Status.DRIVING, remark="Driving to X", miles=27.5)
        for i in range(24, 28)
    ]
    sheet = DaySheet(day=1, date_offset_days=0, slots=driving_slots)
    result = sheet.to_dict()

    events = result["events"]
    driving_events = [e for e in events if e["status"] == "driving"]
    assert len(driving_events) == 1

    ev = driving_events[0]
    assert ev["start"] == 12.0
    assert ev["end"]   == 14.0
    assert abs(ev["miles"] - 110.0) < 0.01


# ---------------------------------------------------------------------------
# Test 22 — Curfew rest from before 05:00 fills exactly to 05:00
# ---------------------------------------------------------------------------

def test_curfew_rest_from_before_5am():
    """
    If the cursor is before 05:00 (slot_of_day < NO_DRIVE_BEFORE_SLOT),
    past_curfew() returns True and insert_curfew_rest() must fill only
    enough slots to reach 05:00 — NOT a full 23:00→05:00 block.

    Cursor at absolute slot 6 (03:00 on day 1, slot_of_day=6):
      slots_needed = NO_DRIVE_BEFORE_SLOT - 6 = 10 - 6 = 4
    After the curfew rest the first drive slot must land at slot_of_day >= 10.
    """
    state = make_state()
    state.cursor = 6   # 03:00 — inside the pre-dawn no-drive window

    seg = drive_seg(slots=2, miles=60.0)
    schedule_drive_segment(state, seg)

    curfew_slots = [s for s in state.timeline if "Curfew" in s.remark]
    expected_curfew = NO_DRIVE_BEFORE_SLOT - 6   # = 4
    assert len(curfew_slots) == expected_curfew, (
        f"Expected {expected_curfew} curfew slots, got {len(curfew_slots)}"
    )

    driving = [s for s in state.timeline if s.status == Status.DRIVING]
    assert len(driving) == 2
    assert driving[0].index % 48 >= NO_DRIVE_BEFORE_SLOT

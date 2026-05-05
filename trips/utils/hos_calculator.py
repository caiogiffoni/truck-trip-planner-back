"""
HOS (Hours of Service) Calculator — Slot-based implementation
FMCSA 49 CFR Part 395 — Property Carrier, 70hr/8-day ruleset

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW TIME WORKS IN THIS FILE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Everything is measured in 30-minute SLOTS.

  slot 0   = 00:00–00:30  Day 1
  slot 1   = 00:30–01:00  Day 1
  slot 12  = 06:00–06:30  Day 1  ← driver starts here
  slot 46  = 23:00–23:30  Day 1  ← driving NOT allowed at or after this
  slot 47  = 23:30–00:00  Day 1
  slot 48  = 00:00–00:30  Day 2
  ...

To convert a slot index to a human time:
  abs_hour  = slot_index * 0.5
  day       = abs_hour // 24  (0-based)
  hour      = abs_hour % 24
  e.g. slot 25 → 12.5 hrs → Day 1, 12:30

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
THE THREE HOS CLOCKS (all measured in slots)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Clock 1 — 14-hr WINDOW (28 slots)
  Starts when the driver goes on-duty for the first time in a shift.
  Once 28 slots have passed since window_start, no more driving.
  Resets after a 10-hr rest.

Clock 2 — 11-hr DRIVE LIMIT (22 slots)
  Counts only driving slots in the current shift.
  Once 22 drive slots are used, no more driving.
  Resets after a 10-hr rest.

Clock 3 — 70-hr / 8-DAY CYCLE (140 slots)
  Counts ALL on-duty and driving slots over a rolling 8-day window.
  Once 140 slots are used, no more driving until a 34-hr restart.
  Only resets after 34 consecutive off-duty hours (68 slots).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
THE BREAK COUNTER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FMCSA requires a 30-min break after 8 cumulative hours of driving.
  DRIVING slot  → break_counter += 1
  ON_DUTY slot  → break_counter  = 0  (ANY on-duty stop counts as a break)
  OFF_DUTY slot → break_counter  = 0

This means: if the driver stops for a pickup (1 hr on-duty), that
automatically satisfies the break requirement. No extra break needed.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CUSTOM RULES (beyond FMCSA minimums)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [CUSTOM] Driver starts on-duty at 06:00 every day (slot 12 within each day).
# [CUSTOM] No driving at or after 23:00 (slot 46 within each day).
          If a drive slot would land at 23:00+, stop and rest instead.
          The curfew applies per calendar day.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FLOW OVERVIEW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. build_raw_segments()   → ordered list of work to do (pre-trip, drive, pickup, etc.)
2. calculate_trip()       → feeds segments into the scheduler one by one
3. schedule_drive_segment()    → places driving slots, inserts rests/breaks as needed
4. schedule_on_duty_segment()  → places on-duty slots, resets break counter
5. build_day_sheets()     → slices flat timeline into 24-hr log sheets
"""

from dataclasses import dataclass, field
from typing import List
from enum import Enum


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# One time slot = 30 minutes. All durations below are in slots.
SLOT = 0.5

# [CUSTOM] Driver starts their shift at 06:00 each day = slot 12 within the day.
# Delete this and SHIFT_START_HOUR usage in calculate_trip() to let driver start at midnight.
SHIFT_START_HOUR  = 6.0
SHIFT_START_SLOT  = int(SHIFT_START_HOUR / SLOT)   # = 12

# [CUSTOM] No driving allowed at or after 23:00 = slot 46 within the day.
# Delete this and the curfew check in schedule_drive_segment() to remove the restriction.
NO_DRIVE_AFTER_HOUR = 23.0
NO_DRIVE_AFTER_SLOT = int(NO_DRIVE_AFTER_HOUR / SLOT)  # = 46 within each day (0-47)
NO_DRIVE_BEFORE_HOUR = 5.0
NO_DRIVE_BEFORE_SLOT = int(NO_DRIVE_BEFORE_HOUR / SLOT)  # = 10 within each day (0-47)

# FMCSA HOS limits
MAX_DRIVE_SLOTS   = 22    # 11 hours of driving per shift
MAX_WINDOW_SLOTS  = 28    # 14-hour on-duty window per shift
BREAK_AFTER_SLOTS = 16    # mandatory 30-min break after 8 hours of driving
REST_SLOTS        = 20    # 10-hour off-duty rest (resets Clock 1 and Clock 2)
RESTART_SLOTS     = 68    # 34-hour restart (resets all 3 clocks including cycle)
MAX_CYCLE_SLOTS   = 140   # 70-hour / 8-day on-duty limit

# Trip-specific durations
FUEL_INTERVAL_MILES = 1000.0
# [CUSTOM] Refuel at 975 miles instead of exactly 1000.
# Fueling slightly early ensures the stop always lands INSIDE a drive chunk,
# never back-to-back with a pickup/dropoff at a leg boundary.
# Change to FUEL_INTERVAL_MILES to refuel at exactly 1000 miles.
FUEL_EARLY_MILES    = 975.0
FUEL_SLOTS          = 1    # 30-min fuel stop
PICKUP_SLOTS        = 2    # 1-hr pickup (on-duty not driving)
DROPOFF_SLOTS       = 2    # 1-hr dropoff (on-duty not driving)
PRE_TRIP_SLOTS      = 1    # 30-min pre-trip inspection
POST_TRIP_SLOTS     = 1    # 30-min post-trip inspection

# Safety guard — prevents infinite loops if a bug blocks progress
MAX_ITERATIONS = 10_000


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class Status(str, Enum):
    """The four duty statuses that appear on a Driver's Daily Log."""
    OFF_DUTY      = "off_duty"
    SLEEPER_BERTH = "sleeper_berth"
    DRIVING       = "driving"
    ON_DUTY       = "on_duty"    # on-duty not driving


@dataclass
class Slot:
    """
    One 30-minute block on the absolute timeline.
    index 0 = midnight Day 1, index 48 = midnight Day 2, etc.
    """
    index:  int           # absolute slot index from trip start
    status: Status
    remark: str   = ""
    miles:  float = 0.0   # miles driven — only populated for DRIVING slots


@dataclass
class Stop:
    """
    A notable event shown as a marker on the route map.
    stop_type: "start" | "pickup" | "dropoff" | "fuel" | "rest"
    """
    stop_type:   str
    remark:      str
    slot_index:  int
    day:         int
    time_of_day: str    # "HH:MM" format
    leg_index:   int = 0


@dataclass
class DaySheet:
    """
    One Driver's Daily Log sheet — exactly 48 slots (24 hours).
    The canvas renderer reads the events list from to_dict().
    """
    day:              int   # 1-based day number
    date_offset_days: int   # days from trip start, 0-based
    slots:            List[Slot] = field(default_factory=list)

    @property
    def total_miles(self) -> float:
        return sum(s.miles for s in self.slots)

    def to_dict(self) -> dict:
        """
        Compress consecutive same-status slots into event blocks.
        Times are normalized to 0.0–24.0 within the day.

        Example output:
          {"status": "driving", "start": 6.5, "end": 11.0, "remark": "...", "miles": 247.0}
        """
        if not self.slots:
            return {
                "day": self.day,
                "date_offset_days": self.date_offset_days,
                "total_miles": 0,
                "events": [],
            }

        # slot indices within this day run from (day_idx * 48) to (day_idx * 48 + 47)
        # subtracting day_offset converts them back to 0–47 for time math
        day_offset = self.date_offset_days * 48

        events    = []
        run_start  = self.slots[0].index
        run_status = self.slots[0].status
        run_remark = self.slots[0].remark
        run_miles  = self.slots[0].miles

        def flush(end_index: int):
            """Write the current run as one event block."""
            events.append({
                "status": run_status.value,
                "start":  round((run_start - day_offset) * SLOT, 4),
                "end":    round((end_index  - day_offset) * SLOT, 4),
                "remark": run_remark,
                "miles":  round(run_miles, 2),
            })

        for s in self.slots[1:]:
            if s.status == run_status and s.remark == run_remark:
                # Same block — just accumulate miles
                run_miles += s.miles
            else:
                # Status or remark changed — close current block, start new one
                flush(s.index)
                run_start  = s.index
                run_status = s.status
                run_remark = s.remark
                run_miles  = s.miles

        flush(self.slots[-1].index + 1)

        return {
            "day":              self.day,
            "date_offset_days": self.date_offset_days,
            "total_miles":      round(self.total_miles, 1),
            "events":           events,
        }


@dataclass
class TripResult:
    """Final output of calculate_trip(). Consumed by Django view and React frontend."""
    days:        List[DaySheet]
    stops:       List[Stop]
    total_slots: int
    violations:  List[str]   # HOS rule violations detected during scheduling

    def to_dict(self) -> dict:
        return {
            "days":        [d.to_dict() for d in self.days],
            "stops":       [
                {
                    "type":      s.stop_type,
                    "remark":    s.remark,
                    "day":       s.day,
                    "time":      s.time_of_day,
                    "leg_index": s.leg_index,
                }
                for s in self.stops
            ],
            "total_hours": round(self.total_slots * SLOT, 2),
            "violations":  self.violations,
        }


# ---------------------------------------------------------------------------
# Raw segment builder
# ---------------------------------------------------------------------------

@dataclass
class RawSegment:
    """
    An unscheduled unit of work — what needs to happen, without worrying about
    HOS rules yet. The scheduler will place these into the timeline slot by slot.
    """
    seg_type:  str        # "drive" or "on_duty"
    slots:     int        # how many 30-min slots this work takes
    miles:     float = 0.0
    remark:    str   = ""
    leg_index: int   = 0  # which leg of the route this belongs to (for map markers)


def build_raw_segments(
    legs: List[dict],
    pickup_location: str,
    dropoff_location: str,
) -> List[RawSegment]:
    """
    Build the ordered list of work for the whole trip, ignoring HOS rules.
    The scheduler will later enforce all timing constraints.

    Output order:
      pre-trip → [drive + fuel stops] → pickup → [drive + fuel stops] → dropoff → post-trip

    Fuel stops are inserted after any leg that crosses a 1000-mile boundary.
    Multiple boundaries in one leg (e.g. 800 → 2300 miles) produce multiple stops.

    Args:
        legs: legs[0] = current_location → pickup
              legs[1] = pickup → dropoff
              Each leg: {"miles": float, "drive_hrs": float, "from": str, "to": str}
    """
    segments: List[RawSegment] = []
    cumulative_miles = 0.0   # running total of miles driven since trip start
    # Tracks miles driven since the last fuel stop (or trip start).
    # Resets to 0 after every fuel stop. When it reaches FUEL_EARLY_MILES
    # we split the current drive chunk and insert a stop.
    miles_since_fuel = 0.0

    # ── Pre-trip inspection ──────────────────────────────────────────────────
    segments.append(RawSegment(
        seg_type="on_duty",
        slots=PRE_TRIP_SLOTS,
        remark="Pre-trip inspection",
    ))

    for leg_idx, leg in enumerate(legs):
        leg_miles     = leg["miles"]
        leg_drive_hrs = leg["drive_hrs"]
        mph           = leg_miles / leg_drive_hrs if leg_drive_hrs > 0 else 55.0

        remaining_miles = leg_miles   # miles left in this leg still to be placed

        # ── Split the leg into drive chunks separated by fuel stops ────────────
        # Instead of driving the full leg and inserting stops after the fact,
        # we drive only up to FUEL_EARLY_MILES, insert a stop, then continue.
        # This guarantees fuel stops always land INSIDE a drive — never stacked
        # back-to-back at a leg boundary where pickup/dropoff also sit.
        while remaining_miles > 0.001:

            # How many miles can we drive before needing fuel?
            miles_to_fuel = FUEL_EARLY_MILES - miles_since_fuel

            # Drive whichever is shorter: rest of leg OR distance to next fuel
            chunk_miles = min(remaining_miles, miles_to_fuel)
            chunk_hours = chunk_miles / mph
            chunk_slots = max(1, round(chunk_hours / SLOT))

            segments.append(RawSegment(
                seg_type="drive",
                slots=chunk_slots,
                miles=chunk_miles,
                remark=f"Driving to {leg['to']}",
                leg_index=leg_idx,
            ))

            cumulative_miles += chunk_miles
            miles_since_fuel += chunk_miles
            remaining_miles  -= chunk_miles

            # ── Insert fuel stop if we hit FUEL_EARLY_MILES ─────────────────────
            # Only stop if there are still miles ahead in the whole trip —
            # no point fueling at the very end before post-trip inspection.
            total_miles_remaining = remaining_miles + sum(
                l["miles"] for l in legs[leg_idx + 1:]
            )
            if miles_since_fuel >= FUEL_EARLY_MILES and total_miles_remaining > 0.001:
                fuel_label = round(cumulative_miles / 25) * 25   # nearest 25 mi
                segments.append(RawSegment(
                    seg_type="on_duty",
                    slots=FUEL_SLOTS,
                    remark=f"Fuel stop (~{int(fuel_label)} mi)",
                    leg_index=leg_idx,
                ))
                miles_since_fuel = 0.0   # reset — next interval measured from here

        # ── End-of-leg stop (pickup or dropoff) ──────────────────────────────────
        if leg_idx == 0:
            segments.append(RawSegment(
                seg_type="on_duty",
                slots=PICKUP_SLOTS,
                remark=f"Pickup at {pickup_location}",
                leg_index=leg_idx,
            ))
        elif leg_idx == len(legs) - 1:
            segments.append(RawSegment(
                seg_type="on_duty",
                slots=DROPOFF_SLOTS,
                remark=f"Dropoff at {dropoff_location}",
                leg_index=leg_idx,
            ))
    # ── Post-trip inspection ─────────────────────────────────────────────────
    segments.append(RawSegment(
        seg_type="on_duty",
        slots=POST_TRIP_SLOTS,
        remark="Post-trip inspection",
    ))

    return segments


# ---------------------------------------------------------------------------
# Scheduler state
# ---------------------------------------------------------------------------

class State:
    """
    Tracks all three HOS clocks and the break counter as the scheduler
    walks through raw segments slot by slot.

    cursor = absolute slot index (0 = midnight Day 1).
    All durations are stored in slots.
    """

    def __init__(self, current_cycle_used: float, has_curfew: bool = True):
        # Start at 06:00 = slot 12
        # [CUSTOM] Change SHIFT_START_HOUR above to adjust the start time.
        self.cursor: int = SHIFT_START_SLOT
        self.has_curfew: bool = has_curfew

        # ── Clock 1: 14-hr window ────────────────────────────────────────────
        # window_start is set when the driver first goes on-duty in a shift.
        # After 28 slots (14 hrs) from window_start, no more driving allowed.
        self.window_start:  int  = self.cursor
        self.shift_active:  bool = False   # False until first on-duty of the shift

        # ── Clock 2: daily drive limit ───────────────────────────────────────
        # Counts only driving slots (not on-duty) in the current shift.
        # Resets to 0 after a 10-hr rest.
        self.drive_slots_today: int = 0

        # ── Clock 3: 70-hr cycle ─────────────────────────────────────────────
        # Counts ALL on-duty + driving slots since the last 34-hr restart.
        # Initialized from current_cycle_used (hours already used before trip).
        self.cycle_slots: int = round(current_cycle_used / SLOT)

        # ── Break counter ────────────────────────────────────────────────────
        # Counts consecutive driving slots since the last non-driving slot.
        # Resets to 0 on ANY on_duty or off_duty slot.
        # When it reaches BREAK_AFTER_SLOTS (16), a break must be inserted.
        self.break_counter: int = 0

        self.timeline:   List[Slot] = []
        self.stops:      List[Stop] = []
        self.violations: List[str]  = []

    # ── Clock read helpers ───────────────────────────────────────────────────

    @property
    def window_slots_used(self) -> int:
        """Slots elapsed since the shift window opened."""
        return self.cursor - self.window_start

    @property
    def window_slots_remaining(self) -> int:
        """Slots left before the 14-hr window closes."""
        return MAX_WINDOW_SLOTS - self.window_slots_used

    @property
    def drive_slots_remaining(self) -> int:
        """Drive slots left before hitting the 11-hr limit."""
        return MAX_DRIVE_SLOTS - self.drive_slots_today

    @property
    def cycle_slots_remaining(self) -> int:
        """Slots left in the 70-hr/8-day cycle."""
        return MAX_CYCLE_SLOTS - self.cycle_slots

    @property
    def slot_of_day(self) -> int:
        """
        Current slot position within the calendar day (0–47).
        Used to check the 11pm driving curfew.
        e.g. cursor=61 → day 2, slot 13 of that day → 06:30
        """
        return self.cursor % 48

    def can_drive(self) -> bool:
        """True if none of the three HOS clocks are exhausted."""
        return (
            self.drive_slots_remaining  > 0
            and self.window_slots_remaining > 0
            and self.cycle_slots_remaining  > 0
        )

    def needs_break(self) -> bool:
        """True when 8 cumulative hours of driving have passed without a break."""
        return self.break_counter >= BREAK_AFTER_SLOTS

    def past_curfew(self) -> bool:
        """
        [CUSTOM] True if the current slot is at or after 23:00 within the day.
        Driving is not allowed once past the curfew — insert a rest instead.
        Delete this method and its call in schedule_drive_segment() to remove.
        """
        return self.slot_of_day >= NO_DRIVE_AFTER_SLOT or self.slot_of_day < NO_DRIVE_BEFORE_SLOT

    # ── Slot writing ─────────────────────────────────────────────────────────

    def push(self, status: Status, remark: str = "", miles: float = 0.0):
        """Append one 30-min slot to the timeline and advance the cursor by 1."""
        self.timeline.append(Slot(
            index=self.cursor,
            status=status,
            remark=remark,
            miles=miles,
        ))
        self.cursor += 1

    def record_stop(self, stop_type: str, remark: str, leg_index: int = 0):
        """Record a map marker at the current cursor position."""
        abs_hour = self.cursor * SLOT
        day      = int(abs_hour / 24) + 1
        h_of_day = abs_hour % 24
        hh       = int(h_of_day)
        mm       = int((h_of_day - hh) * 60)
        self.stops.append(Stop(
            stop_type=stop_type,
            remark=remark,
            slot_index=self.cursor,
            day=day,
            time_of_day=f"{hh:02d}:{mm:02d}",
            leg_index=leg_index,
        ))

    # ── Shift / rest helpers ─────────────────────────────────────────────────

    def _open_shift(self):
        """
        Mark the start of the 14-hr window when the first duty of a shift begins.
        Called at the start of every on-duty or driving segment.
        No-op if the shift is already open.
        """
        if not self.shift_active:
            self.window_start = self.cursor
            self.shift_active = True

    def _reset_daily_clocks(self):
        """
        Reset the per-shift clocks after a rest period.
        Clock 3 (70hr cycle) is NOT reset here — only insert_34hr_restart() does that.
        """
        self.drive_slots_today = 0
        self.break_counter     = 0
        self.window_start      = self.cursor
        self.shift_active      = False

    def insert_rest(self, reason: str = "", curfew: bool = False):
        """
        Insert a 10-hr off-duty rest (20 slots).
        Resets Clock 1 (window) and Clock 2 (drive limit).
        Does NOT reset Clock 3 (70-hr cycle).
        """
        remark = f"10-hr rest{(' - ' + reason) if reason else ''}"
        self.record_stop("rest", remark)
        for _ in range(REST_SLOTS):
            self.push(Status.OFF_DUTY, remark=remark)
        self._reset_daily_clocks()

    def insert_curfew_rest(self):
        """
        Insert a variable-length off-duty rest until the next 05:00.
        Covers the custom 11pm–5am no-drive window.

        Unlike insert_rest(), this does NOT reset the HOS clocks — the
        curfew window is a custom rule independent of FMCSA rest requirements.
        If Clock 1 or Clock 2 is still exhausted after the curfew rest,
        can_drive() will trigger the appropriate FMCSA rest on the next iteration.
        """
        sod = self.slot_of_day
        if sod >= NO_DRIVE_AFTER_SLOT:
            # e.g. at 23:00 (slot 46): need (48-46) + 10 = 12 slots to reach 05:00
            slots_needed = (48 - sod) + NO_DRIVE_BEFORE_SLOT
        else:
            # Before 05:00 (e.g. slot 3 = 01:30): need 10 - 3 = 7 slots
            slots_needed = NO_DRIVE_BEFORE_SLOT - sod

        remark = "Curfew rest (11pm–5am)"
        self.record_stop("rest", remark)
        for _ in range(slots_needed):
            self.push(Status.OFF_DUTY, remark=remark)

    def insert_34hr_restart(self, reason: str = "70-hr cycle limit"):
        """
        Insert a 34-hr restart (68 slots).
        Resets ALL three clocks — the driver gets a fresh 70-hr cycle.
        Only used when cycle_slots_remaining <= 0.
        """
        remark = f"34-hr restart ({reason})"
        self.record_stop("rest", remark)
        for _ in range(RESTART_SLOTS):
            self.push(Status.OFF_DUTY, remark=remark)
        self._reset_daily_clocks()
        self.cycle_slots = 0   # Clock 3 reset


# ---------------------------------------------------------------------------
# Segment schedulers
# ---------------------------------------------------------------------------

def schedule_on_duty_segment(state: State, seg: RawSegment):
    """
    Place an on-duty-not-driving segment onto the timeline, slot by slot.

    What each ON_DUTY slot does to the clocks:
      Clock 1 (window)    → counts toward the 14-hr window
      Clock 2 (drive)     → NOT affected (only driving counts)
      Clock 3 (cycle)     → counts toward the 70-hr total
      break_counter       → RESET to 0  (any on-duty stop satisfies the break rule)

    Inserts a rest if the 14-hr window or 70-hr cycle would be exceeded.
    Records a map marker the first time a pickup, dropoff, or fuel stop is placed.
    """
    state._open_shift()

    remaining     = seg.slots
    iterations    = 0
    stop_recorded = False   # only record the map marker once per segment

    while remaining > 0:
        iterations += 1
        if iterations > MAX_ITERATIONS:
            state.violations.append(f"on_duty loop exceeded limit: {seg.remark}")
            break

        # Resolve Clock 3: 70-hr cycle exhausted → need 34-hr restart
        if state.cycle_slots_remaining <= 0:
            state.insert_34hr_restart("70-hr cycle limit")
            state._open_shift()
            continue

        # Resolve Clock 1: 14-hr window closed → need 10-hr rest
        if state.window_slots_remaining <= 0:
            state.insert_rest("14-hr window")
            state._open_shift()
            continue

        # Record map marker once at the start of this segment
        if not stop_recorded:
            stop_map  = {"pickup": "pickup", "dropoff": "dropoff", "fuel": "fuel"}
            stop_type = next(
                (v for k, v in stop_map.items() if k in seg.remark.lower()), None
            )
            if stop_type:
                state.record_stop(stop_type, seg.remark, seg.leg_index)
            stop_recorded = True

        # Place one on-duty slot
        state.push(Status.ON_DUTY, remark=seg.remark)
        state.cycle_slots   += 1    # Clock 3 advances
        state.break_counter  = 0    # break counter resets — this stop counts as a break
        remaining -= 1


def schedule_drive_segment(state: State, seg: RawSegment):
    """
    Place a driving segment onto the timeline, slot by slot.
    Automatically inserts rests and breaks whenever required.

    What each DRIVING slot does to the clocks:
      Clock 1 (window)    → counts toward the 14-hr window
      Clock 2 (drive)     → increments drive_slots_today
      Clock 3 (cycle)     → counts toward the 70-hr total
      break_counter       → increments by 1

    The loop runs until all slots in the segment are placed.
    On each iteration, it checks (in order):
      1. Can we drive at all? (all 3 clocks have headroom)
      2. Are we past the 11pm curfew?       [CUSTOM rule]
      3. Do we need a mandatory break?      (break_counter >= 16)
      4. Drive one slot.
    """
    state._open_shift()

    # miles_per_slot distributes the leg's total miles evenly across its slots
    miles_per_slot = seg.miles / seg.slots if seg.slots > 0 else 0.0
    remaining      = seg.slots
    iterations     = 0

    while remaining > 0:
        iterations += 1
        if iterations > MAX_ITERATIONS:
            state.violations.append(f"drive loop exceeded limit: {seg.remark}")
            break

        # ── Step 1: Resolve blocking HOS conditions ──────────────────────────
        # Check all three clocks. If any are exhausted, insert the appropriate rest.
        if not state.can_drive():
            if state.cycle_slots_remaining <= 0:
                # Clock 3 hit — need 34-hr restart to reset the 70-hr cycle
                state.insert_34hr_restart("70-hr cycle limit")
            elif state.drive_slots_remaining <= 0:
                # Clock 2 hit — drove 11 hours, need 10-hr rest
                state.insert_rest("11-hr drive limit")
            elif state.window_slots_remaining <= 0:
                # Clock 1 hit — 14-hr window closed, need 10-hr rest
                state.insert_rest("14-hr window")
            else:
                state.insert_rest("cannot drive")
            state._open_shift()
            continue   # re-evaluate all conditions after the rest

        # ── Step 2: 11pm curfew check ────────────────────────────────────────
        # [CUSTOM] No driving at or after 23:00 within the calendar day.
        # If we're at the curfew slot, insert a 10-hr rest and resume next day.
        # Delete this block and past_curfew() to remove this restriction.
        if state.has_curfew and state.past_curfew():
            state.insert_curfew_rest()
            state._open_shift()
            continue

        # ── Step 3: Mandatory 30-min break ───────────────────────────────────
        # FMCSA requires a break after 8 cumulative driving hours (16 slots).
        # We insert an ON_DUTY slot, which also resets the break counter.
        # Note: if the driver had a pickup/fuel stop earlier, the counter was
        # already reset — so this block may never trigger during that shift.
        if state.needs_break():
            state.push(Status.ON_DUTY, remark="30-min break")
            state.cycle_slots   += 1    # Clock 3 still advances during a break
            state.break_counter  = 0    # reset — on_duty always clears the counter
            continue   # do NOT decrement remaining (no drive slot was used)

        # ── Step 4: Drive one slot ───────────────────────────────────────────
        state.push(Status.DRIVING, remark=seg.remark, miles=miles_per_slot)
        state.drive_slots_today += 1    # Clock 2
        state.cycle_slots       += 1    # Clock 3
        state.break_counter     += 1    # approaching the break threshold
        remaining -= 1                  # one slot of the segment done


# ---------------------------------------------------------------------------
# Day sheet builder
# ---------------------------------------------------------------------------

def build_day_sheets(timeline: List[Slot]) -> List[DaySheet]:
    """
    Slice the flat timeline into 48-slot (24-hour) DaySheet objects.

    The timeline may have gaps (e.g. if the day starts with pre-shift off-duty
    that was pushed before the loop). Gaps are filled with OFF_DUTY slots.

    Only days that contain at least one non-off-duty slot are included,
    except Day 1 which is always included even if partially empty.
    """
    if not timeline:
        return []

    last_slot = timeline[-1].index
    num_days  = (last_slot // 48) + 1

    # Index by slot number for O(1) lookup
    slot_map = {s.index: s for s in timeline}

    days: List[DaySheet] = []

    for day_idx in range(num_days):
        day_start = day_idx * 48
        day_end   = day_start + 48

        slots: List[Slot] = []
        has_work = False

        for i in range(day_start, day_end):
            if i in slot_map:
                s = slot_map[i]
                slots.append(s)
                if s.status != Status.OFF_DUTY:
                    has_work = True
            else:
                # Gap in the timeline — fill with off-duty
                slots.append(Slot(index=i, status=Status.OFF_DUTY, remark="Off duty"))

        if has_work or day_idx == 0:
            days.append(DaySheet(
                day=day_idx + 1,
                date_offset_days=day_idx,
                slots=slots,
            ))

    return days


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate_trip(
    legs: List[dict],
    pickup_location: str,
    dropoff_location: str,
    current_cycle_used: float = 0.0,
) -> TripResult:
    """
    Main entry point. Called by the Django view with routing API data.

    Steps:
      1. Build raw segments (what needs to happen, ignoring HOS)
      2. Initialize scheduler state with current cycle hours
      3. Fill pre-shift off-duty (midnight → 06:00)   [CUSTOM]
      4. Process each segment through the HOS scheduler
      5. Slice the flat timeline into daily log sheets

    Args:
        legs: Route legs from OpenRouteService.
              legs[0] = current_location → pickup
              legs[1] = pickup → dropoff
              Each leg: {"miles": float, "drive_hrs": float, "from": str, "to": str}
        pickup_location:    Display name for the pickup stop (shown on log sheet).
        dropoff_location:   Display name for the dropoff stop.
        current_cycle_used: Hours the driver has already been on-duty this 8-day
                            window before this trip starts. Used to initialize Clock 3.

    Returns:
        TripResult containing:
          - days:       list of DaySheet objects (one per calendar day)
          - stops:      list of Stop objects (for Leaflet map markers)
          - violations: any HOS rule violations detected
    """
    raw   = build_raw_segments(legs, pickup_location, dropoff_location)
    state = State(current_cycle_used=current_cycle_used)

    # Record the trip start as a map marker
    state.record_stop("start", f"Start at {legs[0]['from']}", leg_index=0)

    # [CUSTOM] Fill slots 0–11 (midnight to 06:00) with off-duty.
    # The driver is assumed to be sleeping before their 06:00 shift start.
    # Delete this block and set cursor=0 in State.__init__ to start at midnight.
    # for _ in range(SHIFT_START_SLOT):
    # state.push(Status.OFF_DUTY, remark="Off duty (pre-shift)")

    # Process every raw segment through the HOS scheduler.
    # Each segment type has its own handler with the appropriate clock logic.
    for seg in raw:
        if seg.seg_type == "drive":
            schedule_drive_segment(state, seg)
        else:
            schedule_on_duty_segment(state, seg)

    # Slice the flat timeline into per-day log sheets
    days = build_day_sheets(state.timeline)

    return TripResult(
        days=days,
        stops=state.stops,
        total_slots=state.cursor,
        violations=state.violations,
    )
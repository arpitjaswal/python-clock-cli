"""Alarm model and pure scheduling logic.

All functions are time-injectable (pass `now`) so they can be unit-tested
without touching the real clock.

TIMEZONE / CLOCK POLICY
-----------------------
Every datetime here is a naive *local wall-clock* time. We never convert
to/from UTC, so a "07:30" alarm matches when the local clock reads 07:30 —
the numbers you set never shift.

But the wall clock is NOT monotonic. It can jump because of:
  * travel (UTC offset changes),
  * DST transitions,
  * NTP corrections or a manual clock set (offset unchanged!),
  * resume from suspend.

So we do NOT watch the UTC offset (that catches only travel/DST). Instead the
engine compares wall-clock advance against `time.monotonic()` advance; any
divergence beyond a tolerance is a "clock jump." On a jump the engine calls
time.tzset() (so a long-running process actually picks up a new zone),
re-anchors every alarm's target in the now-current zone, and either fires an
alarm we *just* missed (within CATCHUP_WINDOW) or logs it as missed.

KNOWN LIMIT: a user-space polling clock cannot fire while the machine is
suspended. True wake-from-suspend needs an OS primitive (cron / systemd-timer
/ RTC wake) and is out of scope for this stdlib build. We detect the post-wake
jump and report missed alarms instead of silently dropping them.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

REPEAT_MODES = ("once", "daily", "weekdays", "weekends")

# Fire an alarm only if we're within this many seconds past its target.
# Past that (e.g. a big forward jump / resume from sleep) we log it missed
# and reschedule rather than firing for a moment the user never experienced.
CATCHUP_WINDOW_SECONDS = 90

# Wall vs monotonic advance may differ by this much from poll jitter / GC
# without it counting as a real clock jump.
JUMP_TOLERANCE_SECONDS = 3.0


@dataclass
class Alarm:
    time: str                      # "HH:MM" local wall-clock
    label: str = ""
    repeat: str = "once"           # one of REPEAT_MODES (ignored if `date` is set)
    enabled: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    last_fired: str | None = None  # local datetime "YYYY-MM-DD HH:MM" actually fired
    date: str | None = None        # "YYYY-MM-DD" → fire once on this exact local date

    def __post_init__(self) -> None:
        hh, mm = _parse_hhmm(self.time)
        self.time = f"{hh:02d}:{mm:02d}"
        if self.date is not None:
            # Validate and normalise; rejects bad format AND impossible dates.
            self.date = _parse_date(self.date).strftime("%Y-%m-%d")
        if self.repeat not in REPEAT_MODES:
            raise ValueError(f"repeat must be one of {REPEAT_MODES}, got {self.repeat!r}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Alarm":
        return cls(**d)


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hh_s, mm_s = value.strip().split(":")
        hh, mm = int(hh_s), int(mm_s)
    except (ValueError, AttributeError):
        raise ValueError(f"time must be HH:MM, got {value!r}")
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"time out of range: {value!r}")
    return hh, mm


def _parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d")
    except (ValueError, AttributeError):
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}")


def _fires_on_weekday(repeat: str, weekday: int) -> bool:
    """weekday: Mon=0 .. Sun=6."""
    if repeat in ("once", "daily"):
        return True
    if repeat == "weekdays":
        return weekday < 5
    if repeat == "weekends":
        return weekday >= 5
    return False


def local_now() -> datetime:
    """Naive current local wall-clock time."""
    return datetime.now()


def detect_clock_jump(
    prev_wall: datetime,
    prev_mono: float,
    now_wall: datetime,
    now_mono: float,
    tolerance: float = JUMP_TOLERANCE_SECONDS,
) -> float:
    """Return the wall-clock anomaly in seconds, 0.0 if time advanced normally.

    Compares how far the wall clock moved against how far monotonic time moved.
    A positive result = wall clock jumped forward (travel east / DST spring /
    resume from sleep); negative = jumped backward (travel west / DST fall /
    NTP rewind). This catches *every* discontinuity, not just offset changes.
    """
    wall_delta = (now_wall - prev_wall).total_seconds()
    mono_delta = now_mono - prev_mono
    anomaly = wall_delta - mono_delta
    return anomaly if abs(anomaly) > tolerance else 0.0


def next_fire(alarm: Alarm, now: datetime) -> datetime | None:
    """Return the next local datetime this alarm should fire, or None if spent.

    `now` must be naive local time. A 'once' alarm that already fired today is
    spent for today.
    """
    if not alarm.enabled:
        return None
    hh, mm = _parse_hhmm(alarm.time)
    if alarm.date is not None:
        # One-shot on an exact date: repeat/weekday rules don't apply.
        target = _parse_date(alarm.date).replace(hour=hh, minute=mm)
        if target <= now or _already_fired(alarm, target):
            return None
        return target
    for delta in range(0, 8):
        candidate = (now + timedelta(days=delta)).replace(
            hour=hh, minute=mm, second=0, microsecond=0
        )
        if candidate <= now:
            continue
        if not _fires_on_weekday(alarm.repeat, candidate.weekday()):
            continue
        if alarm.repeat == "once" and _already_fired(alarm, candidate):
            continue
        return candidate
    return None


def _already_fired(alarm: Alarm, target: datetime) -> bool:
    """Dedupe key is the full target minute, so a backward jump across midnight
    can't trick a date-only check into replaying an already-fired alarm."""
    return alarm.last_fired == target.strftime("%Y-%m-%d %H:%M")


def fire_decision(alarm: Alarm, target: datetime | None, now: datetime) -> str:
    """Classify what to do with an alarm right now.

    Returns one of: "wait" (target not reached), "fire" (reached, within
    catch-up window), "missed" (reached but too stale to fire — log & skip).
    """
    if target is None or not alarm.enabled:
        return "wait"
    if _already_fired(alarm, target):
        return "wait"
    if now < target:
        return "wait"
    overshoot = (now - target).total_seconds()
    return "fire" if overshoot <= CATCHUP_WINDOW_SECONDS else "missed"


def mark_fired(alarm: Alarm, target: datetime) -> None:
    """Record that `target` fired so it won't replay (survives restart via store)."""
    alarm.last_fired = target.strftime("%Y-%m-%d %H:%M")

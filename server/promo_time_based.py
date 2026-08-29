"""Stage 7: Time-Based Rate promo resolution + schedule-conflict checks.

A Time-Based Rate promo temporarily REPLACES the Standard linear
amount->time conversion (server/rate_calculator.calculate_metered_time
against the shop's permanent metered rate) for the length of a recurring
daily local-time window -- e.g. a "Midnight Rate" of P20 = 120 minutes,
every day 00:00-06:00. It is Standard-only in this version, never touches
VIP pricing, never touches fixed packages, and never creates a
promo_redemptions row (it is not limit-accounted).

The owner never manages priority for these -- overlapping schedules are
REJECTED at create/update/reactivate time (see find_time_based_rate_conflict)
rather than silently resolved. select_active_time_based_rate therefore
expects at most one match; its ordering is pure defence-in-depth for
legacy/racy data and never consults priority.

Deliberately pure, mirroring server/promo_eligibility.py: `now_utc` and
`timezone_name` are passed in explicitly and nothing here reads the clock
or the database. The rate math itself is NOT reimplemented -- a throwaway
metered RateProfile carrying the promo's own price/duration is fed to the
existing server/rate_calculator.calculate_metered_time.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from server.promo_status import derive_promo_status
from server.rate_calculator import calculate_metered_time
from server.rate_eligibility import parse_local_time, resolve_shop_timezone
from server.schemas import Promo, RateProfile

_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MINUTES_PER_DAY = 24 * 60
_MINUTES_PER_WEEK = 7 * _MINUTES_PER_DAY


def parse_days_of_week(raw: object) -> list[int]:
    """Accepts the stored CSV form ("0,4,5"), an already-parsed list/tuple,
    or None -> []. Silently drops anything outside 0..6 and de-duplicates."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        values: list[object] = list(raw)
    else:
        values = [piece for piece in str(raw).split(",") if str(piece).strip() != ""]
    days: list[int] = []
    for value in values:
        try:
            day = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6:
            days.append(day)
    return sorted(set(days))


def is_within_time_based_window(
    now_utc: datetime,
    timezone_name: str,
    days_of_week: object,
    window_start_local_time: str | None,
    window_end_local_time: str | None,
) -> bool:
    """True iff `now_utc`, seen in the shop's local timezone, falls inside a
    recurring window that OPENED on one of `days_of_week`.

    Semantics (confirmed with the owner):
      - Start inclusive, end exclusive (identical convention to
        server/rate_eligibility.is_within_eligibility_window).
      - The selected day is the day the window *opens*. A window that
        wraps past midnight (start > end, e.g. 23:00->02:00) stays active
        until its end time even though the calendar day has changed --
        selecting Friday means "Friday night", including the portion after
        midnight on Saturday.
    """
    days = parse_days_of_week(days_of_week)
    if not days or window_start_local_time is None or window_end_local_time is None:
        return False

    local_now = now_utc.astimezone(resolve_shop_timezone(timezone_name))
    current = local_now.time()
    weekday = local_now.weekday()  # Mon=0 .. Sun=6
    start = parse_local_time(window_start_local_time)
    end = parse_local_time(window_end_local_time)

    if start < end:
        # Same-day window, e.g. 00:00-06:00.
        return weekday in days and start <= current < end

    if start == end:
        # Degenerate zero-length window -- never active (schema forbids
        # configuring one, but be defensive).
        return False

    # Wraps midnight, e.g. 23:00-02:00.
    if current >= start:
        # Window opened earlier *today*.
        return weekday in days
    if current < end:
        # Still inside a window that opened *yesterday*.
        return ((weekday - 1) % 7) in days
    return False


# -------------------------------------------------------------------------
# Schedule-overlap detection (used to REJECT conflicting Time-Based Rate
# promos at create/update/reactivate time -- see find_time_based_rate_conflict).
# -------------------------------------------------------------------------


def _hhmm_to_minutes(value: str) -> int:
    parsed = parse_local_time(value)
    return parsed.hour * 60 + parsed.minute


def _window_duration_minutes(start_minutes: int, end_minutes: int) -> int:
    """Half-open window length. When end <= start the window wraps past
    midnight (e.g. 23:00->02:00 is 180 minutes)."""
    if end_minutes > start_minutes:
        return end_minutes - start_minutes
    return (_MINUTES_PER_DAY - start_minutes) + end_minutes


def _weekly_segments(
    days_of_week: object,
    window_start_local_time: str | None,
    window_end_local_time: str | None,
) -> list[tuple[int, int]]:
    """Expands a recurring schedule into half-open [start, end) minute
    segments on a Mon 00:00 = 0 .. 10080 weekly timeline. The selected day
    is the day the window OPENS; a window that runs past the end of the
    week (Sun night) is split into two segments."""
    days = parse_days_of_week(days_of_week)
    if not days or window_start_local_time is None or window_end_local_time is None:
        return []
    start_minutes = _hhmm_to_minutes(window_start_local_time)
    end_minutes = _hhmm_to_minutes(window_end_local_time)
    duration = _window_duration_minutes(start_minutes, end_minutes)
    if duration <= 0:
        return []

    segments: list[tuple[int, int]] = []
    for day in days:
        opening = day * _MINUTES_PER_DAY + start_minutes
        closing = opening + duration
        if closing <= _MINUTES_PER_WEEK:
            segments.append((opening, closing))
        else:
            segments.append((opening, _MINUTES_PER_WEEK))
            segments.append((0, closing - _MINUTES_PER_WEEK))
    return segments


def _segment_lists_overlap(
    segments_a: list[tuple[int, int]], segments_b: list[tuple[int, int]]
) -> bool:
    for a_start, a_end in segments_a:
        for b_start, b_end in segments_b:
            # Half-open intersection: adjacent windows (06:00 end vs 06:00
            # start) do NOT overlap.
            if a_start < b_end and b_start < a_end:
                return True
    return False


def time_based_windows_overlap(
    days_a: object,
    start_a: str | None,
    end_a: str | None,
    days_b: object,
    start_b: str | None,
    end_b: str | None,
) -> bool:
    """True iff the two recurring weekly schedules share any wall-clock
    minute. Midnight-crossing and end-of-week wrap are handled; windows
    are half-open so 00:00-06:00 and 06:00-12:00 do not overlap."""
    return _segment_lists_overlap(
        _weekly_segments(days_a, start_a, end_a),
        _weekly_segments(days_b, start_b, end_b),
    )


def campaign_ranges_overlap(
    start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime
) -> bool:
    """Half-open [starts_at, ends_at) overlap -- a promo ending exactly
    when another starts does not conflict."""
    return start_a < end_b and start_b < end_a


def describe_time_based_schedule(promo: Promo) -> str:
    days = parse_days_of_week(promo.days_of_week)
    if len(days) == 7:
        days_label = "Every day"
    elif days:
        days_label = ", ".join(_DAY_NAMES[day] for day in days)
    else:
        days_label = "no days"
    return f"{days_label} {promo.window_start_local_time}-{promo.window_end_local_time}"


def find_time_based_rate_conflict(
    *,
    days_of_week: object,
    window_start_local_time: str | None,
    window_end_local_time: str | None,
    starts_at: datetime,
    ends_at: datetime,
    existing: Sequence[Promo],
    exclude_promo_id: str | None = None,
) -> Promo | None:
    """Returns the first existing Time-Based Rate promo whose schedule
    conflicts with the given one, or None.

    Two Time-Based Rate promos conflict iff ALL of:
      - the other promo's owner_state is NOT 'ended' (Draft, Enabled, or
        Disabled all block -- an owner must not be able to stage two
        overlapping schedules and enable both later);
      - their campaign [starts_at, ends_at) ranges overlap;
      - their recurring weekly windows overlap.

    Purchase promos in `existing` are ignored. All Time-Based Rate promos
    are Standard audience by schema rule, so no audience check is needed.
    """
    for other in existing:
        if other.promo_type != "time_based_rate":
            continue
        if exclude_promo_id is not None and other.promo_id == exclude_promo_id:
            continue
        if other.owner_state == "ended":
            continue
        if not campaign_ranges_overlap(starts_at, ends_at, other.starts_at, other.ends_at):
            continue
        if time_based_windows_overlap(
            days_of_week,
            window_start_local_time,
            window_end_local_time,
            other.days_of_week,
            other.window_start_local_time,
            other.window_end_local_time,
        ):
            return other
    return None


@dataclass(frozen=True)
class TimeBasedRateResult:
    promo: Promo
    normal_seconds: int
    promo_seconds: int

    @property
    def benefit_seconds(self) -> int:
        return self.promo_seconds - self.normal_seconds


def _synthetic_rate(promo: Promo, now: datetime) -> RateProfile:
    """A throwaway metered RateProfile carrying the promo's own
    price/duration so the existing calculate_metered_time formula
    (floor(amount * duration / price)) is reused verbatim. Never
    persisted, never returned to any caller."""
    return RateProfile(
        rate_id=f"promo-time-based:{promo.promo_id}",
        name=promo.name,
        duration_seconds=promo.granted_seconds,
        price=promo.price,
        rate_type="metered",
        is_active=True,
        sort_order=0,
        station_scope="standard",
        created_at=now,
        updated_at=now,
    )


def select_active_time_based_rate(
    candidates: Sequence[Promo],
    *,
    amount: Decimal,
    now_utc: datetime,
    timezone_name: str,
    normal_seconds: int | None,
) -> TimeBasedRateResult | None:
    """Picks the single Time-Based Rate promo that applies to a Standard
    linear `amount` purchase right now, or None.

    Overlapping schedules are rejected at configuration time
    (find_time_based_rate_conflict), so at most one promo should ever be
    eligible at a given instant. The ordering here is pure defence-in-depth
    for legacy or racy data: oldest `created_at` first, then `promo_id`
    ascending. It never consults priority -- the owner does not manage
    priority for rate schedules.

    A promo is returned only when its promotional rate STRICTLY beats the
    normal linear rate for this exact amount -- an equal or worse
    promotional rate is never applied (nothing to record, and the
    customer already gets the normal rate).
    """
    eligible = [
        promo
        for promo in candidates
        if promo.promo_type == "time_based_rate"
        and promo.audience == "standard"
        and derive_promo_status(promo.owner_state, promo.starts_at, promo.ends_at, now_utc) == "Active"
        and is_within_time_based_window(
            now_utc,
            timezone_name,
            promo.days_of_week,
            promo.window_start_local_time,
            promo.window_end_local_time,
        )
    ]
    if not eligible:
        return None

    best = sorted(
        eligible,
        key=lambda promo: (promo.created_at, promo.promo_id),
    )[0]

    try:
        promo_seconds = calculate_metered_time(amount, _synthetic_rate(best, now_utc)).duration_seconds
    except Exception:
        # e.g. the amount buys less than one second at the promo rate --
        # fall back to the normal rate rather than error.
        return None

    base = normal_seconds or 0
    if promo_seconds <= base:
        return None

    return TimeBasedRateResult(promo=best, normal_seconds=base, promo_seconds=promo_seconds)

"""Server-side authority for which package rates may be used where and when.

Confirmed owner rules:
  - A package rate's station_scope must match the target PC's station
    (Standard vs VIP), unless the rate is scoped 'any'.
  - guest_only / member_eligible gate whether a rate may ever be attached to
    a member-authenticated flow. No current endpoint combines a member
    identity with a rate_id (member sessions always consume balance
    directly and never reference a package rate), so this branch is
    unreachable today -- it is enforced here regardless, as the single
    authoritative choke point, so it can never be bypassed if that changes.
  - An optional local-time eligibility window (e.g. the midnight promo)
    gates whether the rate can be selected *right now*, in the shop's
    configured local timezone. The window only gates the moment of
    purchase/start/add-time -- a session or add-time already granted keeps
    its full purchased duration regardless of the wall clock afterward.
"""

from datetime import datetime, time, timezone as datetime_timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from server.schemas import RateProfile

DEFAULT_SHOP_TIMEZONE = "Asia/Manila"


def resolve_shop_timezone(timezone_name: str) -> tzinfo:
    """Falls back to the shop default if the configured timezone string is
    invalid, so a bad Settings value can never turn into a 500 on every
    session start. Windows has no built-in IANA timezone database --
    ZoneInfo needs the `tzdata` package (see server/requirements.txt) to
    resolve *any* name, including the default -- so as a last resort this
    degrades to plain UTC rather than crashing every package-rate session
    start/add-time call if that package is somehow missing from the
    environment.
    """
    for candidate in (timezone_name, DEFAULT_SHOP_TIMEZONE):
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            continue
    return datetime_timezone.utc


def parse_local_time(value: str) -> time:
    hour_text, minute_text = value.split(":", 1)
    return time(int(hour_text), int(minute_text))


def is_within_eligibility_window(
    now_utc: datetime,
    timezone_name: str,
    start_local_time: str | None,
    end_local_time: str | None,
) -> bool:
    """Start is inclusive, end is exclusive (e.g. 00:00 allowed, 05:00 not)."""
    if start_local_time is None or end_local_time is None:
        return True

    local_now = now_utc.astimezone(resolve_shop_timezone(timezone_name))
    current_time = local_now.time()
    start_time = parse_local_time(start_local_time)
    end_time = parse_local_time(end_local_time)

    if start_time <= end_time:
        return start_time <= current_time < end_time
    # A window that wraps midnight (e.g. 22:00-02:00). Not needed for the
    # confirmed 00:00-05:00 midnight promo but handled for correctness.
    return current_time >= start_time or current_time < end_time


def validate_rate_for_station_and_context(
    rate: RateProfile,
    is_vip_station: bool,
    is_member_context: bool,
    now_utc: datetime,
    timezone_name: str,
) -> None:
    """Raises HTTPException(409) if this package rate cannot be used right
    now, on this station, in this context. Silent no-op otherwise."""
    expected_scope = "vip" if is_vip_station else "standard"
    if rate.station_scope not in (expected_scope, "any"):
        station_label = "VIP" if is_vip_station else "Standard"
        raise HTTPException(
            status_code=409,
            detail=f"{rate.name} is not available on {station_label} PCs.",
        )

    if is_member_context and (rate.guest_only or not rate.member_eligible):
        raise HTTPException(
            status_code=409,
            detail=f"{rate.name} is for guest sessions only and cannot be used for a member session.",
        )

    if not is_within_eligibility_window(
        now_utc, timezone_name, rate.eligible_start_local_time, rate.eligible_end_local_time
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"{rate.name} is only available from {rate.eligible_start_local_time} "
                f"to {rate.eligible_end_local_time} local time."
            ),
        )

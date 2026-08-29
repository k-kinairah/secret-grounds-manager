"""Stage 7 smoke tests: Time-Based Rate promos.

A Time-Based Rate promo temporarily REPLACES the Standard linear
amount->time conversion for the length of a recurring daily local-time
window, and is never limit-accounted (no promo_redemptions row -- a
separate time_based_rate_applications usage log instead).

Covers:
  - server/promo_time_based.py's pure window/day logic (including the
    "day the window opens" midnight-crossing rule).
  - Schedule-conflict detection: overlapping Time-Based Rate schedules are
    REJECTED at create/update/reactivate time (Draft/Enabled/Disabled all
    block; Ended does not); campaign date ranges must also overlap; the
    owner never manages priority.
  - select_active_time_based_rate deterministic (priority-free) selection.
  - build_promo_quote's source_type="time_based_rate" and the guarantee
    that purchase-promo behavior is byte-for-byte unchanged.
  - The full SessionService.start_prepaid_session / add_prepaid_time
    integration: session seconds, the usage-log row, NO redemption row,
    boundary behavior, package/comp paths untouched.
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

MANILA = ZoneInfo("Asia/Manila")

# Campaign date range wide enough to bracket every mocked test instant
# below -- derive_promo_status only ever returns "Active" for an enabled
# promo whose [starts_at, ends_at) contains "now".
CAMPAIGN_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
CAMPAIGN_END = datetime(2026, 3, 1, tzinfo=timezone.utc)


def local_manila(year, month, day, hour, minute) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=MANILA)


def utc(year, month, day) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Pure window / day-of-week logic
# --------------------------------------------------------------------------

def test_pure_window_same_day() -> None:
    from server.promo_time_based import is_within_time_based_window

    every_day = [0, 1, 2, 3, 4, 5, 6]
    tz = "Asia/Manila"

    assert is_within_time_based_window(local_manila(2026, 1, 2, 0, 0), tz, every_day, "00:00", "06:00") is True
    assert is_within_time_based_window(local_manila(2026, 1, 2, 5, 59), tz, every_day, "00:00", "06:00") is True
    # end is exclusive
    assert is_within_time_based_window(local_manila(2026, 1, 2, 6, 0), tz, every_day, "00:00", "06:00") is False
    assert is_within_time_based_window(local_manila(2026, 1, 2, 23, 0), tz, every_day, "00:00", "06:00") is False
    # no days selected -> never active
    assert is_within_time_based_window(local_manila(2026, 1, 2, 1, 0), tz, [], "00:00", "06:00") is False
    # missing window -> never active
    assert is_within_time_based_window(local_manila(2026, 1, 2, 1, 0), tz, every_day, None, "06:00") is False


def test_pure_window_specific_day() -> None:
    from server.promo_time_based import is_within_time_based_window

    tz = "Asia/Manila"
    day = local_manila(2026, 1, 6, 1, 0).weekday()  # some fixed weekday
    other_day = (day + 1) % 7

    assert is_within_time_based_window(local_manila(2026, 1, 6, 1, 0), tz, [day], "00:00", "06:00") is True
    assert is_within_time_based_window(local_manila(2026, 1, 6, 1, 0), tz, [other_day], "00:00", "06:00") is False


def test_pure_window_crosses_midnight_uses_opening_day() -> None:
    from server.promo_time_based import is_within_time_based_window

    tz = "Asia/Manila"
    opening = local_manila(2026, 1, 2, 23, 30)
    opening_day = opening.weekday()

    # Opened on the selected day, before midnight -> active.
    assert is_within_time_based_window(opening, tz, [opening_day], "23:00", "02:00") is True
    # Same window, now past midnight (calendar day advanced) -> STILL active.
    assert is_within_time_based_window(local_manila(2026, 1, 3, 1, 30), tz, [opening_day], "23:00", "02:00") is True
    # 02:00 is exclusive -> closed.
    assert is_within_time_based_window(local_manila(2026, 1, 3, 2, 0), tz, [opening_day], "23:00", "02:00") is False
    # The following night is a DIFFERENT (unselected) opening day -> inactive.
    assert is_within_time_based_window(local_manila(2026, 1, 3, 23, 30), tz, [opening_day], "23:00", "02:00") is False
    # Just before the window opens -> inactive.
    assert is_within_time_based_window(local_manila(2026, 1, 2, 22, 59), tz, [opening_day], "23:00", "02:00") is False


# --------------------------------------------------------------------------
# Pure schedule-overlap logic
# --------------------------------------------------------------------------

def test_pure_windows_overlap() -> None:
    from server.promo_time_based import time_based_windows_overlap as overlap

    every = [0, 1, 2, 3, 4, 5, 6]

    # Same-day windows.
    assert overlap(every, "00:00", "06:00", every, "00:00", "06:00") is True  # identical
    assert overlap(every, "00:00", "06:00", every, "02:00", "05:00") is True  # fully contained
    assert overlap(every, "00:00", "06:00", every, "05:59", "07:00") is True  # 1-minute overlap
    # Adjacent, half-open -> NOT a conflict.
    assert overlap(every, "00:00", "06:00", every, "06:00", "12:00") is False
    assert overlap(every, "06:00", "12:00", every, "00:00", "06:00") is False

    # Day sets.
    assert overlap([0], "00:00", "06:00", [1], "00:00", "06:00") is False  # disjoint days
    assert overlap([0, 1, 2], "00:00", "06:00", [2, 3], "00:00", "06:00") is True  # share day 2

    # Midnight-crossing windows.
    # Fri 23:00 -> Sat 02:00  vs  Sat 01:00 -> Sat 03:00  => overlap in Sat 01:00-02:00.
    assert overlap([4], "23:00", "02:00", [5], "01:00", "03:00") is True
    assert overlap([4], "23:00", "02:00", [5], "03:00", "05:00") is False
    # Fri 23:00 -> Sat 02:00  vs  Fri 22:00 -> Fri 23:30  => overlap in Fri 23:00-23:30.
    assert overlap([4], "23:00", "02:00", [4], "22:00", "23:30") is True
    # Two identical midnight-crossers on different opening days do NOT overlap.
    assert overlap([4], "23:00", "02:00", [5], "23:00", "02:00") is False
    assert overlap(every, "23:00", "02:00", every, "23:00", "02:00") is True

    # End-of-week wrap: Sun 23:00 -> Mon 02:00  vs  Mon 01:00 -> Mon 03:00.
    assert overlap([6], "23:00", "02:00", [0], "01:00", "03:00") is True
    assert overlap([6], "23:00", "02:00", [0], "03:00", "05:00") is False


def test_pure_campaign_overlap() -> None:
    from server.promo_time_based import campaign_ranges_overlap as ranges_overlap

    dec_1 = utc(2026, 12, 1)
    dec_15 = utc(2026, 12, 15)
    dec_21 = utc(2026, 12, 21)
    jan_1 = utc(2027, 1, 1)
    feb_1 = utc(2027, 2, 1)

    # "December" [Dec 1, Jan 1) vs "January" [Jan 1, Feb 1) -- touch at Jan 1,
    # half-open, so NOT overlapping.
    assert ranges_overlap(dec_1, jan_1, jan_1, feb_1) is False
    assert ranges_overlap(dec_1, jan_1, dec_15, dec_21) is True
    assert ranges_overlap(dec_1, jan_1, dec_1, jan_1) is True
    assert ranges_overlap(dec_1, dec_15, dec_21, jan_1) is False


# --------------------------------------------------------------------------
# select_active_time_based_rate (priority-free ordering)
# --------------------------------------------------------------------------

def _promo_obj(
    *,
    promo_id,
    price=20,
    granted_seconds=7200,
    owner_state="enabled",
    days=(0, 1, 2, 3, 4, 5, 6),
    window=("00:00", "06:00"),
    starts_at=CAMPAIGN_START,
    ends_at=CAMPAIGN_END,
    created_at=None,
):
    """Builds a Promo directly (no DB) so the pure selection function can be
    fed several overlapping schedules -- something the create-time conflict
    check would normally forbid in a real database."""
    from server.promo_status import derive_promo_status
    from server.schemas import Promo

    made_at = created_at or CAMPAIGN_START
    return Promo(
        promo_id=promo_id,
        name=f"TBR {promo_id}",
        audience="standard",
        promo_type="time_based_rate",
        owner_state=owner_state,
        status=derive_promo_status(owner_state, starts_at, ends_at, made_at),
        amount_mode="exact",
        price=Decimal(str(price)),
        granted_seconds=granted_seconds,
        station_scope=None,
        applies_to=None,
        starts_at=starts_at,
        ends_at=ends_at,
        priority=0,
        per_member_redemption_limit=None,
        total_redemption_limit=None,
        days_of_week=list(days),
        window_start_local_time=window[0],
        window_end_local_time=window[1],
        created_by_user_id=None,
        created_at=made_at,
        updated_at=made_at,
    )


def test_select_prefers_oldest_then_id_never_priority() -> None:
    from server.promo_time_based import select_active_time_based_rate

    inside = local_manila(2026, 1, 2, 0, 30)

    # Two overlapping schedules (only possible from legacy/racy data now).
    # The NEWER one grants more time -- if priority or value were consulted
    # it would win; the deterministic rule picks the OLDER created_at.
    older = _promo_obj(promo_id="a-old", granted_seconds=7200, created_at=utc(2026, 1, 1))
    newer = _promo_obj(promo_id="b-new", granted_seconds=9000, created_at=utc(2026, 1, 2))
    result = select_active_time_based_rate(
        [newer, older], amount=Decimal("20.00"), now_utc=inside, timezone_name="Asia/Manila", normal_seconds=4800
    )
    assert result is not None
    assert result.promo.promo_id == "a-old"
    assert result.promo_seconds == 7200

    # Same created_at -> promo_id ascending breaks the tie.
    same_time = utc(2026, 1, 1)
    p2 = _promo_obj(promo_id="zzz", created_at=same_time)
    p1 = _promo_obj(promo_id="aaa", created_at=same_time)
    tied = select_active_time_based_rate(
        [p2, p1], amount=Decimal("20.00"), now_utc=inside, timezone_name="Asia/Manila", normal_seconds=4800
    )
    assert tied is not None and tied.promo.promo_id == "aaa"


def test_select_ignores_outside_window_inactive_and_worse() -> None:
    from server.promo_time_based import select_active_time_based_rate

    inside = local_manila(2026, 1, 2, 0, 30)
    outside = local_manila(2026, 1, 2, 7, 0)

    good = _promo_obj(promo_id="good", granted_seconds=7200)
    assert select_active_time_based_rate(
        [good], amount=Decimal("20.00"), now_utc=outside, timezone_name="Asia/Manila", normal_seconds=4800
    ) is None  # outside the daily window

    draft = _promo_obj(promo_id="draft", granted_seconds=99999, owner_state="draft")
    assert select_active_time_based_rate(
        [draft], amount=Decimal("20.00"), now_utc=inside, timezone_name="Asia/Manila", normal_seconds=4800
    ) is None  # not Active

    disabled = _promo_obj(promo_id="disabled", granted_seconds=99999, owner_state="disabled")
    assert select_active_time_based_rate(
        [disabled], amount=Decimal("20.00"), now_utc=inside, timezone_name="Asia/Manila", normal_seconds=4800
    ) is None

    worse = _promo_obj(promo_id="worse", granted_seconds=3600)  # P20 -> 3600s <= normal 4800
    assert select_active_time_based_rate(
        [worse], amount=Decimal("20.00"), now_utc=inside, timezone_name="Asia/Manila", normal_seconds=4800
    ) is None


# --------------------------------------------------------------------------
# Repository helpers + schedule-conflict enforcement
# --------------------------------------------------------------------------

def _promo_env(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    from server.promo_repository import PromoRepository
    from server.rate_repository import RateRepository

    return PromoRepository(), RateRepository()


def _make_time_based_promo(
    promo_repository,
    *,
    price=20,
    granted_seconds=7200,
    days=None,
    window=("00:00", "06:00"),
    starts_at=CAMPAIGN_START,
    ends_at=CAMPAIGN_END,
    enable=True,
    name=None,
):
    from server.schemas import PromoCreate, PromoStateTransition

    promo = promo_repository.create(
        PromoCreate(
            name=name or f"Rate {window[0]}-{window[1]}",
            audience="standard",
            promo_type="time_based_rate",
            amount_mode="exact",
            price=Decimal(str(price)),
            granted_seconds=granted_seconds,
            starts_at=starts_at,
            ends_at=ends_at,
            days_of_week=days if days is not None else [0, 1, 2, 3, 4, 5, 6],
            window_start_local_time=window[0],
            window_end_local_time=window[1],
        )
    )
    if enable:
        promo_repository.transition_state(promo.promo_id, PromoStateTransition(owner_state="enabled"))
        return promo_repository.get(promo.promo_id)
    return promo


def _expect_conflict(callable_, contains: str) -> None:
    try:
        callable_()
    except HTTPException as error:
        assert error.status_code == 409, f"expected 409, got {error.status_code}"
        assert contains in error.detail, f"expected {contains!r} in {error.detail!r}"
        return
    raise AssertionError("expected an HTTPException(409) schedule conflict")


def test_overlap_rejected_same_days_overlapping_window(temp_directory: str) -> None:
    promo_repository, _ = _promo_env(Path(temp_directory) / "tb-overlap-basic.sqlite3")
    _make_time_based_promo(promo_repository, window=("00:00", "06:00"), name="Midnight Rate")

    # A second schedule covering 02:00-05:00 collides with 00:00-06:00.
    _expect_conflict(
        lambda: _make_time_based_promo(promo_repository, window=("02:00", "05:00"), name="Deep Night"),
        "Midnight Rate",
    )


def test_adjacent_windows_allowed(temp_directory: str) -> None:
    promo_repository, _ = _promo_env(Path(temp_directory) / "tb-adjacent.sqlite3")
    _make_time_based_promo(promo_repository, window=("00:00", "06:00"), name="A")
    # 06:00-12:00 starts exactly where the first ends -> half-open, no conflict.
    b = _make_time_based_promo(promo_repository, window=("06:00", "12:00"), name="B")
    assert b.window_start_local_time == "06:00"


def test_disjoint_days_allowed(temp_directory: str) -> None:
    promo_repository, _ = _promo_env(Path(temp_directory) / "tb-disjoint-days.sqlite3")
    _make_time_based_promo(promo_repository, days=[0], window=("00:00", "06:00"), name="Mon")
    b = _make_time_based_promo(promo_repository, days=[1], window=("00:00", "06:00"), name="Tue")
    assert b.days_of_week == [1]


def test_midnight_crossing_overlap_rejected_and_clear_case_allowed(temp_directory: str) -> None:
    promo_repository, _ = _promo_env(Path(temp_directory) / "tb-midnight.sqlite3")
    # Fri 23:00 -> Sat 02:00.
    _make_time_based_promo(promo_repository, days=[4], window=("23:00", "02:00"), name="Fri Night")

    # Sat 01:00 -> 03:00 overlaps the post-midnight tail.
    _expect_conflict(
        lambda: _make_time_based_promo(promo_repository, days=[5], window=("01:00", "03:00"), name="Sat AM"),
        "Fri Night",
    )
    # Sat 03:00 -> 05:00 is clear.
    ok = _make_time_based_promo(promo_repository, days=[5], window=("03:00", "05:00"), name="Sat Later")
    assert ok.window_start_local_time == "03:00"


def test_campaign_dates_gate_the_conflict(temp_directory: str) -> None:
    promo_repository, _ = _promo_env(Path(temp_directory) / "tb-campaign.sqlite3")
    dec = _make_time_based_promo(
        promo_repository, window=("00:00", "06:00"), name="December Rate",
        starts_at=utc(2026, 12, 1), ends_at=utc(2027, 1, 1),
    )
    assert dec.name == "December Rate"

    # Same window, but January campaign -> ranges don't overlap -> allowed.
    jan = _make_time_based_promo(
        promo_repository, window=("00:00", "06:00"), name="January Rate",
        starts_at=utc(2027, 1, 1), ends_at=utc(2027, 2, 1),
    )
    assert jan.name == "January Rate"

    # Mid-December campaign with an overlapping window -> rejected.
    _expect_conflict(
        lambda: _make_time_based_promo(
            promo_repository, window=("02:00", "05:00"), name="Dec 15-20 Rate",
            starts_at=utc(2026, 12, 15), ends_at=utc(2026, 12, 21),
        ),
        "December Rate",
    )


def test_draft_and_disabled_block_ended_does_not(temp_directory: str) -> None:
    from server.schemas import PromoStateTransition

    promo_repository, _ = _promo_env(Path(temp_directory) / "tb-states.sqlite3")

    # Draft (never enabled) still blocks.
    _make_time_based_promo(promo_repository, window=("00:00", "06:00"), name="Draft A", enable=False)
    _expect_conflict(
        lambda: _make_time_based_promo(promo_repository, window=("01:00", "03:00"), name="B"),
        "Draft A",
    )

    # Enable then disable it -> still blocks.
    draft_a = next(p for p in promo_repository.list() if p.name == "Draft A")
    promo_repository.transition_state(draft_a.promo_id, PromoStateTransition(owner_state="enabled"))
    promo_repository.transition_state(draft_a.promo_id, PromoStateTransition(owner_state="disabled"))
    _expect_conflict(
        lambda: _make_time_based_promo(promo_repository, window=("01:00", "03:00"), name="C"),
        "Draft A",
    )

    # End it -> no longer blocks.
    promo_repository.transition_state(draft_a.promo_id, PromoStateTransition(owner_state="ended"))
    allowed = _make_time_based_promo(promo_repository, window=("00:00", "06:00"), name="Replacement")
    assert allowed.name == "Replacement"


def test_update_excludes_self_and_still_checks_others(temp_directory: str) -> None:
    from server.schemas import PromoUpdate

    promo_repository, _ = _promo_env(Path(temp_directory) / "tb-update.sqlite3")
    a = _make_time_based_promo(promo_repository, window=("00:00", "06:00"), name="A")
    b = _make_time_based_promo(promo_repository, window=("07:00", "09:00"), name="B")

    def _update(promo, **overrides):
        fields = {
            "name": promo.name,
            "audience": "standard",
            "promo_type": "time_based_rate",
            "amount_mode": "exact",
            "price": promo.price,
            "granted_seconds": promo.granted_seconds,
            "station_scope": None,
            "applies_to": None,
            "starts_at": promo.starts_at,
            "ends_at": promo.ends_at,
            "priority": 0,
            "per_member_redemption_limit": None,
            "total_redemption_limit": None,
            "days_of_week": promo.days_of_week,
            "window_start_local_time": promo.window_start_local_time,
            "window_end_local_time": promo.window_end_local_time,
        }
        fields.update(overrides)
        return promo_repository.update(promo.promo_id, PromoUpdate(**fields))

    # Editing A in place (no schedule change) must NOT self-conflict.
    same = _update(a, name="A renamed")
    assert same.name == "A renamed"

    # Editing B to overlap A -> rejected.
    _expect_conflict(
        lambda: _update(b, window_start_local_time="02:00", window_end_local_time="05:00"),
        "A renamed",
    )

    # Editing A to a window that no longer touches B is fine; B can then
    # take the freed slot.
    _update(a, window_start_local_time="00:00", window_end_local_time="01:00")
    moved = _update(b, window_start_local_time="02:00", window_end_local_time="05:00")
    assert moved.window_start_local_time == "02:00"


def test_reactivation_rechecks_conflict(temp_directory: str) -> None:
    from server.schemas import PromoStateTransition

    promo_repository, _ = _promo_env(Path(temp_directory) / "tb-reactivate.sqlite3")

    # B exists first, then is ended; A is created in the freed slot.
    b = _make_time_based_promo(promo_repository, window=("00:00", "06:00"), name="B")
    promo_repository.transition_state(b.promo_id, PromoStateTransition(owner_state="ended"))
    _make_time_based_promo(promo_repository, window=("00:00", "06:00"), name="A")

    # Reactivating B now collides with A.
    future = datetime.now(timezone.utc) + timedelta(days=400)
    _expect_conflict(
        lambda: promo_repository.transition_state(
            b.promo_id, PromoStateTransition(owner_state="enabled", ends_at=future)
        ),
        "A",
    )


def test_purchase_promo_create_update_transition_unchanged(temp_directory: str) -> None:
    from server.schemas import PromoStateTransition, PromoUpdate

    promo_repository, _ = _promo_env(Path(temp_directory) / "tb-purchase-untouched.sqlite3")

    def _purchase(name, granted):
        from server.schemas import PromoCreate

        return promo_repository.create(
            PromoCreate(
                name=name,
                audience="standard",
                amount_mode="exact",
                price=Decimal("20.00"),
                granted_seconds=granted,
                station_scope="standard",
                applies_to="start_session",
                starts_at=CAMPAIGN_START,
                ends_at=CAMPAIGN_END,
                priority=5,
            )
        )

    # Two purchase promos with the same audience/price/window still coexist
    # -- purchase promos have no schedule and never hit the conflict check.
    a = _purchase("Purchase A", 6000)
    b = _purchase("Purchase B", 9000)
    assert a.priority == 5 and b.priority == 5, "purchase-promo priority is untouched"

    updated = promo_repository.update(
        a.promo_id,
        PromoUpdate(
            name="Purchase A v2",
            audience="standard",
            amount_mode="exact",
            price=Decimal("20.00"),
            granted_seconds=6600,
            station_scope="standard",
            applies_to="start_session",
            starts_at=CAMPAIGN_START,
            ends_at=CAMPAIGN_END,
            priority=9,
        ),
    )
    assert updated.priority == 9, "purchase-promo priority still fully editable"

    promo_repository.transition_state(b.promo_id, PromoStateTransition(owner_state="enabled"))
    assert promo_repository.get(b.promo_id).owner_state == "enabled"

    # A Time-Based Rate promo can be created alongside them with no
    # interaction.
    tbr = _make_time_based_promo(promo_repository, window=("00:00", "06:00"), name="Midnight")
    assert tbr.promo_type == "time_based_rate" and tbr.priority == 0


# --------------------------------------------------------------------------
# build_promo_quote
# --------------------------------------------------------------------------

def _make_purchase_promo(promo_repository, *, granted_seconds, price="20.00", applies_to="start_session"):
    from server.schemas import PromoCreate, PromoStateTransition

    promo = promo_repository.create(
        PromoCreate(
            name="Purchase Promo",
            audience="standard",
            amount_mode="exact",
            price=Decimal(price),
            granted_seconds=granted_seconds,
            station_scope="standard",
            applies_to=applies_to,
            starts_at=CAMPAIGN_START,
            ends_at=CAMPAIGN_END,
        )
    )
    promo_repository.transition_state(promo.promo_id, PromoStateTransition(owner_state="enabled"))
    return promo


def _quote(promo_repository, rate_repository, *, amount, now):
    from server.promo_quote_service import build_promo_quote

    return build_promo_quote(
        promo_repository=promo_repository,
        rate_repository=rate_repository,
        pc_name="PC-TEST",
        is_vip_station=False,
        action="start_session",
        amount=Decimal(str(amount)),
        now=now,
        timezone_name="Asia/Manila",
    )


def test_quote_time_based_replaces_linear_rate_and_scales(temp_directory: str) -> None:
    promo_repository, rate_repository = _promo_env(Path(temp_directory) / "tb-quote.sqlite3")
    # P10 = 60 minutes -> a true linear rate.
    _make_time_based_promo(promo_repository, price=10, granted_seconds=3600, window=("00:00", "06:00"))
    at = local_manila(2026, 1, 2, 0, 30)

    q10 = _quote(promo_repository, rate_repository, amount=10, now=at)
    assert q10.source_type == "time_based_rate"
    assert q10.final_seconds == 3600  # P10 -> 60 min

    q30 = _quote(promo_repository, rate_repository, amount=30, now=at)
    assert q30.source_type == "time_based_rate"
    assert q30.final_seconds == 10800  # P30 -> 180 min (linear, not "exact P10 only")
    assert q30.base_seconds == 7200  # normal P30 at P5=20min

    outside = _quote(promo_repository, rate_repository, amount=30, now=local_manila(2026, 1, 2, 9, 0))
    assert outside.source_type == "normal_rate"
    assert outside.final_seconds == 7200
    assert outside.time_based_promo_id is None


def test_quote_purchase_promo_unchanged_when_no_time_based(temp_directory: str) -> None:
    promo_repository, rate_repository = _promo_env(Path(temp_directory) / "tb-quote-purchase.sqlite3")
    purchase = _make_purchase_promo(promo_repository, granted_seconds=6000)

    quote = _quote(promo_repository, rate_repository, amount=20, now=local_manila(2026, 1, 2, 12, 0))
    assert quote.source_type == "promo"
    assert quote.promo_id == purchase.promo_id
    assert quote.base_seconds == 4800
    assert quote.final_seconds == 6000
    assert quote.benefit_seconds == 1200

    bare = _quote(promo_repository, rate_repository, amount=15, now=local_manila(2026, 1, 2, 12, 0))
    assert bare.source_type == "normal_rate"
    assert bare.final_seconds == 3600


def test_quote_time_based_and_purchase_promo_compose_not_stack(temp_directory: str) -> None:
    promo_repository, rate_repository = _promo_env(Path(temp_directory) / "tb-quote-compose.sqlite3")
    _make_time_based_promo(promo_repository, price=20, granted_seconds=7200, window=("00:00", "06:00"))
    inside = local_manila(2026, 1, 2, 0, 30)

    weak = _make_purchase_promo(promo_repository, granted_seconds=6000)
    quote = _quote(promo_repository, rate_repository, amount=20, now=inside)
    assert quote.source_type == "time_based_rate"
    assert quote.final_seconds == 7200

    from server.schemas import PromoStateTransition

    promo_repository.transition_state(weak.promo_id, PromoStateTransition(owner_state="ended"))
    strong = _make_purchase_promo(promo_repository, granted_seconds=9000)
    quote2 = _quote(promo_repository, rate_repository, amount=20, now=inside)
    assert quote2.source_type == "promo"
    assert quote2.promo_id == strong.promo_id
    assert quote2.final_seconds == 9000


# --------------------------------------------------------------------------
# SessionService integration
# --------------------------------------------------------------------------

def create_service_env(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    import server.main as main_module
    from server.schemas import PCLayoutUpdate, PCRegistration

    main_module.pc_registry.clear()
    main_module.register_pc(PCRegistration(pc_name="PC-TB", ip_address="127.0.0.1"))
    main_module.pc_layout_repository.upsert_layout(
        "PC-TB",
        PCLayoutUpdate(display_name="PC-TB", area_label="TEST", layout_order=1, is_vip=False, is_enabled=True),
    )
    main_module.session_service.require_available_client = lambda pc_name: main_module.pc_registry[pc_name]

    async def acknowledge_command(*_: object) -> list[object]:
        return []

    main_module.session_service.send_required_command = acknowledge_command
    return main_module


class _Clock:
    """Patches server.session_service.current_utc_time (module-level import)
    the same way tests/smoke_rate_eligibility.py does."""

    def __init__(self):
        import server.session_service as session_service_module

        self.module = session_service_module
        self.original = session_service_module.current_utc_time

    def set(self, instant: datetime) -> None:
        self.module.current_utc_time = lambda: instant

    def restore(self) -> None:
        self.module.current_utc_time = self.original


def _enable_time_based(main_module, *, price=20, granted_seconds=7200, days=None):
    return _make_time_based_promo(
        main_module.session_service.promo_repository,
        price=price,
        granted_seconds=granted_seconds,
        days=days,
    )


def _pay(amount):
    from server.schemas import PaymentCreateRequest

    return PaymentCreateRequest(
        payment_method="cash", amount=Decimal(str(amount)), cash_received=Decimal(str(amount))
    )


def _count(sql, params=()):
    from server.database import get_connection

    with get_connection() as connection:
        return connection.execute(sql, params).fetchone()[0]


def test_start_inside_window_uses_promo_rate_and_logs_no_redemption(temp_directory: str) -> None:
    main_module = create_service_env(Path(temp_directory) / "tb-start.sqlite3")
    promo = _enable_time_based(main_module)
    clock = _Clock()
    clock.set(local_manila(2026, 1, 2, 0, 30))
    try:
        response = asyncio.run(
            main_module.session_service.start_prepaid_session("PC-TB", _pay(20), amount=Decimal("20.00"))
        )
    finally:
        clock.restore()

    assert response.session.total_seconds == 7200, "the promotional linear rate must replace the normal P20=4800s"
    assert _count("SELECT COUNT(*) FROM promo_redemptions") == 0, "a time-based rate is never a redemption"

    from server.database import get_connection

    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM time_based_rate_applications WHERE session_id = ?", (response.session.session_id,)
        ).fetchone()
    assert row is not None
    assert row["promo_id"] == promo.promo_id
    assert row["action"] == "start_session"
    assert row["normal_seconds"] == 4800
    assert row["promo_seconds"] == 7200
    assert row["window_start_snapshot"] == "00:00"


def test_start_outside_window_uses_normal_rate(temp_directory: str) -> None:
    main_module = create_service_env(Path(temp_directory) / "tb-start-out.sqlite3")
    _enable_time_based(main_module)
    clock = _Clock()
    clock.set(local_manila(2026, 1, 2, 9, 0))
    try:
        response = asyncio.run(
            main_module.session_service.start_prepaid_session("PC-TB", _pay(20), amount=Decimal("20.00"))
        )
    finally:
        clock.restore()

    assert response.session.total_seconds == 4800
    assert _count("SELECT COUNT(*) FROM time_based_rate_applications") == 0


def test_session_started_before_window_keeps_seconds_across_boundary(temp_directory: str) -> None:
    main_module = create_service_env(Path(temp_directory) / "tb-boundary.sqlite3")
    _enable_time_based(main_module)
    clock = _Clock()

    clock.set(local_manila(2026, 1, 2, 23, 50))
    try:
        start = asyncio.run(
            main_module.session_service.start_prepaid_session("PC-TB", _pay(20), amount=Decimal("20.00"))
        )
        assert start.session.total_seconds == 4800
        scheduled_end = start.session.scheduled_end_at

        clock.set(local_manila(2026, 1, 3, 0, 10))
        persisted = main_module.session_service.repository.get_session(start.session.session_id)
        assert persisted.scheduled_end_at == scheduled_end, "granted seconds are frozen once purchased"

        add = asyncio.run(
            main_module.session_service.add_prepaid_time(
                start.session.session_id, _pay(20), amount=Decimal("20.00")
            )
        )
    finally:
        clock.restore()

    assert add.session.total_seconds == 4800 + 7200
    assert add.session.added_seconds == 7200
    assert _count("SELECT COUNT(*) FROM promo_redemptions") == 0
    assert _count("SELECT COUNT(*) FROM time_based_rate_applications WHERE action = 'start_session'") == 0
    assert _count("SELECT COUNT(*) FROM time_based_rate_applications WHERE action = 'add_time'") == 1


def test_package_and_comp_paths_untouched_during_window(temp_directory: str) -> None:
    main_module = create_service_env(Path(temp_directory) / "tb-package.sqlite3")
    _enable_time_based(main_module)
    from server.rate_repository import RateRepository

    rate_repository = RateRepository()
    package = next(
        rate for rate in rate_repository.list_rates() if rate.name == "Standard Guest - P5 (20 Minutes)"
    )

    clock = _Clock()
    clock.set(local_manila(2026, 1, 2, 0, 30))
    try:
        pkg = asyncio.run(
            main_module.session_service.start_prepaid_session("PC-TB", _pay("5.00"), rate_id=package.rate_id)
        )
        assert pkg.session.total_seconds == package.duration_seconds
        asyncio.run(main_module.session_service.end_session(pkg.session.session_id))

        comp = asyncio.run(main_module.session_service.start_session("PC-TB", duration_seconds=1800))
        assert comp.session.total_seconds == 1800
    finally:
        clock.restore()

    assert _count("SELECT COUNT(*) FROM time_based_rate_applications") == 0


def test_purchase_promo_still_wins_and_records_redemption_when_better(temp_directory: str) -> None:
    main_module = create_service_env(Path(temp_directory) / "tb-purchase-wins.sqlite3")
    _enable_time_based(main_module, granted_seconds=7200)
    purchase = _make_purchase_promo(main_module.session_service.promo_repository, granted_seconds=9000)

    clock = _Clock()
    clock.set(local_manila(2026, 1, 2, 0, 30))
    try:
        response = asyncio.run(
            main_module.session_service.start_prepaid_session("PC-TB", _pay(20), amount=Decimal("20.00"))
        )
    finally:
        clock.restore()

    assert response.session.total_seconds == 9000
    assert _count("SELECT COUNT(*) FROM promo_redemptions WHERE promo_id = ?", (purchase.promo_id,)) == 1
    assert _count("SELECT COUNT(*) FROM time_based_rate_applications") == 0


def test_list_time_based_applications_history(temp_directory: str) -> None:
    main_module = create_service_env(Path(temp_directory) / "tb-history.sqlite3")
    promo = _enable_time_based(main_module)
    clock = _Clock()
    clock.set(local_manila(2026, 1, 2, 0, 30))
    try:
        asyncio.run(
            main_module.session_service.start_prepaid_session("PC-TB", _pay(20), amount=Decimal("20.00"))
        )
    finally:
        clock.restore()

    applications = main_module.session_service.promo_repository.list_time_based_applications(promo.promo_id)
    assert len(applications) == 1
    application = applications[0]
    assert application.normal_seconds == 4800
    assert application.promo_seconds == 7200
    assert application.amount == Decimal("20.00")
    assert application.days_of_week_snapshot == [0, 1, 2, 3, 4, 5, 6]


def main() -> None:
    pure_tests = [
        test_pure_window_same_day,
        test_pure_window_specific_day,
        test_pure_window_crosses_midnight_uses_opening_day,
        test_pure_windows_overlap,
        test_pure_campaign_overlap,
        test_select_prefers_oldest_then_id_never_priority,
        test_select_ignores_outside_window_inactive_and_worse,
    ]
    for test in pure_tests:
        test()
        print(f"{test.__name__} passed.")

    dir_tests = [
        test_overlap_rejected_same_days_overlapping_window,
        test_adjacent_windows_allowed,
        test_disjoint_days_allowed,
        test_midnight_crossing_overlap_rejected_and_clear_case_allowed,
        test_campaign_dates_gate_the_conflict,
        test_draft_and_disabled_block_ended_does_not,
        test_update_excludes_self_and_still_checks_others,
        test_reactivation_rechecks_conflict,
        test_purchase_promo_create_update_transition_unchanged,
        test_quote_time_based_replaces_linear_rate_and_scales,
        test_quote_purchase_promo_unchanged_when_no_time_based,
        test_quote_time_based_and_purchase_promo_compose_not_stack,
        test_start_inside_window_uses_promo_rate_and_logs_no_redemption,
        test_start_outside_window_uses_normal_rate,
        test_session_started_before_window_keeps_seconds_across_boundary,
        test_package_and_comp_paths_untouched_during_window,
        test_purchase_promo_still_wins_and_records_redemption_when_better,
        test_list_time_based_applications_history,
    ]
    for test in dir_tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
        print(f"{test.__name__} passed.")

    print("Promo Maker Stage 7 (Time-Based Rate) smoke tests passed.")


if __name__ == "__main__":
    main()

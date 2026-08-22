"""Stage 2 smoke tests: pure promo eligibility filtering and deterministic
best-promo selection (server/promo_eligibility.py), plus the two read-only
advisory redemption-count helpers added to PromoRepository. No session/
rate/payment/member integration and no redemption writes are exercised --
none exist yet.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def make_promo(
    *,
    promo_id: str | None = None,
    name: str = "Test Promo",
    audience: str = "standard",
    owner_state: str = "enabled",
    amount_mode: str = "exact",
    price: Decimal = Decimal("20.00"),
    granted_seconds: int = 6000,
    station_scope: str | None = "standard",
    applies_to: str | None = "start_session",
    starts_at: datetime = NOW - timedelta(hours=1),
    ends_at: datetime = NOW + timedelta(hours=1),
    priority: int = 0,
    per_member_redemption_limit: int | None = None,
    total_redemption_limit: int | None = None,
    created_at: datetime = NOW - timedelta(days=1),
):
    from server.promo_status import derive_promo_status
    from server.schemas import Promo

    return Promo(
        promo_id=promo_id or str(uuid4()),
        name=name,
        audience=audience,
        owner_state=owner_state,
        status=derive_promo_status(owner_state, starts_at, ends_at, NOW),
        amount_mode=amount_mode,
        price=price,
        granted_seconds=granted_seconds,
        station_scope=station_scope,
        applies_to=applies_to,
        starts_at=starts_at,
        ends_at=ends_at,
        priority=priority,
        per_member_redemption_limit=per_member_redemption_limit,
        total_redemption_limit=total_redemption_limit,
        created_by_user_id="owner-1",
        created_at=created_at,
        updated_at=created_at,
    )


def test_draft_scheduled_disabled_ended_expired_rejected() -> None:
    from server.promo_eligibility import select_best_promo

    cases = {
        "draft": make_promo(owner_state="draft"),
        "scheduled": make_promo(owner_state="enabled", starts_at=NOW + timedelta(hours=1), ends_at=NOW + timedelta(hours=2)),
        "disabled": make_promo(owner_state="disabled"),
        "ended_explicit": make_promo(owner_state="ended"),
        "expired": make_promo(owner_state="enabled", starts_at=NOW - timedelta(hours=2), ends_at=NOW - timedelta(hours=1)),
    }
    for label, promo in cases.items():
        result = select_best_promo(
            [promo],
            audience="standard",
            amount=Decimal("20.00"),
            now=NOW,
            action="start_session",
            station_scope="standard",
            base_granted_seconds=0,
        )
        assert result.selected_promo is None, f"{label} must be ineligible"
        assert result.reason == "no_eligible_promo"


def test_start_inclusive_end_exclusive() -> None:
    from server.promo_eligibility import select_best_promo

    at_start = make_promo(starts_at=NOW, ends_at=NOW + timedelta(hours=1))
    result = select_best_promo(
        [at_start], audience="standard", amount=Decimal("20.00"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=0,
    )
    assert result.selected_promo is not None, "starts_at must be inclusive"

    at_end = make_promo(starts_at=NOW - timedelta(hours=1), ends_at=NOW)
    result = select_best_promo(
        [at_end], audience="standard", amount=Decimal("20.00"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=0,
    )
    assert result.selected_promo is None, "ends_at must be exclusive"
    assert result.reason == "no_eligible_promo"


def test_audience_mismatch() -> None:
    from server.promo_eligibility import select_best_promo

    vip_promo = make_promo(audience="vip", station_scope="vip", applies_to="both")
    result = select_best_promo(
        [vip_promo], audience="standard", amount=Decimal("20.00"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=0,
    )
    assert result.selected_promo is None
    assert result.reason == "no_eligible_promo"


def test_station_scope_mismatch() -> None:
    from server.promo_eligibility import select_best_promo

    wrong_scope = make_promo(station_scope="vip")  # audience=standard but scope=vip
    result = select_best_promo(
        [wrong_scope], audience="standard", amount=Decimal("20.00"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=0,
    )
    assert result.selected_promo is None


def test_applies_to_start_add_both() -> None:
    from server.promo_eligibility import select_best_promo

    start_only = make_promo(applies_to="start_session")
    add_only = make_promo(applies_to="add_time")
    both = make_promo(applies_to="both")

    def selected(promo, action):
        return select_best_promo(
            [promo], audience="standard", amount=Decimal("20.00"), now=NOW,
            action=action, station_scope="standard", base_granted_seconds=0,
        ).selected_promo is not None

    assert selected(start_only, "start_session") is True
    assert selected(start_only, "add_time") is False
    assert selected(add_only, "add_time") is True
    assert selected(add_only, "start_session") is False
    assert selected(both, "start_session") is True
    assert selected(both, "add_time") is True


def test_exact_amount_matching() -> None:
    from server.promo_eligibility import select_best_promo

    promo = make_promo(amount_mode="exact", price=Decimal("20.00"))
    matched = select_best_promo(
        [promo], audience="standard", amount=Decimal("20.00"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=0,
    )
    assert matched.selected_promo is not None

    unmatched_low = select_best_promo(
        [promo], audience="standard", amount=Decimal("19.99"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=0,
    )
    assert unmatched_low.selected_promo is None
    unmatched_high = select_best_promo(
        [promo], audience="standard", amount=Decimal("20.01"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=0,
    )
    assert unmatched_high.selected_promo is None


def test_minimum_member_topup_matching() -> None:
    from server.promo_eligibility import select_best_promo

    promo = make_promo(
        audience="member_topup", amount_mode="minimum", price=Decimal("100.00"),
        granted_seconds=3600, station_scope=None, applies_to=None,
    )
    below = select_best_promo(
        [promo], audience="member_topup", amount=Decimal("99.99"), now=NOW,
        member_id="member-1", base_granted_seconds=0,
    )
    assert below.selected_promo is None

    exact = select_best_promo(
        [promo], audience="member_topup", amount=Decimal("100.00"), now=NOW,
        member_id="member-1", base_granted_seconds=0,
    )
    assert exact.selected_promo is not None

    above = select_best_promo(
        [promo], audience="member_topup", amount=Decimal("150.00"), now=NOW,
        member_id="member-1", base_granted_seconds=0,
    )
    assert above.selected_promo is not None, "minimum mode must qualify amounts >= threshold"


def test_member_id_requirement() -> None:
    from server.promo_eligibility import select_best_promo

    promo = make_promo(
        audience="member_topup", amount_mode="exact", price=Decimal("100.00"),
        granted_seconds=3600, station_scope=None, applies_to=None,
    )
    without_member = select_best_promo(
        [promo], audience="member_topup", amount=Decimal("100.00"), now=NOW,
        member_id=None, base_granted_seconds=0,
    )
    assert without_member.selected_promo is None
    assert without_member.reason == "no_eligible_promo"

    with_member = select_best_promo(
        [promo], audience="member_topup", amount=Decimal("100.00"), now=NOW,
        member_id="member-1", base_granted_seconds=0,
    )
    assert with_member.selected_promo is not None


def test_total_limit_exhausted() -> None:
    from server.promo_eligibility import select_best_promo

    promo = make_promo(total_redemption_limit=3, promo_id="limited-total")
    still_available = select_best_promo(
        [promo], audience="standard", amount=Decimal("20.00"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=0,
        total_redemption_counts={"limited-total": 2},
    )
    assert still_available.selected_promo is not None

    exhausted = select_best_promo(
        [promo], audience="standard", amount=Decimal("20.00"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=0,
        total_redemption_counts={"limited-total": 3},
    )
    assert exhausted.selected_promo is None
    assert exhausted.reason == "no_eligible_promo"


def test_per_member_limit_exhausted() -> None:
    from server.promo_eligibility import select_best_promo

    promo = make_promo(
        audience="member_topup", amount_mode="exact", price=Decimal("100.00"),
        granted_seconds=3600, station_scope=None, applies_to=None,
        per_member_redemption_limit=1, promo_id="limited-per-member",
    )
    first_time = select_best_promo(
        [promo], audience="member_topup", amount=Decimal("100.00"), now=NOW,
        member_id="member-1", base_granted_seconds=0,
        member_redemption_counts={"limited-per-member": 0},
    )
    assert first_time.selected_promo is not None

    already_used = select_best_promo(
        [promo], audience="member_topup", amount=Decimal("100.00"), now=NOW,
        member_id="member-1", base_granted_seconds=0,
        member_redemption_counts={"limited-per-member": 1},
    )
    assert already_used.selected_promo is None

    # A different member is unaffected by member-1's usage.
    different_member = select_best_promo(
        [promo], audience="member_topup", amount=Decimal("100.00"), now=NOW,
        member_id="member-2", base_granted_seconds=0,
        member_redemption_counts={"limited-per-member": 0},
    )
    assert different_member.selected_promo is not None


def test_voided_redemptions_excluded_from_repository_counts() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "voided-counts.sqlite3")
        from server.database import get_connection, initialize_database

        initialize_database()
        from server.promo_repository import PromoRepository
        from server.schemas import PromoCreate

        repository = PromoRepository()
        promo = repository.create(
            PromoCreate(
                name="Count Test", audience="standard", amount_mode="exact",
                price=Decimal("20.00"), granted_seconds=6000, station_scope="standard",
                applies_to="start_session",
                starts_at=NOW - timedelta(hours=1), ends_at=NOW + timedelta(days=1),
            )
        )

        def insert_redemption(member_id: str | None, voided: bool) -> None:
            with get_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO promo_redemptions (
                        redemption_id, promo_id, promo_name_snapshot, audience_snapshot,
                        amount_mode_snapshot, price_snapshot, granted_seconds_snapshot,
                        starts_at_snapshot, ends_at_snapshot,
                        payment_id, member_id, voided_at, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()), promo.promo_id, promo.name, promo.audience,
                        promo.amount_mode, "20.00", promo.granted_seconds,
                        promo.starts_at.isoformat(), promo.ends_at.isoformat(),
                        str(uuid4()), member_id,
                        datetime.now(timezone.utc).isoformat() if voided else None,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )

        insert_redemption(member_id="member-1", voided=False)
        insert_redemption(member_id="member-1", voided=True)
        insert_redemption(member_id="member-2", voided=False)

        assert repository.count_total_redemptions(promo.promo_id) == 2, "voided rows must not count"
        assert repository.count_member_redemptions(promo.promo_id, "member-1") == 1
        assert repository.count_member_redemptions(promo.promo_id, "member-2") == 1
        assert repository.count_member_redemptions(promo.promo_id, "member-3") == 0


def test_greatest_granted_seconds_wins() -> None:
    from server.promo_eligibility import select_best_promo

    smaller = make_promo(promo_id="a", granted_seconds=5000)
    bigger = make_promo(promo_id="b", granted_seconds=7000)
    result = select_best_promo(
        [smaller, bigger], audience="standard", amount=Decimal("20.00"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=0,
    )
    assert result.selected_promo.promo_id == "b"
    assert result.granted_seconds == 7000


def test_priority_tiebreak() -> None:
    from server.promo_eligibility import select_best_promo

    low_priority = make_promo(promo_id="a", granted_seconds=6000, priority=1)
    high_priority = make_promo(promo_id="b", granted_seconds=6000, priority=5)
    result = select_best_promo(
        [low_priority, high_priority], audience="standard", amount=Decimal("20.00"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=0,
    )
    assert result.selected_promo.promo_id == "b"


def test_created_at_tiebreak() -> None:
    from server.promo_eligibility import select_best_promo

    newer = make_promo(promo_id="a", granted_seconds=6000, priority=1, created_at=NOW - timedelta(hours=1))
    older = make_promo(promo_id="b", granted_seconds=6000, priority=1, created_at=NOW - timedelta(days=10))
    result = select_best_promo(
        [newer, older], audience="standard", amount=Decimal("20.00"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=0,
    )
    assert result.selected_promo.promo_id == "b", "earliest created_at must win the tie"


def test_promo_id_final_tiebreak() -> None:
    from server.promo_eligibility import select_best_promo

    same_time = NOW - timedelta(days=1)
    promo_z = make_promo(promo_id="zzzz", granted_seconds=6000, priority=1, created_at=same_time)
    promo_a = make_promo(promo_id="aaaa", granted_seconds=6000, priority=1, created_at=same_time)
    result = select_best_promo(
        [promo_z, promo_a], audience="standard", amount=Decimal("20.00"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=0,
    )
    assert result.selected_promo.promo_id == "aaaa"


def test_equal_or_worse_standard_vip_falls_back_to_normal() -> None:
    from server.promo_eligibility import select_best_promo

    promo = make_promo(granted_seconds=4800)  # e.g. P20 promo -> 80 min
    equal = select_best_promo(
        [promo], audience="standard", amount=Decimal("20.00"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=4800,
    )
    assert equal.selected_promo is None
    assert equal.reason == "not_better_than_base"

    worse = select_best_promo(
        [promo], audience="standard", amount=Decimal("20.00"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=6000,
    )
    assert worse.selected_promo is None
    assert worse.reason == "not_better_than_base"


def test_better_standard_vip_selected() -> None:
    from server.promo_eligibility import select_best_promo

    promo = make_promo(granted_seconds=6000)  # normal P20 = 4800s, promo = 6000s
    result = select_best_promo(
        [promo], audience="standard", amount=Decimal("20.00"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=4800,
    )
    assert result.selected_promo is not None
    assert result.reason == "selected"
    assert result.customer_benefit_seconds == 1200
    assert result.base_granted_seconds == 4800


def test_member_bonus_selection_does_not_change_base_product_seconds() -> None:
    from server.promo_eligibility import select_best_promo

    promo = make_promo(
        audience="member_topup", amount_mode="minimum", price=Decimal("100.00"),
        granted_seconds=3600, station_scope=None, applies_to=None,
    )
    result = select_best_promo(
        [promo], audience="member_topup", amount=Decimal("100.00"), now=NOW,
        member_id="member-1", base_granted_seconds=0,
    )
    assert result.selected_promo is not None
    # base_granted_seconds is the *bonus* baseline (always 0), never the
    # purchased product's own ordinary seconds -- the caller keeps that
    # figure entirely separate and this module never sees or touches it.
    assert result.base_granted_seconds == 0
    assert result.granted_seconds == 3600
    assert result.customer_benefit_seconds == 3600


def test_new_member_baseline_comparison() -> None:
    from server.promo_eligibility import select_best_promo

    promo = make_promo(
        audience="new_member", amount_mode="exact", price=Decimal("100.00"),
        granted_seconds=21600, station_scope=None, applies_to=None,
    )
    # A matching permanent offer exists and grants just as much -> no win.
    no_better = select_best_promo(
        [promo], audience="new_member", amount=Decimal("100.00"), now=NOW,
        base_granted_seconds=21600,
    )
    assert no_better.selected_promo is None
    assert no_better.reason == "not_better_than_base"

    # A matching permanent offer exists and the promo beats it.
    better = select_best_promo(
        [promo], audience="new_member", amount=Decimal("100.00"), now=NOW,
        base_granted_seconds=18000,
    )
    assert better.selected_promo is not None

    # No matching permanent offer at all -- caller supplies no baseline.
    no_baseline = select_best_promo(
        [promo], audience="new_member", amount=Decimal("100.00"), now=NOW,
        base_granted_seconds=None,
    )
    assert no_baseline.selected_promo is not None


def test_deterministic_regardless_of_input_order() -> None:
    from server.promo_eligibility import select_best_promo

    same_time = NOW - timedelta(days=1)
    promos = [
        make_promo(promo_id="c", granted_seconds=6000, priority=1, created_at=same_time),
        make_promo(promo_id="a", granted_seconds=7000, priority=0, created_at=same_time),
        make_promo(promo_id="b", granted_seconds=5000, priority=9, created_at=same_time),
    ]
    forward = select_best_promo(
        promos, audience="standard", amount=Decimal("20.00"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=0,
    )
    reversed_result = select_best_promo(
        list(reversed(promos)), audience="standard", amount=Decimal("20.00"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=0,
    )
    assert forward.selected_promo.promo_id == "a"  # greatest granted_seconds wins regardless of priority
    assert forward.selected_promo.promo_id == reversed_result.selected_promo.promo_id


def test_decimal_money_handling() -> None:
    from server.promo_eligibility import select_best_promo

    promo = make_promo(amount_mode="exact", price=Decimal("19.99"))
    # A value that would be a near-miss under float arithmetic
    # (0.1 + 0.1 + ... rounding artifacts) must still compare exactly.
    almost_equal = Decimal("19.98") + Decimal("0.01")
    assert almost_equal == Decimal("19.99")
    result = select_best_promo(
        [promo], audience="standard", amount=almost_equal, now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=0,
    )
    assert result.selected_promo is not None

    off_by_one_cent = select_best_promo(
        [promo], audience="standard", amount=Decimal("19.98"), now=NOW,
        action="start_session", station_scope="standard", base_granted_seconds=0,
    )
    assert off_by_one_cent.selected_promo is None


def main() -> None:
    tests = [
        test_draft_scheduled_disabled_ended_expired_rejected,
        test_start_inclusive_end_exclusive,
        test_audience_mismatch,
        test_station_scope_mismatch,
        test_applies_to_start_add_both,
        test_exact_amount_matching,
        test_minimum_member_topup_matching,
        test_member_id_requirement,
        test_total_limit_exhausted,
        test_per_member_limit_exhausted,
        test_voided_redemptions_excluded_from_repository_counts,
        test_greatest_granted_seconds_wins,
        test_priority_tiebreak,
        test_created_at_tiebreak,
        test_promo_id_final_tiebreak,
        test_equal_or_worse_standard_vip_falls_back_to_normal,
        test_better_standard_vip_selected,
        test_member_bonus_selection_does_not_change_base_product_seconds,
        test_new_member_baseline_comparison,
        test_deterministic_regardless_of_input_order,
        test_decimal_money_handling,
    ]
    for test in tests:
        test()
        print(f"{test.__name__} passed.")
    print("Promo Maker Stage 2 (eligibility/selection) smoke tests passed.")


if __name__ == "__main__":
    main()

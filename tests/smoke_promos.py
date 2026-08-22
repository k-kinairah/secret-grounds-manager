"""Promo Maker Stage 1 smoke tests: schema, repository, derived status, and
audit trail only. No eligibility resolution, session/member/payment
integration, or API routes exist yet -- none of that is exercised here.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def create_env(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    from server.promo_repository import PromoRepository
    from server.schemas import PromoCreate

    return PromoRepository(), PromoCreate


def make_window(start_offset: timedelta, end_offset: timedelta) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    return now + start_offset, now + end_offset


def base_standard_fields(**overrides: object) -> dict[str, object]:
    starts_at, ends_at = make_window(timedelta(hours=-1), timedelta(days=7))
    fields: dict[str, object] = {
        "name": "August Standard Promo",
        "audience": "standard",
        "amount_mode": "exact",
        "price": Decimal("20.00"),
        "granted_seconds": 6000,
        "station_scope": "standard",
        "applies_to": "start_session",
        "starts_at": starts_at,
        "ends_at": ends_at,
        "priority": 0,
    }
    fields.update(overrides)
    return fields


def base_member_topup_fields(**overrides: object) -> dict[str, object]:
    starts_at, ends_at = make_window(timedelta(hours=-1), timedelta(days=7))
    fields: dict[str, object] = {
        "name": "Load 100 Get 1 Bonus Hour",
        "audience": "member_topup",
        "amount_mode": "minimum",
        "price": Decimal("100.00"),
        "granted_seconds": 3600,
        "starts_at": starts_at,
        "ends_at": ends_at,
    }
    fields.update(overrides)
    return fields


def test_migration_is_idempotent(temp_directory: str) -> None:
    database_path = Path(temp_directory) / "idempotent.sqlite3"
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database, get_connection

    initialize_database()
    initialize_database()  # must not raise, must not duplicate/alter anything

    with get_connection() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"promos", "promo_audit_events", "promo_redemptions"}.issubset(tables)
        # Existing tables are untouched by the new migration block.
        assert "rate_profiles" in tables
        assert "members" in tables
        assert "sessions" in tables
        assert "member_time_transactions" in tables

        # member_time_transactions was explicitly NOT altered in Stage 1;
        # Stage 4 later added the nullable promo_redemption_id linkage
        # column (idempotent add_column_if_missing, no CHECK rebuild) --
        # this second initialize_database() call must not duplicate it.
        member_txn_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(member_time_transactions)").fetchall()
        }
        assert "promo_redemption_id" in member_txn_columns
        assert len(
            [
                row["name"]
                for row in connection.execute("PRAGMA table_info(member_time_transactions)").fetchall()
                if row["name"] == "promo_redemption_id"
            ]
        ) == 1
        transaction_type_check = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='member_time_transactions'"
        ).fetchone()[0]
        assert "promo_bonus_topup" not in transaction_type_check
        assert "'promo_topup'" in transaction_type_check  # existing type, reused unmodified by Stage 4

        promo_count = connection.execute("SELECT COUNT(*) FROM promos").fetchone()[0]
        assert promo_count == 0, "promos must never be auto-seeded"


def test_create_writes_audit_event(temp_directory: str) -> None:
    repository, PromoCreate = create_env(Path(temp_directory) / "create.sqlite3")

    promo = repository.create(PromoCreate(**base_standard_fields()), performed_by_user_id="owner-1")
    assert promo.owner_state == "draft"
    assert promo.status == "Draft"
    assert promo.price == Decimal("20.00")

    events = repository.list_audit_events(promo.promo_id)
    assert len(events) == 1
    assert events[0].action == "created"
    assert events[0].old_values is None
    assert events[0].new_values["owner_state"] == "draft"
    assert events[0].performed_by_user_id == "owner-1"


def test_invalid_audience_scope_mode_combinations_rejected(temp_directory: str) -> None:
    _, PromoCreate = create_env(Path(temp_directory) / "invalid-combos.sqlite3")

    invalid_cases = [
        # standard promo with vip scope
        base_standard_fields(station_scope="vip"),
        # standard promo missing applies_to
        base_standard_fields(applies_to=None),
        # standard promo with a per-member limit (no member identity exists)
        base_standard_fields(per_member_redemption_limit=1),
        # vip promo with standard scope
        base_standard_fields(audience="vip", station_scope="standard", applies_to="both"),
        # minimum amount_mode on a non-member_topup audience
        base_standard_fields(amount_mode="minimum"),
        # member_topup with a station_scope set
        base_member_topup_fields(station_scope="standard"),
        # member_topup with applies_to set
        base_member_topup_fields(applies_to="both"),
        # new_member with a station_scope set
        base_member_topup_fields(audience="new_member", station_scope="vip", amount_mode="exact"),
    ]
    for fields in invalid_cases:
        try:
            PromoCreate(**fields)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"expected rejection for {fields}")


def test_invalid_dates_price_seconds_limits_rejected(temp_directory: str) -> None:
    _, PromoCreate = create_env(Path(temp_directory) / "invalid-values.sqlite3")

    now = datetime.now(timezone.utc)
    invalid_cases = [
        base_standard_fields(ends_at=now - timedelta(days=1)),   # ends before starts
        base_standard_fields(starts_at=now, ends_at=now),         # equal, not strictly later
        base_standard_fields(price=Decimal("0.00")),
        base_standard_fields(price=Decimal("-5.00")),
        base_standard_fields(granted_seconds=0),
        base_standard_fields(granted_seconds=-60),
        base_standard_fields(total_redemption_limit=0),
        base_standard_fields(total_redemption_limit=-1),
    ]
    for fields in invalid_cases:
        try:
            PromoCreate(**fields)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"expected rejection for {fields}")

    # Naive (non-timezone-aware) datetimes must also be rejected -- Stage 1
    # requires canonical UTC values, not ambiguous local wall-clock input.
    try:
        PromoCreate(**base_standard_fields(starts_at=datetime.now(), ends_at=datetime.now() + timedelta(days=1)))
    except ValidationError:
        pass
    else:
        raise AssertionError("expected rejection for a naive (non-UTC) datetime")


def test_status_derivation_all_five_states(temp_directory: str) -> None:
    from server.promo_status import derive_promo_status

    now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    starts_at = now - timedelta(hours=1)
    ends_at = now + timedelta(hours=1)

    assert derive_promo_status("draft", starts_at, ends_at, now) == "Draft"
    assert derive_promo_status("disabled", starts_at, ends_at, now) == "Disabled"
    assert derive_promo_status("ended", starts_at, ends_at, now) == "Ended"
    assert derive_promo_status("enabled", now + timedelta(hours=1), now + timedelta(hours=2), now) == "Scheduled"
    assert derive_promo_status("enabled", starts_at, ends_at, now) == "Active"
    assert derive_promo_status("enabled", now - timedelta(hours=2), now - timedelta(hours=1), now) == "Ended"

    # Explicit draft/disabled/ended override the date math even when the
    # window would otherwise say "Active" or "Scheduled".
    assert derive_promo_status("disabled", starts_at, ends_at, now) == "Disabled"
    assert derive_promo_status("ended", now + timedelta(hours=1), now + timedelta(hours=2), now) == "Ended"


def test_status_boundary_start_inclusive_end_exclusive(temp_directory: str) -> None:
    from server.promo_status import derive_promo_status

    now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    # now == starts_at exactly -> Active (inclusive).
    assert derive_promo_status("enabled", now, now + timedelta(hours=1), now) == "Active"
    # now == ends_at exactly -> Ended (exclusive).
    assert derive_promo_status("enabled", now - timedelta(hours=1), now, now) == "Ended"
    # One microsecond before ends_at -> still Active.
    assert (
        derive_promo_status("enabled", now - timedelta(hours=1), now, now - timedelta(microseconds=1))
        == "Active"
    )


def test_update_preserves_existing_redemptions(temp_directory: str) -> None:
    database_path = Path(temp_directory) / "update-preserves.sqlite3"
    repository, PromoCreate = create_env(database_path)
    from server.database import get_connection
    from server.schemas import PromoUpdate

    promo = repository.create(PromoCreate(**base_standard_fields(name="Original Name")))

    # Simulate a pre-existing redemption snapshot directly (no writer
    # exists for this table in Stage 1) to prove editing the promo never
    # touches it.
    redemption_id = str(uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO promo_redemptions (
                redemption_id, promo_id, promo_name_snapshot, audience_snapshot,
                amount_mode_snapshot, price_snapshot, granted_seconds_snapshot,
                station_scope_snapshot, applies_to_snapshot, starts_at_snapshot,
                ends_at_snapshot, payment_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                redemption_id,
                promo.promo_id,
                "Original Name",
                "standard",
                "exact",
                "20.00",
                6000,
                "standard",
                "start_session",
                promo.starts_at.isoformat(),
                promo.ends_at.isoformat(),
                "fake-payment-id",
                now_iso,
            ),
        )

    repository.update(
        promo.promo_id,
        PromoUpdate(**base_standard_fields(name="Renamed Promo", price=Decimal("25.00"))),
    )

    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM promo_redemptions WHERE redemption_id = ?", (redemption_id,)
        ).fetchone()
    assert row["promo_name_snapshot"] == "Original Name", "editing a promo must never rewrite a redemption snapshot"
    assert row["price_snapshot"] == "20.00"


def test_enable_disable_end_transitions_are_audited(temp_directory: str) -> None:
    repository, PromoCreate = create_env(Path(temp_directory) / "transitions.sqlite3")
    from server.schemas import PromoStateTransition

    promo = repository.create(PromoCreate(**base_standard_fields()))

    enabled = repository.transition_state(
        promo.promo_id, PromoStateTransition(owner_state="enabled"), performed_by_user_id="owner-1"
    )
    assert enabled.owner_state == "enabled"
    assert enabled.status == "Active"

    disabled = repository.transition_state(
        promo.promo_id, PromoStateTransition(owner_state="disabled"), performed_by_user_id="owner-1"
    )
    assert disabled.owner_state == "disabled"
    assert disabled.status == "Disabled"

    reenabled = repository.transition_state(
        promo.promo_id, PromoStateTransition(owner_state="enabled"), performed_by_user_id="owner-1"
    )
    assert reenabled.owner_state == "enabled"

    ended = repository.transition_state(
        promo.promo_id, PromoStateTransition(owner_state="ended"), performed_by_user_id="owner-1"
    )
    assert ended.owner_state == "ended"
    assert ended.status == "Ended"

    events = repository.list_audit_events(promo.promo_id)
    actions = [event.action for event in events]
    assert actions == ["created", "enabled", "disabled", "enabled", "ended"]

    # Idempotent re-request of the same state writes no duplicate event.
    repository.transition_state(promo.promo_id, PromoStateTransition(owner_state="ended"))
    assert len(repository.list_audit_events(promo.promo_id)) == 5


def test_ended_reactivation_requires_future_ends_at(temp_directory: str) -> None:
    repository, PromoCreate = create_env(Path(temp_directory) / "reactivation.sqlite3")
    from fastapi import HTTPException

    from server.schemas import PromoStateTransition

    promo = repository.create(PromoCreate(**base_standard_fields()))
    repository.transition_state(promo.promo_id, PromoStateTransition(owner_state="enabled"))
    repository.transition_state(promo.promo_id, PromoStateTransition(owner_state="ended"))

    # Missing ends_at entirely.
    try:
        repository.transition_state(promo.promo_id, PromoStateTransition(owner_state="enabled"))
        raise AssertionError("expected rejection: reactivation without ends_at")
    except HTTPException as error:
        assert error.status_code == 400

    # A past ends_at.
    try:
        repository.transition_state(
            promo.promo_id,
            PromoStateTransition(
                owner_state="enabled", ends_at=datetime.now(timezone.utc) - timedelta(hours=1)
            ),
        )
        raise AssertionError("expected rejection: reactivation with a past ends_at")
    except HTTPException as error:
        assert error.status_code == 409

    # A valid future ends_at succeeds and is audited as "reactivated", not "enabled".
    future_ends_at = datetime.now(timezone.utc) + timedelta(days=3)
    reactivated = repository.transition_state(
        promo.promo_id, PromoStateTransition(owner_state="enabled", ends_at=future_ends_at)
    )
    assert reactivated.owner_state == "enabled"
    assert reactivated.status == "Active"
    assert reactivated.ends_at == future_ends_at

    events = repository.list_audit_events(promo.promo_id)
    assert events[-1].action == "reactivated"

    # ends_at supplied outside a reactivation is rejected.
    try:
        repository.transition_state(
            promo.promo_id,
            PromoStateTransition(owner_state="disabled", ends_at=datetime.now(timezone.utc) + timedelta(days=1)),
        )
        raise AssertionError("expected rejection: ends_at supplied for a non-reactivation transition")
    except HTTPException as error:
        assert error.status_code == 400


def test_owner_customized_values_are_preserved(temp_directory: str) -> None:
    repository, PromoCreate = create_env(Path(temp_directory) / "preserved.sqlite3")

    promo = repository.create(
        PromoCreate(**base_standard_fields(name="Custom", price=Decimal("17.50"), priority=42)),
        performed_by_user_id="owner-42",
    )
    assert promo.price == Decimal("17.50")
    assert promo.priority == 42
    assert promo.created_by_user_id == "owner-42"

    reloaded = repository.get(promo.promo_id)
    assert reloaded is not None
    assert reloaded.price == Decimal("17.50")
    assert reloaded.priority == 42
    assert reloaded.created_by_user_id == "owner-42", "created_by_user_id must survive updates unchanged"

    from server.schemas import PromoUpdate

    repository.update(promo.promo_id, PromoUpdate(**base_standard_fields(name="Custom", priority=7)))
    after_update = repository.get(promo.promo_id)
    assert after_update.priority == 7
    assert after_update.created_by_user_id == "owner-42", "update() must never alter created_by_user_id"


def test_deterministic_list_ordering(temp_directory: str) -> None:
    repository, PromoCreate = create_env(Path(temp_directory) / "ordering.sqlite3")

    created_ids = [
        repository.create(PromoCreate(**base_standard_fields(name=f"Promo {index}"))).promo_id
        for index in range(5)
    ]

    listed_ids = [promo.promo_id for promo in repository.list()]
    assert listed_ids == created_ids

    # Ordering is stable across repeated calls, not just first-call luck.
    assert [promo.promo_id for promo in repository.list()] == created_ids

    filtered_ids = [promo.promo_id for promo in repository.list(owner_states=["draft"])]
    assert filtered_ids == created_ids


def test_sql_values_are_safely_parameterized(temp_directory: str) -> None:
    repository, PromoCreate = create_env(Path(temp_directory) / "sql-safety.sqlite3")

    dangerous_name = "Robert'); DROP TABLE promos;--"
    promo = repository.create(PromoCreate(**base_standard_fields(name=dangerous_name)))

    reloaded = repository.get(promo.promo_id)
    assert reloaded is not None, "the promos table must still exist -- the string was never executed as SQL"
    assert reloaded.name == dangerous_name

    all_promos = repository.list()
    assert len(all_promos) == 1


def test_member_topup_and_new_member_variants_construct_cleanly(temp_directory: str) -> None:
    """Sanity coverage for the two non-session audiences, distinct from the
    invalid-combination tests above."""
    repository, PromoCreate = create_env(Path(temp_directory) / "audiences.sqlite3")

    topup = repository.create(PromoCreate(**base_member_topup_fields()))
    assert topup.audience == "member_topup"
    assert topup.amount_mode == "minimum"
    assert topup.station_scope is None
    assert topup.applies_to is None

    new_member = repository.create(
        PromoCreate(
            **base_member_topup_fields(
                name="New Member Signup Offer",
                audience="new_member",
                amount_mode="exact",
                price=Decimal("75.00"),
                granted_seconds=14400,
            )
        )
    )
    assert new_member.audience == "new_member"
    assert new_member.amount_mode == "exact"


def main() -> None:
    tests = [
        test_migration_is_idempotent,
        test_create_writes_audit_event,
        test_invalid_audience_scope_mode_combinations_rejected,
        test_invalid_dates_price_seconds_limits_rejected,
        test_status_derivation_all_five_states,
        test_status_boundary_start_inclusive_end_exclusive,
        test_update_preserves_existing_redemptions,
        test_enable_disable_end_transitions_are_audited,
        test_ended_reactivation_requires_future_ends_at,
        test_owner_customized_values_are_preserved,
        test_deterministic_list_ordering,
        test_sql_values_are_safely_parameterized,
        test_member_topup_and_new_member_variants_construct_cleanly,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
        print(f"{test.__name__} passed.")
    print("Promo Maker Stage 1 smoke tests passed.")


if __name__ == "__main__":
    main()

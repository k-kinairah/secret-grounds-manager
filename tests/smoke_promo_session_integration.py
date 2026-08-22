"""Stage 3 smoke tests: Standard/VIP promo quoting, the atomic
claim_standard_or_vip_promo transaction, and promo-aware payment void.

Focused on the highest-risk piece explicitly called out in the task: that
session creation/update, payment creation, and the promo_redemptions
insert genuinely happen in ONE atomic SQLite transaction, that a final
recheck correctly rejects a promo that became unavailable between quote
and confirmation, and that voiding a promo payment atomically frees its
redemption slot. session_service.py's live start_prepaid_session/
add_prepaid_time do not yet call this path automatically -- see the
implementation report for that scoping note.
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def create_env(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    from server.income_attribution import AttributionSnapshot  # noqa: F401
    from server.pc_layout_repository import IncomeGroupRepository, PCLayoutRepository
    from server.payment_repository import PaymentRepository
    from server.promo_repository import PromoRepository
    from server.rate_repository import RateRepository
    from server.session_repository import SessionRepository
    from server.schemas import PCLayoutUpdate

    pc_layout_repository = PCLayoutRepository()
    pc_layout_repository.upsert_layout(
        "PC-TEST",
        PCLayoutUpdate(display_name="PC-TEST", area_label="TEST", layout_order=1, is_vip=False, is_enabled=True),
    )
    pc_layout_repository.upsert_layout(
        "PC-VIP-TEST",
        PCLayoutUpdate(display_name="VIP TEST", area_label="TEST", layout_order=2, is_vip=True, is_enabled=True),
    )

    return {
        "promo_repository": PromoRepository(),
        "rate_repository": RateRepository(),
        "session_repository": SessionRepository(),
        "payment_repository": PaymentRepository(),
        "pc_layout_repository": pc_layout_repository,
        "income_group_repository": IncomeGroupRepository(),
    }


def make_promo_create(**overrides):
    from server.schemas import PromoCreate

    now = datetime.now(timezone.utc)
    fields = {
        "name": "Standard Promo",
        "audience": "standard",
        "amount_mode": "exact",
        "price": Decimal("20.00"),
        "granted_seconds": 6000,
        "station_scope": "standard",
        "applies_to": "start_session",
        "starts_at": now - timedelta(hours=1),
        "ends_at": now + timedelta(days=7),
        "priority": 0,
    }
    fields.update(overrides)
    return PromoCreate(**fields)


def make_session(pc_name: str, seconds: int, amount: Decimal, rate_name: str, is_vip: bool):
    from server.schemas import SessionRecord

    now = datetime.now(timezone.utc)
    return SessionRecord(
        session_id=str(uuid4()),
        pc_name=pc_name,
        status="active",
        started_at=now,
        scheduled_end_at=now + timedelta(seconds=seconds),
        ended_at=None,
        original_seconds=seconds,
        added_seconds=0,
        total_seconds=seconds,
        amount_paid=None,
        ended_reason=None,
        created_at=now,
        rate_id=None,
        rate_name_snapshot=rate_name,
        base_amount=amount,
        additional_amount=Decimal("0.00"),
        total_amount=amount,
        session_mode="prepaid",
        station_type="vip" if is_vip else "standard",
        created_by_user_id="operator-1",
    )


def make_payment(session_id: str, amount: Decimal):
    from server.schemas import PaymentRecord

    now = datetime.now(timezone.utc)
    return PaymentRecord(
        payment_id=str(uuid4()),
        session_id=session_id,
        amount=amount,
        payment_method="cash",
        cash_received=amount,
        change_given=Decimal("0.00"),
        reference_number=None,
        notes=None,
        action_type="session_start",
        related_command_id=None,
        review_required=False,
        shift_id=None,
        recorded_by_user_id="operator-1",
        idempotency_key=str(uuid4()),
        status="completed",
        created_at=now,
    )


def test_standard_quote_beats_normal_calculation(temp_directory: str) -> None:
    env = create_env(Path(temp_directory) / "quote-standard-better.sqlite3")
    from server.promo_quote_service import build_promo_quote

    promo = env["promo_repository"].create(make_promo_create(granted_seconds=6000))  # P20 promo -> 100 min
    env["promo_repository"].transition_state(
        promo.promo_id, __import__("server.schemas", fromlist=["PromoStateTransition"]).PromoStateTransition(owner_state="enabled")
    )

    now = datetime.now(timezone.utc)
    quote = build_promo_quote(
        promo_repository=env["promo_repository"],
        rate_repository=env["rate_repository"],
        pc_name="PC-TEST",
        is_vip_station=False,
        action="start_session",
        amount=Decimal("20.00"),
        now=now,
    )
    assert quote.source_type == "promo"
    assert quote.promo_id == promo.promo_id
    assert quote.base_seconds == 4800  # normal P20 = 80 min
    assert quote.final_seconds == 6000
    assert quote.benefit_seconds == 1200


def test_equal_or_worse_standard_promo_falls_back(temp_directory: str) -> None:
    env = create_env(Path(temp_directory) / "quote-standard-worse.sqlite3")
    from server.promo_quote_service import build_promo_quote
    from server.schemas import PromoStateTransition

    promo = env["promo_repository"].create(make_promo_create(granted_seconds=4800))  # equal to normal
    env["promo_repository"].transition_state(promo.promo_id, PromoStateTransition(owner_state="enabled"))

    quote = build_promo_quote(
        promo_repository=env["promo_repository"],
        rate_repository=env["rate_repository"],
        pc_name="PC-TEST",
        is_vip_station=False,
        action="start_session",
        amount=Decimal("20.00"),
        now=datetime.now(timezone.utc),
    )
    assert quote.source_type == "normal_rate"
    assert quote.promo_id is None
    assert quote.final_seconds == 4800


def test_vip_promo_beats_normal_package(temp_directory: str) -> None:
    env = create_env(Path(temp_directory) / "quote-vip-better.sqlite3")
    from server.promo_quote_service import build_promo_quote
    from server.schemas import PromoStateTransition

    # Normal P30 VIP package = 1 hour (3600s). Promo beats it.
    promo = env["promo_repository"].create(
        make_promo_create(
            name="VIP Promo", audience="vip", price=Decimal("30.00"), granted_seconds=5400,
            station_scope="vip", applies_to="start_session",
        )
    )
    env["promo_repository"].transition_state(promo.promo_id, PromoStateTransition(owner_state="enabled"))

    quote = build_promo_quote(
        promo_repository=env["promo_repository"],
        rate_repository=env["rate_repository"],
        pc_name="PC-VIP-TEST",
        is_vip_station=True,
        action="start_session",
        amount=Decimal("30.00"),
        now=datetime.now(timezone.utc),
    )
    assert quote.source_type == "promo"
    assert quote.base_seconds == 3600
    assert quote.final_seconds == 5400


def test_permanent_vip_250_10hr_package_unchanged(temp_directory: str) -> None:
    env = create_env(Path(temp_directory) / "vip-250-unchanged.sqlite3")
    from server.promo_quote_service import build_promo_quote

    quote = build_promo_quote(
        promo_repository=env["promo_repository"],
        rate_repository=env["rate_repository"],
        pc_name="PC-VIP-TEST",
        is_vip_station=True,
        action="start_session",
        amount=Decimal("250.00"),
        now=datetime.now(timezone.utc),
    )
    assert quote.source_type == "normal_package"
    assert quote.final_seconds == 10 * 3600, "the permanent P250/10hr package must be untouched"

    # Confirm the rate_profiles row itself is unmodified by any promo code.
    rates = env["rate_repository"].list_rates()
    promo_250 = next(r for r in rates if r.name == "VIP Guest - P250 (10 Hour Promo)")
    assert promo_250.price == Decimal("250.00")
    assert promo_250.duration_seconds == 10 * 3600


def test_audience_station_action_mismatch_rejected(temp_directory: str) -> None:
    env = create_env(Path(temp_directory) / "mismatch.sqlite3")
    from server.promo_quote_service import build_promo_quote
    from server.schemas import PromoStateTransition

    promo = env["promo_repository"].create(
        make_promo_create(granted_seconds=6000, applies_to="add_time")  # add_time only
    )
    env["promo_repository"].transition_state(promo.promo_id, PromoStateTransition(owner_state="enabled"))

    quote = build_promo_quote(
        promo_repository=env["promo_repository"],
        rate_repository=env["rate_repository"],
        pc_name="PC-TEST",
        is_vip_station=False,
        action="start_session",
        amount=Decimal("20.00"),
        now=datetime.now(timezone.utc),
    )
    assert quote.source_type != "promo", "add_time-only promo must not apply to start_session"


def test_start_inclusive_end_exclusive_quote(temp_directory: str) -> None:
    env = create_env(Path(temp_directory) / "quote-boundary.sqlite3")
    from server.promo_quote_service import build_promo_quote
    from server.schemas import PromoStateTransition

    now = datetime.now(timezone.utc)
    promo = env["promo_repository"].create(
        make_promo_create(granted_seconds=6000, starts_at=now, ends_at=now + timedelta(hours=1))
    )
    env["promo_repository"].transition_state(promo.promo_id, PromoStateTransition(owner_state="enabled"))

    at_start = build_promo_quote(
        promo_repository=env["promo_repository"], rate_repository=env["rate_repository"],
        pc_name="PC-TEST", is_vip_station=False, action="start_session", amount=Decimal("20.00"), now=now,
    )
    assert at_start.source_type == "promo"

    at_end = build_promo_quote(
        promo_repository=env["promo_repository"], rate_repository=env["rate_repository"],
        pc_name="PC-TEST", is_vip_station=False, action="start_session", amount=Decimal("20.00"),
        now=now + timedelta(hours=1),
    )
    assert at_end.source_type != "promo"


def test_atomic_claim_creates_session_payment_and_redemption_together(temp_directory: str) -> None:
    database_path = Path(temp_directory) / "atomic-claim.sqlite3"
    env = create_env(database_path)
    from server.promo_session_integration import claim_standard_or_vip_promo
    from server.database import get_connection
    from server.schemas import PromoStateTransition

    promo = env["promo_repository"].create(make_promo_create(granted_seconds=6000))
    env["promo_repository"].transition_state(promo.promo_id, PromoStateTransition(owner_state="enabled"))

    session = make_session("PC-TEST", 6000, Decimal("20.00"), "promo", is_vip=False)
    payment = make_payment(session.session_id, Decimal("20.00"))

    outcome = claim_standard_or_vip_promo(
        session_repository=env["session_repository"],
        payment_repository=env["payment_repository"],
        promo_repository=env["promo_repository"],
        pc_layout_repository=env["pc_layout_repository"],
        income_group_repository=env["income_group_repository"],
        session=session,
        payment=payment,
        promo_id=promo.promo_id,
        audience="standard",
        action="start_session",
        station_scope="standard",
        amount=Decimal("20.00"),
        is_update=False,
        now=datetime.now(timezone.utc),
    )
    assert outcome.promo_id == promo.promo_id

    with get_connection() as connection:
        session_row = connection.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session.session_id,)
        ).fetchone()
        payment_row = connection.execute(
            "SELECT * FROM payments WHERE payment_id = ?", (payment.payment_id,)
        ).fetchone()
        redemption_row = connection.execute(
            "SELECT * FROM promo_redemptions WHERE payment_id = ?", (payment.payment_id,)
        ).fetchone()
    assert session_row is not None
    assert payment_row is not None
    assert redemption_row is not None
    assert redemption_row["promo_id"] == promo.promo_id
    assert redemption_row["session_id"] == session.session_id
    assert redemption_row["member_id"] is None
    assert redemption_row["income_group_id_snapshot"] is None or isinstance(
        redemption_row["income_group_id_snapshot"], (str, type(None))
    )


def test_final_claim_rechecks_and_rejects_disabled_promo(temp_directory: str) -> None:
    env = create_env(Path(temp_directory) / "recheck-disabled.sqlite3")
    from server.promo_session_integration import claim_standard_or_vip_promo
    from server.database import get_connection
    from server.schemas import PromoStateTransition

    promo = env["promo_repository"].create(make_promo_create(granted_seconds=6000))
    env["promo_repository"].transition_state(promo.promo_id, PromoStateTransition(owner_state="enabled"))
    # Quoted, then the owner disables it before confirmation.
    env["promo_repository"].transition_state(promo.promo_id, PromoStateTransition(owner_state="disabled"))

    session = make_session("PC-TEST", 6000, Decimal("20.00"), "promo", is_vip=False)
    payment = make_payment(session.session_id, Decimal("20.00"))

    try:
        claim_standard_or_vip_promo(
            session_repository=env["session_repository"],
            payment_repository=env["payment_repository"],
            promo_repository=env["promo_repository"],
            pc_layout_repository=env["pc_layout_repository"],
            income_group_repository=env["income_group_repository"],
            session=session, payment=payment, promo_id=promo.promo_id,
            audience="standard", action="start_session", station_scope="standard",
            amount=Decimal("20.00"), is_update=False, now=datetime.now(timezone.utc),
        )
        raise AssertionError("expected 409 for a promo disabled after quoting")
    except HTTPException as error:
        assert error.status_code == 409

    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM promo_redemptions").fetchone()[0] == 0


def test_exhausted_total_limit_returns_conflict_with_no_partial_writes(temp_directory: str) -> None:
    env = create_env(Path(temp_directory) / "exhausted.sqlite3")
    from server.promo_session_integration import claim_standard_or_vip_promo
    from server.database import get_connection
    from server.schemas import PromoStateTransition

    promo = env["promo_repository"].create(make_promo_create(granted_seconds=6000, total_redemption_limit=1))
    env["promo_repository"].transition_state(promo.promo_id, PromoStateTransition(owner_state="enabled"))

    # First claim succeeds and consumes the only slot.
    session1 = make_session("PC-TEST", 6000, Decimal("20.00"), "promo", is_vip=False)
    payment1 = make_payment(session1.session_id, Decimal("20.00"))
    claim_standard_or_vip_promo(
        session_repository=env["session_repository"], payment_repository=env["payment_repository"],
        promo_repository=env["promo_repository"], pc_layout_repository=env["pc_layout_repository"],
        income_group_repository=env["income_group_repository"], session=session1, payment=payment1,
        promo_id=promo.promo_id, audience="standard", action="start_session", station_scope="standard",
        amount=Decimal("20.00"), is_update=False, now=datetime.now(timezone.utc),
    )

    # Second claim must be rejected, leaving no trace.
    session2 = make_session("PC-TEST-2", 6000, Decimal("20.00"), "promo", is_vip=False)
    payment2 = make_payment(session2.session_id, Decimal("20.00"))
    try:
        claim_standard_or_vip_promo(
            session_repository=env["session_repository"], payment_repository=env["payment_repository"],
            promo_repository=env["promo_repository"], pc_layout_repository=env["pc_layout_repository"],
            income_group_repository=env["income_group_repository"], session=session2, payment=payment2,
            promo_id=promo.promo_id, audience="standard", action="start_session", station_scope="standard",
            amount=Decimal("20.00"), is_update=False, now=datetime.now(timezone.utc),
        )
        raise AssertionError("expected 409 for an exhausted total limit")
    except HTTPException as error:
        assert error.status_code == 409

    with get_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sessions WHERE pc_name = ?", ("PC-TEST-2",)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM promo_redemptions WHERE promo_id = ?", (promo.promo_id,)
        ).fetchone()[0] == 1, "only the first, successful claim may exist"


def test_retry_with_same_payment_id_creates_exactly_one_redemption(temp_directory: str) -> None:
    """Proves the unique index (idx_promo_redemptions_one_per_payment)
    guards against a retried request creating a duplicate redemption."""
    env = create_env(Path(temp_directory) / "unique-per-payment.sqlite3")
    from server.database import get_connection
    from server.schemas import PromoStateTransition

    promo = env["promo_repository"].create(make_promo_create(granted_seconds=6000))
    env["promo_repository"].transition_state(promo.promo_id, PromoStateTransition(owner_state="enabled"))

    session = make_session("PC-TEST", 6000, Decimal("20.00"), "promo", is_vip=False)
    payment = make_payment(session.session_id, Decimal("20.00"))
    env["session_repository"].create_session(session)
    env["payment_repository"].create_payment(payment)

    with get_connection() as connection:
        env["promo_repository"].claim_redemption_for_standard_or_vip(
            connection, promo, session_id=session.session_id, payment_id=payment.payment_id,
            pc_name_snapshot="PC-TEST", income_group_id_snapshot=None, income_group_name_snapshot=None,
            operator_user_id="operator-1", now=datetime.now(timezone.utc),
        )

    import sqlite3

    try:
        with get_connection() as connection:
            env["promo_repository"].claim_redemption_for_standard_or_vip(
                connection, promo, session_id=session.session_id, payment_id=payment.payment_id,
                pc_name_snapshot="PC-TEST", income_group_id_snapshot=None, income_group_name_snapshot=None,
                operator_user_id="operator-1", now=datetime.now(timezone.utc),
            )
        raise AssertionError("expected a uniqueness violation on retry with the same payment_id")
    except sqlite3.IntegrityError:
        pass

    with get_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM promo_redemptions WHERE payment_id = ?", (payment.payment_id,)
        ).fetchone()[0]
    assert count == 1


def test_payment_void_marks_redemption_voided_atomically(temp_directory: str) -> None:
    env = create_env(Path(temp_directory) / "void-atomic.sqlite3")
    from server.promo_session_integration import claim_standard_or_vip_promo
    from server.database import get_connection
    from server.schemas import PromoStateTransition
    from server.billing_service import BillingService
    from server.shift_repository import ShiftRepository

    promo = env["promo_repository"].create(make_promo_create(granted_seconds=6000, total_redemption_limit=5))
    env["promo_repository"].transition_state(promo.promo_id, PromoStateTransition(owner_state="enabled"))

    session = make_session("PC-TEST", 6000, Decimal("20.00"), "promo", is_vip=False)
    payment = make_payment(session.session_id, Decimal("20.00"))
    claim_standard_or_vip_promo(
        session_repository=env["session_repository"], payment_repository=env["payment_repository"],
        promo_repository=env["promo_repository"], pc_layout_repository=env["pc_layout_repository"],
        income_group_repository=env["income_group_repository"], session=session, payment=payment,
        promo_id=promo.promo_id, audience="standard", action="start_session", station_scope="standard",
        amount=Decimal("20.00"), is_update=False, now=datetime.now(timezone.utc),
    )

    billing_service = BillingService(env["session_repository"], env["payment_repository"], ShiftRepository())
    billing_service.void_payment(payment.payment_id, "Test void", actor_user_id="owner-1")

    with get_connection() as connection:
        redemption_row = connection.execute(
            "SELECT * FROM promo_redemptions WHERE payment_id = ?", (payment.payment_id,)
        ).fetchone()
        payment_row = connection.execute(
            "SELECT * FROM payments WHERE payment_id = ?", (payment.payment_id,)
        ).fetchone()
    assert redemption_row["voided_at"] is not None
    assert redemption_row["void_reason"] == "Test void"
    assert redemption_row["promo_name_snapshot"] == promo.name, "snapshot survives voiding"
    assert payment_row["status"] == "voided"

    # The slot is freed: a second redemption for the same promo now succeeds.
    session2 = make_session("PC-TEST-2", 6000, Decimal("20.00"), "promo", is_vip=False)
    payment2 = make_payment(session2.session_id, Decimal("20.00"))
    outcome = claim_standard_or_vip_promo(
        session_repository=env["session_repository"], payment_repository=env["payment_repository"],
        promo_repository=env["promo_repository"], pc_layout_repository=env["pc_layout_repository"],
        income_group_repository=env["income_group_repository"], session=session2, payment=payment2,
        promo_id=promo.promo_id, audience="standard", action="start_session", station_scope="standard",
        amount=Decimal("20.00"), is_update=False, now=datetime.now(timezone.utc),
    )
    assert outcome.promo_id == promo.promo_id


def test_redemption_retains_snapshot_after_promo_edit(temp_directory: str) -> None:
    env = create_env(Path(temp_directory) / "snapshot-survives-edit.sqlite3")
    from server.promo_session_integration import claim_standard_or_vip_promo
    from server.database import get_connection
    from server.schemas import PromoStateTransition, PromoUpdate

    promo = env["promo_repository"].create(make_promo_create(name="Original", granted_seconds=6000))
    env["promo_repository"].transition_state(promo.promo_id, PromoStateTransition(owner_state="enabled"))

    session = make_session("PC-TEST", 6000, Decimal("20.00"), "promo", is_vip=False)
    payment = make_payment(session.session_id, Decimal("20.00"))
    claim_standard_or_vip_promo(
        session_repository=env["session_repository"], payment_repository=env["payment_repository"],
        promo_repository=env["promo_repository"], pc_layout_repository=env["pc_layout_repository"],
        income_group_repository=env["income_group_repository"], session=session, payment=payment,
        promo_id=promo.promo_id, audience="standard", action="start_session", station_scope="standard",
        amount=Decimal("20.00"), is_update=False, now=datetime.now(timezone.utc),
    )

    env["promo_repository"].update(promo.promo_id, PromoUpdate(**make_promo_create(name="Renamed", granted_seconds=9999).model_dump()))

    with get_connection() as connection:
        row = connection.execute(
            "SELECT promo_name_snapshot, granted_seconds_snapshot FROM promo_redemptions WHERE payment_id = ?",
            (payment.payment_id,),
        ).fetchone()
    assert row["promo_name_snapshot"] == "Original"
    assert row["granted_seconds_snapshot"] == 6000


def main() -> None:
    tests = [
        test_standard_quote_beats_normal_calculation,
        test_equal_or_worse_standard_promo_falls_back,
        test_vip_promo_beats_normal_package,
        test_permanent_vip_250_10hr_package_unchanged,
        test_audience_station_action_mismatch_rejected,
        test_start_inclusive_end_exclusive_quote,
        test_atomic_claim_creates_session_payment_and_redemption_together,
        test_final_claim_rechecks_and_rejects_disabled_promo,
        test_exhausted_total_limit_returns_conflict_with_no_partial_writes,
        test_retry_with_same_payment_id_creates_exactly_one_redemption,
        test_payment_void_marks_redemption_voided_atomically,
        test_redemption_retains_snapshot_after_promo_edit,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
        print(f"{test.__name__} passed.")
    print("Promo Maker Stage 3 (session/payment integration) smoke tests passed.")


if __name__ == "__main__":
    main()

"""Stage 2 (payment-time income attribution) tests.

Covers: session-linked payments always snapshotting the session's PC/
income group; member purchases attributing immediately only when the
member is already playing AND the balance type is usable on that
station; member purchases with no eligible active session staying
'pending' with balance/shift/operator crediting unaffected; historical-
reassignment safety; refund/void neutrality; idempotency; atomicity; and
the unconfigured-PC safe-fallback behavior. No pending-attribution sweep
exists yet -- that is Stage 3.
"""

import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def create_repositories(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    from server.billing_service import BillingService
    from server.member_repository import MemberRepository
    from server.payment_repository import PaymentRepository
    from server.pc_layout_repository import IncomeGroupRepository, PCLayoutRepository
    from server.schemas import UserCreateRequest
    from server.session_repository import SessionRepository
    from server.shift_repository import ShiftRepository
    from server.user_repository import UserRepository

    user_repository = UserRepository()
    cashier_public = user_repository.create_user(
        UserCreateRequest(username="attribution-cashier", display_name="Attribution Cashier", password="cashier-pass", role="cashier")
    )
    actor = user_repository.get_user(cashier_public.user_id)
    assert actor is not None

    payment_repository = PaymentRepository()
    shift_repository = ShiftRepository()
    session_repository = SessionRepository()
    pc_layout_repository = PCLayoutRepository()
    income_group_repository = IncomeGroupRepository()
    member_repository = MemberRepository(payment_repository, shift_repository)
    billing_service = BillingService(session_repository, payment_repository, shift_repository, user_repository)
    return {
        "member_repository": member_repository,
        "payment_repository": payment_repository,
        "billing_service": billing_service,
        "session_repository": session_repository,
        "shift_repository": shift_repository,
        "pc_layout_repository": pc_layout_repository,
        "income_group_repository": income_group_repository,
        "actor": actor,
    }


def create_test_member(member_repository, username: str):
    from server.schemas import MemberCreateRequest

    return member_repository.create_member(
        MemberCreateRequest(username=username, display_name=username, pin="1234", confirm_pin="1234"),
        None,
    )


def make_purchase_request(product_id: str, idempotency_key: str | None = None):
    from server.schemas import MemberPurchaseRequest

    return MemberPurchaseRequest(
        product_id=product_id,
        payment_method="cash",
        cash_received="1000.00",
        idempotency_key=idempotency_key,
    )


def create_guest_session(session_repository, session_id: str, pc_name: str, amount: Decimal):
    from server.schemas import SessionRecord
    from server.time_utils import current_utc_time

    now = current_utc_time()
    session_repository.create_session(
        SessionRecord(
            session_id=session_id,
            pc_name=pc_name,
            status="active",
            started_at=now,
            scheduled_end_at=now,
            original_seconds=3600,
            total_seconds=3600,
            created_at=now,
            base_amount=amount,
            total_amount=amount,
            session_mode="prepaid",
        )
    )


def create_active_member_session(session_repository, session_id: str, pc_name: str, member_id: str):
    from server.schemas import SessionRecord
    from server.time_utils import current_utc_time

    now = current_utc_time()
    session_repository.create_session(
        SessionRecord(
            session_id=session_id,
            pc_name=pc_name,
            status="active",
            started_at=now,
            scheduled_end_at=now,
            original_seconds=3600,
            total_seconds=3600,
            created_at=now,
            session_mode="member_balance",
            member_id=member_id,
        )
    )


def reassign_pc_income_group(database_path_env_already_set: bool, pc_name: str, income_group_id: str) -> None:
    from server.database import get_connection

    with get_connection() as connection:
        connection.execute(
            "UPDATE pc_layouts SET income_group_id = ? WHERE pc_name = ?",
            (income_group_id, pc_name),
        )


def create_unconfigured_pc(pc_layout_repository, pc_name: str) -> None:
    from server.schemas import PCLayoutUpdate

    pc_layout_repository.upsert_layout(
        pc_name,
        PCLayoutUpdate(display_name=pc_name, area_label="UNCONFIGURED", layout_order=999, is_vip=False, is_enabled=True),
    )


def test_guest_payments_snapshot_seeded_income_groups(temp_directory: str) -> None:
    repos = create_repositories(Path(temp_directory) / "guest-snapshot.sqlite3")
    billing_service = repos["billing_service"]
    session_repository = repos["session_repository"]
    actor = repos["actor"]

    from server.schemas import PaymentCreateRequest

    create_guest_session(session_repository, "session-pc11", "PC-11", Decimal("15.00"))
    billing_11 = billing_service.record_payment(
        "session-pc11",
        PaymentCreateRequest(payment_method="cash", amount=Decimal("15.00"), cash_received=Decimal("15.00")),
        actor_user_id=actor.user_id,
    )
    payment_11 = billing_11.payments[0]
    assert payment_11.pc_name_snapshot == "PC-11"
    assert payment_11.income_group_id_snapshot == "gene"
    assert payment_11.income_group_name_snapshot == "Gene"
    assert payment_11.attribution_status == "assigned"
    assert payment_11.attribution_assigned_at is not None
    assert payment_11.attribution_session_id == "session-pc11"
    assert payment_11.status == "completed"

    create_guest_session(session_repository, "session-pc15", "PC-15", Decimal("30.00"))
    billing_15 = billing_service.record_payment(
        "session-pc15",
        PaymentCreateRequest(payment_method="cash", amount=Decimal("30.00"), cash_received=Decimal("30.00")),
        actor_user_id=actor.user_id,
    )
    payment_15 = billing_15.payments[0]
    assert payment_15.pc_name_snapshot == "PC-15"
    assert payment_15.income_group_id_snapshot == "tyrone"
    assert payment_15.income_group_name_snapshot == "Tyrone"
    assert payment_15.attribution_status == "assigned"


def test_reassignment_never_mutates_old_payment_new_payment_uses_new_group(temp_directory: str) -> None:
    repos = create_repositories(Path(temp_directory) / "reassignment.sqlite3")
    billing_service = repos["billing_service"]
    session_repository = repos["session_repository"]
    payment_repository = repos["payment_repository"]
    actor = repos["actor"]

    from server.schemas import PaymentCreateRequest

    create_guest_session(session_repository, "session-old", "PC-11", Decimal("15.00"))
    old_billing = billing_service.record_payment(
        "session-old",
        PaymentCreateRequest(payment_method="cash", amount=Decimal("15.00"), cash_received=Decimal("15.00")),
        actor_user_id=actor.user_id,
    )
    old_payment_id = old_billing.payments[0].payment_id
    assert old_billing.payments[0].income_group_id_snapshot == "gene"

    # Owner reassigns PC-11 away from Gene, to United.
    reassign_pc_income_group(True, "PC-11", "united")

    create_guest_session(session_repository, "session-new", "PC-11", Decimal("20.00"))
    new_billing = billing_service.record_payment(
        "session-new",
        PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00")),
        actor_user_id=actor.user_id,
    )
    assert new_billing.payments[0].income_group_id_snapshot == "united"
    assert new_billing.payments[0].income_group_name_snapshot == "United"

    # The old payment must be completely untouched by the reassignment.
    reloaded_old = payment_repository.get_payment(old_payment_id)
    assert reloaded_old is not None
    assert reloaded_old.income_group_id_snapshot == "gene"
    assert reloaded_old.income_group_name_snapshot == "Gene"
    assert reloaded_old.pc_name_snapshot == "PC-11"


def test_standard_topup_while_active_standard_session_assigns_immediately(temp_directory: str) -> None:
    repos = create_repositories(Path(temp_directory) / "standard-on-standard.sqlite3")
    member_repository = repos["member_repository"]
    session_repository = repos["session_repository"]
    actor = repos["actor"]
    member = create_test_member(member_repository, "playing.standard")

    create_active_member_session(session_repository, "member-session-standard", "PC-11", member.member_id)
    result = member_repository.purchase(member.member_id, make_purchase_request("member-promo"), actor)

    assert result.payment.status == "completed"
    assert result.payment.attribution_status == "assigned"
    assert result.payment.pc_name_snapshot == "PC-11"
    assert result.payment.income_group_id_snapshot == "gene"
    assert result.payment.attribution_session_id == "member-session-standard"
    assert result.member.time_balance_seconds == 4 * 3600
    # The active session itself is not extended by the purchase.
    session = session_repository.get_session("member-session-standard")
    assert session is not None
    assert session.total_seconds == 3600


def test_standard_topup_while_active_vip_session_assigns_immediately(temp_directory: str) -> None:
    repos = create_repositories(Path(temp_directory) / "standard-on-vip.sqlite3")
    member_repository = repos["member_repository"]
    session_repository = repos["session_repository"]
    actor = repos["actor"]
    member = create_test_member(member_repository, "playing.vip.buys.standard")

    create_active_member_session(session_repository, "member-session-vip-1", "PC-15", member.member_id)
    result = member_repository.purchase(member.member_id, make_purchase_request("member-promo"), actor)

    assert result.payment.attribution_status == "assigned"
    assert result.payment.pc_name_snapshot == "PC-15"
    assert result.payment.income_group_id_snapshot == "tyrone"
    assert result.payment.attribution_session_id == "member-session-vip-1"


def test_vip_promo_while_active_vip_session_assigns_immediately(temp_directory: str) -> None:
    repos = create_repositories(Path(temp_directory) / "vip-on-vip.sqlite3")
    member_repository = repos["member_repository"]
    session_repository = repos["session_repository"]
    actor = repos["actor"]
    member = create_test_member(member_repository, "playing.vip.buys.vip")

    create_active_member_session(session_repository, "member-session-vip-2", "PC-15", member.member_id)
    result = member_repository.purchase(member.member_id, make_purchase_request("vip-promo-250"), actor)

    assert result.payment.attribution_status == "assigned"
    assert result.payment.pc_name_snapshot == "PC-15"
    assert result.payment.income_group_id_snapshot == "tyrone"
    assert result.member.vip_balance_seconds == 36000


def test_vip_promo_while_active_standard_session_remains_pending(temp_directory: str) -> None:
    repos = create_repositories(Path(temp_directory) / "vip-on-standard.sqlite3")
    member_repository = repos["member_repository"]
    session_repository = repos["session_repository"]
    actor = repos["actor"]
    member = create_test_member(member_repository, "playing.standard.buys.vip")

    create_active_member_session(session_repository, "member-session-standard-2", "PC-11", member.member_id)
    result = member_repository.purchase(member.member_id, make_purchase_request("vip-promo-250"), actor)

    # Must NOT attribute VIP revenue to Gene just because the member is
    # sitting at a Standard PC right now.
    assert result.payment.status == "completed"
    assert result.payment.attribution_status == "pending"
    assert result.payment.pc_name_snapshot is None
    assert result.payment.income_group_id_snapshot is None
    assert result.payment.income_group_name_snapshot is None
    # Balance and shift crediting are unaffected by the pending attribution.
    assert result.member.vip_balance_seconds == 36000


def test_standard_topup_with_no_active_session_remains_pending(temp_directory: str) -> None:
    repos = create_repositories(Path(temp_directory) / "standard-no-session.sqlite3")
    member_repository = repos["member_repository"]
    actor = repos["actor"]
    member = create_test_member(member_repository, "idle.standard")

    result = member_repository.purchase(member.member_id, make_purchase_request("member-promo"), actor)

    assert result.payment.status == "completed"
    assert result.payment.attribution_status == "pending"
    assert result.payment.pc_name_snapshot is None
    assert result.payment.income_group_id_snapshot is None
    assert result.payment.income_group_name_snapshot is None
    assert result.payment.attribution_assigned_at is None
    assert result.payment.attribution_session_id is None
    assert result.member.time_balance_seconds == 4 * 3600


def test_vip_promo_with_no_active_session_remains_pending(temp_directory: str) -> None:
    repos = create_repositories(Path(temp_directory) / "vip-no-session.sqlite3")
    member_repository = repos["member_repository"]
    actor = repos["actor"]
    member = create_test_member(member_repository, "idle.vip")

    result = member_repository.purchase(member.member_id, make_purchase_request("vip-promo-250"), actor)

    assert result.payment.status == "completed"
    assert result.payment.attribution_status == "pending"
    assert result.payment.pc_name_snapshot is None
    assert result.member.vip_balance_seconds == 36000


def test_account_creation_purchase_remains_pending(temp_directory: str) -> None:
    repos = create_repositories(Path(temp_directory) / "account-creation.sqlite3")
    member_repository = repos["member_repository"]
    actor = repos["actor"]
    member = create_test_member(member_repository, "brand.new")

    # A brand-new member cannot possibly have an active session yet.
    result = member_repository.purchase(member.member_id, make_purchase_request("membership-fee"), actor)

    assert result.payment.status == "completed"
    assert result.payment.attribution_status == "pending"
    assert result.payment.pc_name_snapshot is None
    assert result.member.time_balance_seconds == 6 * 3600


def test_shift_and_operator_fields_populated_for_pending_purchase(temp_directory: str) -> None:
    repos = create_repositories(Path(temp_directory) / "shift-pending.sqlite3")
    member_repository = repos["member_repository"]
    shift_repository = repos["shift_repository"]
    actor = repos["actor"]
    member = create_test_member(member_repository, "shift.tracked")

    from server.schemas import ShiftOpenRequest

    shift_detail = shift_repository.open_shift(ShiftOpenRequest(opening_cash=Decimal("500.00")), actor)

    result = member_repository.purchase(member.member_id, make_purchase_request("member-promo"), actor)

    assert result.payment.attribution_status == "pending"
    assert result.payment.shift_id == shift_detail.shift.shift_id
    assert result.payment.recorded_by_user_id == actor.user_id

    totals = shift_repository.get_shift(shift_detail.shift.shift_id, actor).totals
    assert totals.gross_session_sales == Decimal("50.00")


def test_failed_member_purchase_rolls_back_everything(temp_directory: str) -> None:
    repos = create_repositories(Path(temp_directory) / "failed-purchase.sqlite3")
    member_repository = repos["member_repository"]
    payment_repository = repos["payment_repository"]
    actor = repos["actor"]
    member = create_test_member(member_repository, "failure.member")

    original_apply_balance_delta = member_repository.apply_balance_delta

    def failing_apply_balance_delta(*args, **kwargs):
        raise RuntimeError("simulated crash mid-purchase")

    member_repository.apply_balance_delta = failing_apply_balance_delta  # type: ignore[method-assign]
    idempotency_key = "stage2-failure-key"
    try:
        try:
            member_repository.purchase(member.member_id, make_purchase_request("member-promo", idempotency_key), actor)
        except RuntimeError:
            pass
        else:
            raise AssertionError("The simulated failure must propagate")
    finally:
        member_repository.apply_balance_delta = original_apply_balance_delta  # type: ignore[method-assign]

    assert payment_repository.get_payment_by_idempotency_key(idempotency_key) is None
    reloaded = member_repository.get_member(member.member_id)
    assert reloaded is not None
    assert reloaded.time_balance_seconds == 0
    assert member_repository.list_ledger(member.member_id) == []

    retry = member_repository.purchase(member.member_id, make_purchase_request("member-promo", idempotency_key), actor)
    assert retry.member.time_balance_seconds == 4 * 3600
    assert retry.payment.attribution_status == "pending"


def test_idempotent_retry_does_not_alter_attribution(temp_directory: str) -> None:
    repos = create_repositories(Path(temp_directory) / "idempotent-retry.sqlite3")
    member_repository = repos["member_repository"]
    session_repository = repos["session_repository"]
    actor = repos["actor"]
    member = create_test_member(member_repository, "retry.member")

    create_active_member_session(session_repository, "member-session-retry", "PC-11", member.member_id)
    idempotency_key = "stage2-retry-key"

    first = member_repository.purchase(member.member_id, make_purchase_request("member-promo", idempotency_key), actor)
    assert first.payment.attribution_status == "assigned"
    assert first.payment.income_group_id_snapshot == "gene"

    retry = member_repository.purchase(member.member_id, make_purchase_request("member-promo", idempotency_key), actor)
    assert retry.payment.payment_id == first.payment.payment_id
    assert retry.payment.attribution_status == "assigned"
    assert retry.payment.income_group_id_snapshot == "gene"
    assert retry.payment.pc_name_snapshot == "PC-11"
    assert retry.member.time_balance_seconds == 4 * 3600, "retry must not double-credit"


def test_refund_of_pending_purchase_never_becomes_eligible(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRefundRequest

    repos = create_repositories(Path(temp_directory) / "refund-pending.sqlite3")
    member_repository = repos["member_repository"]
    payment_repository = repos["payment_repository"]
    actor = repos["actor"]
    member = create_test_member(member_repository, "refund.pending.member")

    purchase = member_repository.purchase(member.member_id, make_purchase_request("member-promo"), actor)
    assert purchase.payment.attribution_status == "pending"

    refunded = member_repository.refund_purchase(
        purchase.payment.payment_id,
        MemberPurchaseRefundRequest(reason="Customer cancellation"),
        actor,
    )
    assert refunded.member.time_balance_seconds == 0

    reloaded = payment_repository.get_payment(purchase.payment.payment_id)
    assert reloaded is not None
    assert reloaded.status == "voided"
    assert reloaded.attribution_status == "pending"
    assert reloaded.pc_name_snapshot is None
    assert reloaded.income_group_id_snapshot is None
    # A future pending-attribution sweep must filter on status='completed'
    # -- this row can never match that filter again, so it can never be
    # swept into a group after being refunded.
    assert reloaded.status != "completed"


def test_void_of_assigned_purchase_preserves_snapshots(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRefundRequest

    repos = create_repositories(Path(temp_directory) / "refund-assigned.sqlite3")
    member_repository = repos["member_repository"]
    payment_repository = repos["payment_repository"]
    session_repository = repos["session_repository"]
    actor = repos["actor"]
    member = create_test_member(member_repository, "refund.assigned.member")

    create_active_member_session(session_repository, "member-session-refund", "PC-13", member.member_id)
    purchase = member_repository.purchase(member.member_id, make_purchase_request("member-promo"), actor)
    assert purchase.payment.attribution_status == "assigned"
    assert purchase.payment.income_group_id_snapshot == "mama-lala"

    before = payment_repository.get_payment(purchase.payment.payment_id)
    assert before is not None

    member_repository.refund_purchase(
        purchase.payment.payment_id,
        MemberPurchaseRefundRequest(reason="Customer cancellation"),
        actor,
    )

    after = payment_repository.get_payment(purchase.payment.payment_id)
    assert after is not None
    assert after.status == "voided"
    assert after.pc_name_snapshot == before.pc_name_snapshot == "PC-13"
    assert after.income_group_id_snapshot == before.income_group_id_snapshot == "mama-lala"
    assert after.income_group_name_snapshot == before.income_group_name_snapshot == "Mama/Lala"
    assert after.attribution_status == before.attribution_status == "assigned"
    assert after.attribution_assigned_at == before.attribution_assigned_at
    assert after.attribution_session_id == before.attribution_session_id == "member-session-refund"


def test_unconfigured_pc_guest_payment_stays_pending_with_pc_name_retained(temp_directory: str) -> None:
    repos = create_repositories(Path(temp_directory) / "unconfigured-guest.sqlite3")
    billing_service = repos["billing_service"]
    session_repository = repos["session_repository"]
    pc_layout_repository = repos["pc_layout_repository"]
    actor = repos["actor"]

    from server.schemas import PaymentCreateRequest

    create_unconfigured_pc(pc_layout_repository, "PC-CUSTOM")
    create_guest_session(session_repository, "session-unconfigured", "PC-CUSTOM", Decimal("10.00"))

    billing = billing_service.record_payment(
        "session-unconfigured",
        PaymentCreateRequest(payment_method="cash", amount=Decimal("10.00"), cash_received=Decimal("10.00")),
        actor_user_id=actor.user_id,
    )
    payment = billing.payments[0]
    assert payment.status == "completed"
    assert payment.attribution_status == "pending"
    # The PC itself IS known (unlike the no-active-session member case) --
    # only its income-group ownership is unresolved, so the PC name is
    # retained for a future manual/administrative resolution.
    assert payment.pc_name_snapshot == "PC-CUSTOM"
    assert payment.income_group_id_snapshot is None
    assert payment.income_group_name_snapshot is None
    assert payment.attribution_assigned_at is None
    assert payment.attribution_session_id is None


def test_unconfigured_pc_member_purchase_stays_pending_with_pc_name_retained(temp_directory: str) -> None:
    repos = create_repositories(Path(temp_directory) / "unconfigured-member.sqlite3")
    member_repository = repos["member_repository"]
    session_repository = repos["session_repository"]
    pc_layout_repository = repos["pc_layout_repository"]
    actor = repos["actor"]
    member = create_test_member(member_repository, "unconfigured.player")

    create_unconfigured_pc(pc_layout_repository, "PC-CUSTOM2")
    create_active_member_session(session_repository, "member-session-unconfigured", "PC-CUSTOM2", member.member_id)

    result = member_repository.purchase(member.member_id, make_purchase_request("member-promo"), actor)
    assert result.payment.status == "completed"
    assert result.payment.attribution_status == "pending"
    assert result.payment.pc_name_snapshot == "PC-CUSTOM2"
    assert result.payment.income_group_id_snapshot is None
    assert result.member.time_balance_seconds == 4 * 3600


def test_stage1_migration_columns_still_correct_after_stage2(temp_directory: str) -> None:
    """Guards against Stage 2 accidentally changing Stage 1's migration
    behavior (historical rows, seed data, backfill)."""
    repos = create_repositories(Path(temp_directory) / "stage1-guard.sqlite3")
    pc_layout_repository = repos["pc_layout_repository"]
    income_group_repository = repos["income_group_repository"]

    groups = {g.income_group_id: g.name for g in income_group_repository.list_income_groups()}
    assert groups == {"united": "United", "gene": "Gene", "mama-lala": "Mama/Lala", "tyrone": "Tyrone"}

    layout = pc_layout_repository.get_layout("PC-15")
    assert layout is not None
    assert layout.income_group_id == "tyrone"
    assert layout.is_vip is True


def main() -> None:
    tests = [
        test_guest_payments_snapshot_seeded_income_groups,
        test_reassignment_never_mutates_old_payment_new_payment_uses_new_group,
        test_standard_topup_while_active_standard_session_assigns_immediately,
        test_standard_topup_while_active_vip_session_assigns_immediately,
        test_vip_promo_while_active_vip_session_assigns_immediately,
        test_vip_promo_while_active_standard_session_remains_pending,
        test_standard_topup_with_no_active_session_remains_pending,
        test_vip_promo_with_no_active_session_remains_pending,
        test_account_creation_purchase_remains_pending,
        test_shift_and_operator_fields_populated_for_pending_purchase,
        test_failed_member_purchase_rolls_back_everything,
        test_idempotent_retry_does_not_alter_attribution,
        test_refund_of_pending_purchase_never_becomes_eligible,
        test_void_of_assigned_purchase_preserves_snapshots,
        test_unconfigured_pc_guest_payment_stays_pending_with_pc_name_retained,
        test_unconfigured_pc_member_purchase_stays_pending_with_pc_name_retained,
        test_stage1_migration_columns_still_correct_after_stage2,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
    print("Stage 2 income attribution smoke tests passed.")


if __name__ == "__main__":
    main()

"""Stage 4 smoke tests: Member Top-up promos and atomic refund reversal.

Covers eligibility (product_type/balance_type metadata, never names), the
advisory quote route, expected_promo_id, the atomic purchase transaction
(payment + base + bonus + redemption), idempotency, limits, and the atomic
refund reversal (base + bonus together, snapshot preservation, slot
release, active-reservation/insufficient-balance safeguards, and that the
generic payment-void route cannot bypass member-balance reversal).
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def create_env(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    import server.main as main_module
    from server.schemas import PCLayoutUpdate, UserCreateRequest
    from server.user_repository import UserRepository

    main_module.pc_layout_repository.upsert_layout(
        "PC-MEMBER",
        PCLayoutUpdate(display_name="PC-MEMBER", area_label="TEST", layout_order=1, is_vip=False, is_enabled=True, income_group_id="tyrone"),
    )

    user_repository = UserRepository()
    owner = user_repository.create_user(
        UserCreateRequest(username="owner4", display_name="Owner 4", password="password1", role="owner")
    )
    admin = user_repository.create_user(
        UserCreateRequest(username="admin4", display_name="Admin 4", password="password2", role="admin")
    )
    cashier = user_repository.create_user(
        UserCreateRequest(username="cashier4", display_name="Cashier 4", password="password3", role="cashier")
    )
    return main_module, owner, admin, cashier


def create_member(main_module, owner, code="M001"):
    from server.schemas import MemberCreateRequest

    return main_module.member_repository.create_member(
        MemberCreateRequest(member_code=code, username=f"{code.lower()}user", pin="1234", confirm_pin="1234"), owner
    )


def make_member_topup_promo(main_module, **overrides):
    from server.schemas import PromoCreate, PromoStateTransition

    now = datetime.now(timezone.utc)
    fields = {
        "name": "Stage 4 Member Bonus",
        "audience": "member_topup",
        "amount_mode": "minimum",
        "price": Decimal("100.00"),
        "granted_seconds": 3600,
        "starts_at": now - timedelta(hours=1),
        "ends_at": now + timedelta(days=7),
    }
    fields.update(overrides)
    promo = main_module.session_service.promo_repository.create(PromoCreate(**fields))
    main_module.session_service.promo_repository.transition_state(
        promo.promo_id, PromoStateTransition(owner_state="enabled")
    )
    return promo


# --- 1. Eligibility ---------------------------------------------------


def test_regular_topup_product_is_promo_eligible(temp_directory: str) -> None:
    from server.promo_member_topup_service import is_member_topup_promo_eligible

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "elig-regular.sqlite3")
    product = main_module.member_repository.get_product("member-topup-100")
    assert is_member_topup_promo_eligible(product) is True


def test_vip_product_rejected_from_member_topup_promo(temp_directory: str) -> None:
    from server.promo_member_topup_service import is_member_topup_promo_eligible

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "elig-vip.sqlite3")
    product = main_module.member_repository.get_product("vip-promo-250")
    assert product is not None
    assert product.balance_type == "vip"
    assert is_member_topup_promo_eligible(product) is False


def test_initial_membership_product_rejected(temp_directory: str) -> None:
    from server.promo_member_topup_service import is_member_topup_promo_eligible

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "elig-initial.sqlite3")
    product = main_module.member_repository.get_product("membership-fee")
    assert product is not None
    assert product.product_type == "initial_membership"
    assert is_member_topup_promo_eligible(product) is False


def test_disabled_product_rejected(temp_directory: str) -> None:
    from server.promo_member_topup_service import is_member_topup_promo_eligible
    from server.schemas import MembershipProductUpdate

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "elig-disabled.sqlite3")
    main_module.member_repository.set_product_active("member-topup-100", False)
    product = main_module.member_repository.get_product("member-topup-100")
    assert product is not None
    assert product.is_active is False
    assert is_member_topup_promo_eligible(product) is False


# --- 2. Quote -----------------------------------------------------------


def test_quote_makes_no_writes(temp_directory: str) -> None:
    from server.database import get_connection

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "quote-no-writes.sqlite3")
    member = create_member(main_module, owner)
    make_member_topup_promo(main_module)

    def counts():
        with get_connection() as connection:
            return (
                connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM member_time_transactions").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM promo_redemptions").fetchone()[0],
            )

    before = counts()
    quote = main_module.member_repository.quote_member_topup_promo(member.member_id, "member-topup-100")
    assert quote.bonus_seconds == 3600
    after = counts()
    assert before == after


def test_no_promo_quote_returns_zero_bonus(temp_directory: str) -> None:
    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "quote-no-promo.sqlite3")
    member = create_member(main_module, owner)
    # No promo configured at all.
    quote = main_module.member_repository.quote_member_topup_promo(member.member_id, "member-topup-100")
    assert quote.bonus_seconds == 0
    assert quote.promo_id is None
    assert quote.total_seconds == quote.base_seconds == 8 * 60 * 60


def test_exact_and_minimum_promo_eligibility(temp_directory: str) -> None:
    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "exact-min.sqlite3")
    member = create_member(main_module, owner)
    make_member_topup_promo(main_module, amount_mode="exact", price=Decimal("50.00"), granted_seconds=1800)

    exact_match = main_module.member_repository.quote_member_topup_promo(member.member_id, "member-promo")
    assert exact_match.bonus_seconds == 1800  # P50 product matches exact P50 promo

    minimum_promo = make_member_topup_promo(
        main_module, name="Minimum Promo", amount_mode="minimum", price=Decimal("100.00"), granted_seconds=3600
    )
    above_minimum = main_module.member_repository.quote_member_topup_promo(member.member_id, "member-topup-100")
    assert above_minimum.promo_id == minimum_promo.promo_id
    assert above_minimum.bonus_seconds == 3600


def test_vip_specific_products_unchanged_by_member_topup_promo(temp_directory: str) -> None:
    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "vip-unchanged.sqlite3")
    member = create_member(main_module, owner)
    make_member_topup_promo(main_module, price=Decimal("250.00"), granted_seconds=999999)

    quote = main_module.member_repository.quote_member_topup_promo(member.member_id, "vip-promo-250")
    assert quote.bonus_seconds == 0
    assert quote.promo_id is None
    assert quote.total_seconds == quote.base_seconds == 10 * 60 * 60


# --- 3/4. expected_promo_id + atomic purchase ---------------------------


def test_expected_promo_mismatch_returns_409_no_writes(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRequest
    from server.database import get_connection

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "expected-mismatch.sqlite3")
    member = create_member(main_module, owner)
    make_member_topup_promo(main_module)

    def counts():
        with get_connection() as connection:
            return (
                connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM member_time_transactions").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM promo_redemptions").fetchone()[0],
            )

    before = counts()
    try:
        main_module.member_repository.purchase(
            member.member_id,
            MemberPurchaseRequest(
                product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00"),
                expected_promo_id="not-the-real-promo-id",
            ),
            cashier,
        )
        raise AssertionError("expected 409 for expected_promo_id mismatch")
    except HTTPException as error:
        assert error.status_code == 409
    assert counts() == before


def test_atomic_purchase_credits_base_and_bonus(temp_directory: str) -> None:
    from server.database import get_connection
    from server.schemas import MemberPurchaseRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "atomic-purchase.sqlite3")
    member = create_member(main_module, owner)
    promo = make_member_topup_promo(main_module)

    result = main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(
            product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00"),
            expected_promo_id=promo.promo_id,
        ),
        cashier,
    )
    assert result.base_seconds == 8 * 60 * 60
    assert result.bonus_seconds == 3600
    assert result.total_seconds_credited == 8 * 60 * 60 + 3600
    assert result.applied_promo is not None
    assert result.applied_promo.promo_id == promo.promo_id

    updated_member = main_module.member_repository.get_member(member.member_id)
    assert updated_member.time_balance_seconds == 8 * 60 * 60 + 3600

    with get_connection() as connection:
        ledger_rows = connection.execute(
            "SELECT * FROM member_time_transactions WHERE related_payment_id = ? ORDER BY created_at ASC",
            (result.payment.payment_id,),
        ).fetchall()
        redemption_row = connection.execute(
            "SELECT * FROM promo_redemptions WHERE payment_id = ?", (result.payment.payment_id,)
        ).fetchone()
    assert len(ledger_rows) == 2
    assert ledger_rows[0]["seconds_delta"] == 8 * 60 * 60
    assert ledger_rows[1]["seconds_delta"] == 3600
    assert ledger_rows[0]["promo_redemption_id"] == redemption_row["redemption_id"]
    assert ledger_rows[1]["promo_redemption_id"] == redemption_row["redemption_id"]
    assert redemption_row["base_transaction_id"] == ledger_rows[0]["transaction_id"]
    assert redemption_row["bonus_transaction_id"] == ledger_rows[1]["transaction_id"]
    assert redemption_row["member_id"] == member.member_id


def test_vip_2to1_conversion_only_applies_during_gameplay(temp_directory: str) -> None:
    """A promotional member top-up credits ordinary Standard balance only
    -- no VIP 2:1 arithmetic happens at purchase time. Verified by
    confirming the credited amount is the raw base+bonus seconds, not
    halved/doubled, and that vip_balance_seconds is untouched."""
    from server.schemas import MemberPurchaseRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "no-vip-arith.sqlite3")
    member = create_member(main_module, owner)
    make_member_topup_promo(main_module)

    main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00")),
        cashier,
    )
    updated_member = main_module.member_repository.get_member(member.member_id)
    assert updated_member.time_balance_seconds == 8 * 60 * 60 + 3600
    assert updated_member.vip_balance_seconds == 0


def test_per_member_and_total_limits_enforced(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "limits.sqlite3")
    member_a = create_member(main_module, owner, "MA")
    member_b = create_member(main_module, owner, "MB")
    promo = make_member_topup_promo(main_module, per_member_redemption_limit=1, total_redemption_limit=1)

    first = main_module.member_repository.purchase(
        member_a.member_id,
        MemberPurchaseRequest(product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00")),
        cashier,
    )
    assert first.bonus_seconds == 3600

    # Total limit (1) reached: member_b gets no bonus, not an error.
    second = main_module.member_repository.purchase(
        member_b.member_id,
        MemberPurchaseRequest(product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00")),
        cashier,
    )
    assert second.bonus_seconds == 0
    assert second.applied_promo is None


def test_voided_redemption_frees_limit_slot(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRequest, MemberPurchaseRefundRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "voided-frees-slot.sqlite3")
    member_a = create_member(main_module, owner, "MA")
    member_b = create_member(main_module, owner, "MB")
    make_member_topup_promo(main_module, total_redemption_limit=1)

    first = main_module.member_repository.purchase(
        member_a.member_id,
        MemberPurchaseRequest(product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00")),
        cashier,
    )
    assert first.bonus_seconds == 3600

    main_module.member_repository.refund_purchase(
        first.payment.payment_id, MemberPurchaseRefundRequest(reason="Other", notes="free the slot"), admin
    )

    second = main_module.member_repository.purchase(
        member_b.member_id,
        MemberPurchaseRequest(product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00")),
        cashier,
    )
    assert second.bonus_seconds == 3600


def test_idempotent_retry_does_not_duplicate_anything(temp_directory: str) -> None:
    from server.database import get_connection
    from server.schemas import MemberPurchaseRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "idempotent.sqlite3")
    member = create_member(main_module, owner)
    make_member_topup_promo(main_module)
    key = "stage4-idempotency-key-001"

    first = main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(
            product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00"), idempotency_key=key
        ),
        cashier,
    )
    second = main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(
            product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00"), idempotency_key=key
        ),
        cashier,
    )
    assert second.payment.payment_id == first.payment.payment_id
    assert second.bonus_seconds == first.bonus_seconds == 3600

    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM member_time_transactions WHERE member_id = ?", (member.member_id,)).fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM promo_redemptions").fetchone()[0] == 1
    updated_member = main_module.member_repository.get_member(member.member_id)
    assert updated_member.time_balance_seconds == 8 * 60 * 60 + 3600  # not doubled


def test_failed_purchase_rolls_back_everything(temp_directory: str) -> None:
    """A promo that becomes disabled between the advisory quote and the
    final atomic recheck must roll back cleanly -- but the purchase should
    still succeed at the base (non-promo) amount, exactly like Stage 3's
    equivalent standard/vip behavior: a promo becoming unavailable never
    blocks the underlying transaction, it just falls back to ordinary
    credit. This proves the atomic claim's internal recheck rejects
    cleanly with no partial base/bonus/redemption state."""
    from server.database import get_connection
    from server.promo_member_topup_service import claim_member_topup_promo
    from server.schemas import PromoStateTransition

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "rollback.sqlite3")
    member = create_member(main_module, owner)
    promo = make_member_topup_promo(main_module)
    product = main_module.member_repository.get_product("member-topup-100")

    # Disable the promo, then attempt an atomic claim directly against the
    # (now stale) promo_id -- the internal recheck must reject with 409
    # and leave no partial writes.
    main_module.session_service.promo_repository.transition_state(
        promo.promo_id, PromoStateTransition(owner_state="disabled")
    )
    from server.income_attribution import attribution_pending_unknown_pc
    from server.schemas import PaymentRecord
    from server.time_utils import current_utc_time
    from uuid import uuid4

    now = current_utc_time()
    attribution = attribution_pending_unknown_pc()
    payment = PaymentRecord(
        payment_id=str(uuid4()), session_id=None, amount=product.price, payment_method="cash",
        cash_received=product.price, change_given=Decimal("0.00"), action_type="manual",
        related_command_id=None, review_required=False, shift_id=None, recorded_by_user_id=cashier.user_id,
        status="completed", created_at=now, pc_name_snapshot=attribution.pc_name_snapshot,
        income_group_id_snapshot=attribution.income_group_id_snapshot,
        income_group_name_snapshot=attribution.income_group_name_snapshot,
        attribution_status=attribution.status, attribution_assigned_at=attribution.assigned_at,
        attribution_session_id=attribution.session_id,
    )
    try:
        claim_member_topup_promo(
            member_repository=main_module.member_repository,
            payment_repository=main_module.member_repository.payment_repository,
            promo_repository=main_module.member_repository.promo_repository,
            member_id=member.member_id,
            product=product,
            promo_id=promo.promo_id,
            payment=payment,
            actor=cashier,
            attribution=attribution,
            now=now,
        )
        raise AssertionError("expected 409 for a disabled promo")
    except HTTPException as error:
        assert error.status_code == 409

    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM member_time_transactions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM promo_redemptions").fetchone()[0] == 0
    assert main_module.member_repository.get_member(member.member_id).time_balance_seconds == 0


# --- 8. Refund ------------------------------------------------------------


def test_refund_reverses_base_and_bonus_atomically(temp_directory: str) -> None:
    from server.database import get_connection
    from server.schemas import MemberPurchaseRequest, MemberPurchaseRefundRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "refund-atomic.sqlite3")
    member = create_member(main_module, owner)
    make_member_topup_promo(main_module)

    purchase = main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00")),
        cashier,
    )
    assert main_module.member_repository.get_member(member.member_id).time_balance_seconds == 8 * 60 * 60 + 3600

    refund = main_module.member_repository.refund_purchase(
        purchase.payment.payment_id, MemberPurchaseRefundRequest(reason="Other", notes="test refund"), admin
    )
    assert refund.base_seconds == 8 * 60 * 60
    assert refund.bonus_seconds == 3600
    assert refund.total_seconds_reversed == 8 * 60 * 60 + 3600
    assert main_module.member_repository.get_member(member.member_id).time_balance_seconds == 0

    with get_connection() as connection:
        payment_row = connection.execute(
            "SELECT status FROM payments WHERE payment_id = ?", (purchase.payment.payment_id,)
        ).fetchone()
        redemption_row = connection.execute(
            "SELECT * FROM promo_redemptions WHERE payment_id = ?", (purchase.payment.payment_id,)
        ).fetchone()
    assert payment_row["status"] == "voided"
    assert redemption_row["voided_at"] is not None
    # Snapshot preserved.
    assert redemption_row["granted_seconds_snapshot"] == 3600
    assert redemption_row["promo_name_snapshot"] == "Stage 4 Member Bonus"


def test_insufficient_remaining_balance_blocks_refund(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRequest, MemberPurchaseRefundRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "insufficient-balance.sqlite3")
    member = create_member(main_module, owner)
    make_member_topup_promo(main_module)

    purchase = main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00")),
        cashier,
    )
    # Spend most of the balance so base+bonus can no longer be fully reversed.
    main_module.member_repository.apply_balance_delta(
        member.member_id, "session_usage_finalized", -(8 * 60 * 60 + 3600 - 60), cashier.user_id
    )
    try:
        main_module.member_repository.refund_purchase(
            purchase.payment.payment_id, MemberPurchaseRefundRequest(reason="Other", notes="test"), admin
        )
        raise AssertionError("expected 409 for insufficient remaining balance")
    except HTTPException as error:
        assert error.status_code == 409

    from server.database import get_connection

    with get_connection() as connection:
        payment_row = connection.execute(
            "SELECT status FROM payments WHERE payment_id = ?", (purchase.payment.payment_id,)
        ).fetchone()
        redemption_row = connection.execute(
            "SELECT voided_at FROM promo_redemptions WHERE payment_id = ?", (purchase.payment.payment_id,)
        ).fetchone()
    assert payment_row["status"] == "completed"
    assert redemption_row["voided_at"] is None


def test_active_reservation_blocks_refund(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRequest, MemberPurchaseRefundRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "active-reservation.sqlite3")
    member = create_member(main_module, owner)
    make_member_topup_promo(main_module)

    purchase = main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00")),
        cashier,
    )
    updated_member = main_module.member_repository.get_member(member.member_id)
    main_module.member_repository.create_reservation(updated_member, "fake-session-1", 60, cashier.user_id)

    try:
        main_module.member_repository.refund_purchase(
            purchase.payment.payment_id, MemberPurchaseRefundRequest(reason="Other", notes="test"), admin
        )
        raise AssertionError("expected 409 while member has an active reservation")
    except HTTPException as error:
        assert error.status_code == 409


def test_failed_refund_leaves_everything_unchanged(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRequest, MemberPurchaseRefundRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "failed-refund-noop.sqlite3")
    member = create_member(main_module, owner)
    make_member_topup_promo(main_module)

    purchase = main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00")),
        cashier,
    )
    updated_member = main_module.member_repository.get_member(member.member_id)
    main_module.member_repository.create_reservation(updated_member, "fake-session-2", 60, cashier.user_id)

    from server.database import get_connection

    with get_connection() as connection:
        payment_before = dict(
            connection.execute("SELECT * FROM payments WHERE payment_id = ?", (purchase.payment.payment_id,)).fetchone()
        )
        redemption_before = dict(
            connection.execute("SELECT * FROM promo_redemptions WHERE payment_id = ?", (purchase.payment.payment_id,)).fetchone()
        )
        ledger_before = [
            dict(row) for row in connection.execute(
                "SELECT * FROM member_time_transactions WHERE member_id = ?", (member.member_id,)
            ).fetchall()
        ]

    try:
        main_module.member_repository.refund_purchase(
            purchase.payment.payment_id, MemberPurchaseRefundRequest(reason="Other", notes="test"), admin
        )
        raise AssertionError("expected 409 blocked by active reservation")
    except HTTPException:
        pass

    with get_connection() as connection:
        payment_after = dict(
            connection.execute("SELECT * FROM payments WHERE payment_id = ?", (purchase.payment.payment_id,)).fetchone()
        )
        redemption_after = dict(
            connection.execute("SELECT * FROM promo_redemptions WHERE payment_id = ?", (purchase.payment.payment_id,)).fetchone()
        )
        ledger_after = [
            dict(row) for row in connection.execute(
                "SELECT * FROM member_time_transactions WHERE member_id = ?", (member.member_id,)
            ).fetchall()
        ]
    assert payment_before == payment_after
    assert redemption_before == redemption_after
    assert ledger_before == ledger_after


def test_generic_payment_void_cannot_bypass_member_refund_logic(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "generic-void-blocked.sqlite3")
    member = create_member(main_module, owner)
    make_member_topup_promo(main_module)

    purchase = main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00")),
        cashier,
    )
    try:
        main_module.billing_service.void_payment(purchase.payment.payment_id, "trying generic void", admin.user_id)
        raise AssertionError("the generic session-payment void must reject a membership purchase payment")
    except HTTPException as error:
        assert error.status_code == 400

    from server.database import get_connection

    with get_connection() as connection:
        payment_row = connection.execute(
            "SELECT status FROM payments WHERE payment_id = ?", (purchase.payment.payment_id,)
        ).fetchone()
        redemption_row = connection.execute(
            "SELECT voided_at FROM promo_redemptions WHERE payment_id = ?", (purchase.payment.payment_id,)
        ).fetchone()
    assert payment_row["status"] == "completed"
    assert redemption_row["voided_at"] is None
    assert main_module.member_repository.get_member(member.member_id).time_balance_seconds == 8 * 60 * 60 + 3600


# --- 9. Attribution and operator behavior ---------------------------------


def test_non_default_income_attribution_and_different_refunding_operator(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRequest, MemberPurchaseRefundRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "attribution.sqlite3")
    member = create_member(main_module, owner)
    make_member_topup_promo(main_module)

    purchase = main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00")),
        cashier,
    )
    # No active session -> attribution is pending, not "tyrone" (member
    # purchases only attribute immediately when the member already has an
    # active session on a station that can use the balance type -- this
    # confirms existing behavior is untouched, not overridden by the promo).
    assert purchase.payment.attribution_status == "pending"

    refund = main_module.member_repository.refund_purchase(
        purchase.payment.payment_id, MemberPurchaseRefundRequest(reason="Other", notes="different operator"), owner
    )
    assert refund.payment.voided_by_user_id == owner.user_id
    assert refund.payment.voided_by_user_id != cashier.user_id


def test_operator_activity_and_shift_attribution_for_promo_purchase(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRequest, ShiftOpenRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "operator-shift.sqlite3")
    member = create_member(main_module, owner)
    make_member_topup_promo(main_module)
    main_module.open_shift(ShiftOpenRequest(opening_cash=Decimal("0.00")), cashier)

    purchase = main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00")),
        cashier,
    )
    assert purchase.payment.recorded_by_user_id == cashier.user_id
    assert purchase.payment.shift_id is not None

    activity = main_module.list_operator_activity("all", None, "payment", 50, owner)
    matching = [item for item in activity if item.amount == Decimal("100.00") and item.operator_id == cashier.user_id]
    assert len(matching) >= 1


# --- Non-promo compatibility ----------------------------------------------


def test_non_promo_member_purchases_unchanged(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "non-promo-baseline.sqlite3")
    member = create_member(main_module, owner)
    # No promo configured at all.
    result = main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00")),
        cashier,
    )
    assert result.bonus_seconds == 0
    assert result.applied_promo is None
    assert result.base_seconds == 8 * 60 * 60
    assert result.total_seconds_credited == 8 * 60 * 60
    updated_member = main_module.member_repository.get_member(member.member_id)
    assert updated_member.time_balance_seconds == 8 * 60 * 60


def test_initial_membership_purchase_unaffected(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "initial-membership.sqlite3")
    member = create_member(main_module, owner)
    make_member_topup_promo(main_module)  # promo exists but must never touch initial_membership

    result = main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(product_id="membership-fee", payment_method="cash", cash_received=Decimal("100.00")),
        cashier,
    )
    assert result.bonus_seconds == 0
    assert result.applied_promo is None
    assert result.transaction.transaction_type == "membership_purchase"


# --- Determinism fix: base/bonus must never be inferred from ordering -----


def construct_tied_promo_purchase(
    main_module,
    member,
    promo,
    *,
    base_seconds: int,
    bonus_seconds: int,
    base_transaction_id: str,
    bonus_transaction_id: str,
    idempotency_key: str | None = None,
):
    """Directly constructs a promo purchase's rows with IDENTICAL
    created_at timestamps and DELIBERATELY reversed lexical transaction
    ids (base_transaction_id sorts AFTER bonus_transaction_id) -- the
    exact condition that would make the old ORDER BY created_at [, id]
    ASC LIMIT 1 heuristic pick the wrong row. Bypasses
    claim_member_topup_promo entirely so the test controls ids/timestamps
    precisely; everything it writes (payment, both ledger rows, the
    member balance, the redemption row) mirrors exactly what that
    function would have produced."""
    from server.database import get_connection
    from server.income_attribution import attribution_pending_unknown_pc
    from server.schemas import PaymentRecord
    from server.time_utils import current_utc_time
    from uuid import uuid4

    assert base_transaction_id > bonus_transaction_id, "test setup must keep base lexically AFTER bonus"

    now = current_utc_time()
    tied_created_at = now.isoformat()
    attribution = attribution_pending_unknown_pc()
    payment = PaymentRecord(
        payment_id=str(uuid4()), session_id=None, amount=promo.price, payment_method="cash",
        cash_received=promo.price, change_given=Decimal("0.00"), action_type="manual",
        related_command_id=None, review_required=False, shift_id=None, recorded_by_user_id=None,
        idempotency_key=idempotency_key, status="completed", created_at=now,
        pc_name_snapshot=attribution.pc_name_snapshot,
        income_group_id_snapshot=attribution.income_group_id_snapshot,
        income_group_name_snapshot=attribution.income_group_name_snapshot,
        attribution_status=attribution.status, attribution_assigned_at=attribution.assigned_at,
        attribution_session_id=attribution.session_id,
    )
    redemption_id = str(uuid4())
    with get_connection() as connection:
        main_module.member_repository.payment_repository.create_payment(payment, connection=connection)
        before = member.time_balance_seconds
        connection.execute(
            """
            INSERT INTO member_time_transactions (
                transaction_id, member_id, transaction_type, balance_type, seconds_delta,
                balance_before_seconds, balance_after_seconds, amount_paid, related_session_id,
                related_payment_id, notes, performed_by_user_id, created_at, promo_redemption_id
            ) VALUES (?, ?, 'promo_topup', 'standard', ?, ?, ?, ?, NULL, ?, ?, NULL, ?, ?)
            """,
            (
                base_transaction_id, member.member_id, base_seconds, before, before + base_seconds,
                str(promo.price), payment.payment_id, "base", tied_created_at, redemption_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO member_time_transactions (
                transaction_id, member_id, transaction_type, balance_type, seconds_delta,
                balance_before_seconds, balance_after_seconds, amount_paid, related_session_id,
                related_payment_id, notes, performed_by_user_id, created_at, promo_redemption_id
            ) VALUES (?, ?, 'promo_topup', 'standard', ?, ?, ?, '0.00', NULL, ?, ?, NULL, ?, ?)
            """,
            (
                bonus_transaction_id, member.member_id, bonus_seconds, before + base_seconds,
                before + base_seconds + bonus_seconds, payment.payment_id, "bonus", tied_created_at, redemption_id,
            ),
        )
        connection.execute(
            "UPDATE members SET time_balance_seconds = ?, updated_at = ? WHERE member_id = ?",
            (before + base_seconds + bonus_seconds, tied_created_at, member.member_id),
        )
        connection.execute(
            """
            INSERT INTO promo_redemptions (
                redemption_id, promo_id, promo_name_snapshot, audience_snapshot, amount_mode_snapshot,
                price_snapshot, granted_seconds_snapshot, station_scope_snapshot, applies_to_snapshot,
                starts_at_snapshot, ends_at_snapshot, member_id, session_id, payment_id,
                base_transaction_id, bonus_transaction_id, pc_name_snapshot, income_group_id_snapshot,
                income_group_name_snapshot, operator_user_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, NULL, NULL, ?)
            """,
            (
                redemption_id, promo.promo_id, promo.name, promo.audience, promo.amount_mode,
                str(promo.price), bonus_seconds, promo.starts_at.isoformat(), promo.ends_at.isoformat(),
                member.member_id, payment.payment_id, base_transaction_id, bonus_transaction_id, tied_created_at,
            ),
        )
    return payment, redemption_id


def test_tied_timestamps_reversed_ids_resolve_base_and_bonus_correctly(temp_directory: str) -> None:
    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "tied-resolve.sqlite3")
    member = create_member(main_module, owner)
    promo = make_member_topup_promo(main_module)

    payment, redemption_id = construct_tied_promo_purchase(
        main_module, member, promo,
        base_seconds=8 * 60 * 60, bonus_seconds=3600,
        base_transaction_id="zzz-base-txn", bonus_transaction_id="aaa-bonus-txn",
    )

    base_transaction, bonus_transaction = main_module.member_repository.resolve_purchase_transactions(
        payment.payment_id
    )
    assert base_transaction.transaction_id == "zzz-base-txn"
    assert base_transaction.seconds_delta == 8 * 60 * 60
    assert bonus_transaction.transaction_id == "aaa-bonus-txn"
    assert bonus_transaction.seconds_delta == 3600


def test_tied_timestamps_refund_reverses_correct_combined_amount(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRefundRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "tied-refund.sqlite3")
    member = create_member(main_module, owner)
    promo = make_member_topup_promo(main_module)

    payment, redemption_id = construct_tied_promo_purchase(
        main_module, member, promo,
        base_seconds=8 * 60 * 60, bonus_seconds=3600,
        base_transaction_id="zzz-base-refund", bonus_transaction_id="aaa-bonus-refund",
    )
    assert main_module.member_repository.get_member(member.member_id).time_balance_seconds == 8 * 60 * 60 + 3600

    refund = main_module.member_repository.refund_purchase(
        payment.payment_id, MemberPurchaseRefundRequest(reason="Other", notes="tied timestamps"), admin
    )
    assert refund.base_seconds == 8 * 60 * 60
    assert refund.bonus_seconds == 3600
    assert refund.total_seconds_reversed == 8 * 60 * 60 + 3600
    assert main_module.member_repository.get_member(member.member_id).time_balance_seconds == 0


def test_tied_timestamps_idempotent_retry_returns_correct_values(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "tied-idempotent.sqlite3")
    member = create_member(main_module, owner)
    promo = make_member_topup_promo(main_module)
    key = "tied-idempotency-key"

    payment, redemption_id = construct_tied_promo_purchase(
        main_module, member, promo,
        base_seconds=8 * 60 * 60, bonus_seconds=3600,
        base_transaction_id="zzz-base-idem", bonus_transaction_id="aaa-bonus-idem",
        idempotency_key=key,
    )

    retry = main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(
            product_id="member-topup-100", payment_method="cash", cash_received=Decimal("100.00"),
            idempotency_key=key,
        ),
        cashier,
    )
    assert retry.payment.payment_id == payment.payment_id
    assert retry.base_seconds == 8 * 60 * 60
    assert retry.bonus_seconds == 3600
    assert retry.total_seconds_credited == 8 * 60 * 60 + 3600

    from server.database import get_connection

    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM member_time_transactions WHERE member_id = ?", (member.member_id,)
        ).fetchone()[0] == 2
    assert main_module.member_repository.get_member(member.member_id).time_balance_seconds == 8 * 60 * 60 + 3600


def test_corrupted_linkage_produces_no_partial_refund(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRefundRequest
    from server.database import get_connection

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "corrupted-linkage.sqlite3")
    member = create_member(main_module, owner)
    promo = make_member_topup_promo(main_module)

    payment, redemption_id = construct_tied_promo_purchase(
        main_module, member, promo,
        base_seconds=8 * 60 * 60, bonus_seconds=3600,
        base_transaction_id="zzz-base-corrupt", bonus_transaction_id="aaa-bonus-corrupt",
    )
    # Corrupt the linkage: point bonus_transaction_id at a transaction id
    # that does not exist at all.
    with get_connection() as connection:
        connection.execute(
            "UPDATE promo_redemptions SET bonus_transaction_id = 'does-not-exist' WHERE redemption_id = ?",
            (redemption_id,),
        )
        payment_before = dict(
            connection.execute("SELECT * FROM payments WHERE payment_id = ?", (payment.payment_id,)).fetchone()
        )
        member_balance_before = main_module.member_repository.get_member(member.member_id).time_balance_seconds

    try:
        main_module.member_repository.refund_purchase(
            payment.payment_id, MemberPurchaseRefundRequest(reason="Other", notes="corrupted"), admin
        )
        raise AssertionError("expected a 500 for corrupted promo redemption linkage")
    except HTTPException as error:
        assert error.status_code == 500

    with get_connection() as connection:
        payment_after = dict(
            connection.execute("SELECT * FROM payments WHERE payment_id = ?", (payment.payment_id,)).fetchone()
        )
    assert payment_before == payment_after
    assert payment_after["status"] == "completed"
    assert main_module.member_repository.get_member(member.member_id).time_balance_seconds == member_balance_before


def test_corrupted_linkage_wrong_member_produces_no_partial_refund(temp_directory: str) -> None:
    """Corruption where base/bonus resolve to real rows, but one of them
    belongs to a DIFFERENT member than the redemption claims -- must also
    fail safely before any mutation, not just the missing-row case."""
    from server.schemas import MemberPurchaseRefundRequest
    from server.database import get_connection

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "corrupted-wrong-member.sqlite3")
    member_a = create_member(main_module, owner, "MA")
    member_b = create_member(main_module, owner, "MB")
    promo = make_member_topup_promo(main_module)

    payment, redemption_id = construct_tied_promo_purchase(
        main_module, member_a, promo,
        base_seconds=8 * 60 * 60, bonus_seconds=3600,
        base_transaction_id="zzz-base-wrongmember", bonus_transaction_id="aaa-bonus-wrongmember",
    )
    with get_connection() as connection:
        connection.execute(
            "UPDATE member_time_transactions SET member_id = ? WHERE transaction_id = ?",
            (member_b.member_id, "aaa-bonus-wrongmember"),
        )
        payment_before = dict(
            connection.execute("SELECT * FROM payments WHERE payment_id = ?", (payment.payment_id,)).fetchone()
        )

    try:
        main_module.member_repository.refund_purchase(
            payment.payment_id, MemberPurchaseRefundRequest(reason="Other", notes="corrupted"), admin
        )
        raise AssertionError("expected a 500 for a bonus row belonging to the wrong member")
    except HTTPException as error:
        assert error.status_code == 500

    with get_connection() as connection:
        payment_after = dict(
            connection.execute("SELECT * FROM payments WHERE payment_id = ?", (payment.payment_id,)).fetchone()
        )
    assert payment_before == payment_after
    assert payment_after["status"] == "completed"
    assert main_module.member_repository.get_member(member_a.member_id).time_balance_seconds == 8 * 60 * 60 + 3600


def main() -> None:
    tests = [
        test_regular_topup_product_is_promo_eligible,
        test_vip_product_rejected_from_member_topup_promo,
        test_initial_membership_product_rejected,
        test_disabled_product_rejected,
        test_quote_makes_no_writes,
        test_no_promo_quote_returns_zero_bonus,
        test_exact_and_minimum_promo_eligibility,
        test_vip_specific_products_unchanged_by_member_topup_promo,
        test_expected_promo_mismatch_returns_409_no_writes,
        test_atomic_purchase_credits_base_and_bonus,
        test_vip_2to1_conversion_only_applies_during_gameplay,
        test_per_member_and_total_limits_enforced,
        test_voided_redemption_frees_limit_slot,
        test_idempotent_retry_does_not_duplicate_anything,
        test_failed_purchase_rolls_back_everything,
        test_refund_reverses_base_and_bonus_atomically,
        test_insufficient_remaining_balance_blocks_refund,
        test_active_reservation_blocks_refund,
        test_failed_refund_leaves_everything_unchanged,
        test_generic_payment_void_cannot_bypass_member_refund_logic,
        test_non_default_income_attribution_and_different_refunding_operator,
        test_operator_activity_and_shift_attribution_for_promo_purchase,
        test_non_promo_member_purchases_unchanged,
        test_initial_membership_purchase_unaffected,
        test_tied_timestamps_reversed_ids_resolve_base_and_bonus_correctly,
        test_tied_timestamps_refund_reverses_correct_combined_amount,
        test_tied_timestamps_idempotent_retry_returns_correct_values,
        test_corrupted_linkage_produces_no_partial_refund,
        test_corrupted_linkage_wrong_member_produces_no_partial_refund,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
        print(f"{test.__name__} passed.")
    print("Promo Maker Stage 4 (Member Top-up) smoke tests passed.")


if __name__ == "__main__":
    main()

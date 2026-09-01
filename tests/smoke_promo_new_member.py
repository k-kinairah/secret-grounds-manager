"""Stage 5 smoke tests: New Member promotional account-creation offers.

NOTE: account creation now always charges the flat Membership Fee
(server/member_repository.py::_create_member_paid); New Member promos no
longer run at signup. The service + quote logic
(server/promo_new_member_service.py) is kept dormant, so only the
eligibility / quote / validation tests below still run. The
create_member-integration tests (atomic claim, idempotency, total-limit,
refund, attribution, rollback) remain defined but are omitted from the
run list -- rewire them if the New Member promo entry point is ever
restored, or delete this file when the feature is fully removed.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def create_env(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    import server.main as main_module
    from server.schemas import UserCreateRequest
    from server.user_repository import UserRepository

    user_repository = UserRepository()
    owner = user_repository.create_user(
        UserCreateRequest(username="owner5", display_name="Owner 5", password="password1", role="owner")
    )
    admin = user_repository.create_user(
        UserCreateRequest(username="admin5", display_name="Admin 5", password="password2", role="admin")
    )
    cashier = user_repository.create_user(
        UserCreateRequest(username="cashier5", display_name="Cashier 5", password="password3", role="cashier")
    )
    return main_module, owner, admin, cashier


def make_new_member_promo(main_module, **overrides):
    from server.schemas import PromoCreate, PromoStateTransition

    now = datetime.now(timezone.utc)
    fields = {
        "name": "Stage 5 New Member Bonus",
        "audience": "new_member",
        "amount_mode": "exact",
        "price": Decimal("100.00"),
        "granted_seconds": 25200,  # 7 hours vs the P100=6hr permanent product
        "starts_at": now - timedelta(hours=1),
        "ends_at": now + timedelta(days=7),
    }
    fields.update(overrides)
    promo = main_module.session_service.promo_repository.create(PromoCreate(**fields))
    main_module.session_service.promo_repository.transition_state(
        promo.promo_id, PromoStateTransition(owner_state="enabled")
    )
    return promo


def next_username(counter=[0]):
    counter[0] += 1
    return f"newmember{counter[0]}"


# --- 1. Eligibility -----------------------------------------------------


def test_minimum_amount_mode_rejected_for_new_member(temp_directory: str) -> None:
    from server.schemas import PromoCreate

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "minimum-rejected.sqlite3")
    now = datetime.now(timezone.utc)
    try:
        PromoCreate(
            name="Bad", audience="new_member", amount_mode="minimum", price=Decimal("100.00"),
            granted_seconds=3600, starts_at=now - timedelta(hours=1), ends_at=now + timedelta(days=1),
        )
        raise AssertionError("amount_mode='minimum' must be rejected for new_member")
    except ValidationError:
        pass


def test_per_member_limit_rejected_for_new_member(temp_directory: str) -> None:
    from server.schemas import PromoCreate

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "per-member-rejected.sqlite3")
    now = datetime.now(timezone.utc)
    try:
        PromoCreate(
            name="Bad", audience="new_member", amount_mode="exact", price=Decimal("100.00"),
            granted_seconds=3600, starts_at=now - timedelta(hours=1), ends_at=now + timedelta(days=1),
            per_member_redemption_limit=1,
        )
        raise AssertionError("per_member_redemption_limit must be rejected for new_member")
    except ValidationError:
        pass


def test_exact_amount_eligibility(temp_directory: str) -> None:
    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "exact-eligibility.sqlite3")
    promo = make_new_member_promo(main_module)

    matching = main_module.member_repository.quote_new_member_promo(Decimal("100.00"))
    assert matching.promo_id == promo.promo_id
    assert matching.source_type == "promo"

    non_matching = main_module.member_repository.quote_new_member_promo(Decimal("99.00"))
    assert non_matching.promo_id is None


def test_active_scheduled_expired_disabled_behavior(temp_directory: str) -> None:
    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "status-derivation.sqlite3")
    now = datetime.now(timezone.utc)

    scheduled = make_new_member_promo(
        main_module, name="Scheduled", starts_at=now + timedelta(days=1), ends_at=now + timedelta(days=8)
    )
    quote = main_module.member_repository.quote_new_member_promo(Decimal("100.00"))
    assert quote.promo_id is None, "a Scheduled (future starts_at) promo must not be selected"

    expired = make_new_member_promo(
        main_module, name="Expired", starts_at=now - timedelta(days=8), ends_at=now - timedelta(days=1)
    )
    quote = main_module.member_repository.quote_new_member_promo(Decimal("100.00"))
    assert quote.promo_id is None, "an Ended (past ends_at) promo must not be selected"

    from server.schemas import PromoStateTransition

    active = make_new_member_promo(main_module, name="Now Active")
    quote = main_module.member_repository.quote_new_member_promo(Decimal("100.00"))
    assert quote.promo_id == active.promo_id

    main_module.session_service.promo_repository.transition_state(
        active.promo_id, PromoStateTransition(owner_state="disabled")
    )
    quote = main_module.member_repository.quote_new_member_promo(Decimal("100.00"))
    assert quote.promo_id is None, "a Disabled promo must not be selected"


# --- 2. Baseline comparison ----------------------------------------------


def test_promo_better_than_same_price_product_selected(temp_directory: str) -> None:
    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "better-than-product.sqlite3")
    promo = make_new_member_promo(main_module, price=Decimal("100.00"), granted_seconds=25200)  # 7hr vs 6hr normal

    quote = main_module.member_repository.quote_new_member_promo(Decimal("100.00"))
    assert quote.source_type == "promo"
    assert quote.promo_id == promo.promo_id
    assert quote.normal_product_id == "membership-fee"
    assert quote.normal_seconds == 6 * 60 * 60
    assert quote.final_seconds == 25200
    assert quote.benefit_seconds == 25200 - 6 * 60 * 60


def test_equal_or_worse_promo_falls_back_to_product(temp_directory: str) -> None:
    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "worse-falls-back.sqlite3")
    make_new_member_promo(main_module, price=Decimal("100.00"), granted_seconds=6 * 60 * 60)  # equal to normal

    quote = main_module.member_repository.quote_new_member_promo(Decimal("100.00"))
    assert quote.source_type == "normal_product"
    assert quote.promo_id is None
    assert quote.final_seconds == 6 * 60 * 60
    assert quote.benefit_seconds == 0


def test_different_price_promo_works_as_temporary_offer(temp_directory: str) -> None:
    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "temporary-offer.sqlite3")
    promo = make_new_member_promo(main_module, price=Decimal("75.00"), granted_seconds=14400)

    quote = main_module.member_repository.quote_new_member_promo(Decimal("75.00"))
    assert quote.source_type == "promo"
    assert quote.promo_id == promo.promo_id
    assert quote.normal_product_id is None  # no permanent product at P75
    assert quote.normal_seconds is None
    assert quote.final_seconds == 14400
    assert quote.benefit_seconds == 14400  # any positive match wins with no baseline

    # No permanent membership_products row was created for this temporary price.
    products = main_module.member_repository.list_products(include_inactive=True)
    assert not any(p.price == Decimal("75.00") for p in products)


# --- 3. Quote -------------------------------------------------------------


def test_quote_makes_zero_writes(temp_directory: str) -> None:
    from server.database import get_connection

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "quote-no-writes.sqlite3")
    make_new_member_promo(main_module)

    def counts():
        with get_connection() as connection:
            return (
                connection.execute("SELECT COUNT(*) FROM members").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM member_time_transactions").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM promo_redemptions").fetchone()[0],
            )

    before = counts()
    main_module.member_repository.quote_new_member_promo(Decimal("100.00"))
    main_module.member_repository.quote_new_member_promo(Decimal("999.00"))
    assert counts() == before


def test_invalid_amount_handled_clearly(temp_directory: str) -> None:
    from server.schemas import NewMemberPromoQuoteRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "invalid-amount.sqlite3")
    try:
        NewMemberPromoQuoteRequest(amount=Decimal("-5.00"))
        raise AssertionError("a negative amount must be rejected")
    except ValidationError:
        pass
    try:
        NewMemberPromoQuoteRequest(amount=Decimal("0.00"))
        raise AssertionError("a zero amount must be rejected")
    except ValidationError:
        pass


# --- 4/5. expected_promo_id + atomic creation ------------------------------


def test_expected_promo_mismatch_returns_409_no_writes(temp_directory: str) -> None:
    from server.database import get_connection
    from server.schemas import MemberCreateRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "expected-mismatch.sqlite3")
    make_new_member_promo(main_module)

    def counts():
        with get_connection() as connection:
            return (
                connection.execute("SELECT COUNT(*) FROM members").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
            )

    before = counts()
    try:
        main_module.member_repository.create_member(
            MemberCreateRequest(
                username=next_username(), pin="1234", confirm_pin="1234",
                amount=Decimal("100.00"), payment_method="cash", cash_received=Decimal("100.00"),
                expected_promo_id="not-the-real-promo-id",
            ),
            cashier,
        )
        raise AssertionError("expected 409 for expected_promo_id mismatch")
    except HTTPException as error:
        assert error.status_code == 409
    assert counts() == before


def test_atomic_account_payment_balance_redemption_creation(temp_directory: str) -> None:
    from server.database import get_connection
    from server.schemas import MemberCreateRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "atomic-creation.sqlite3")
    promo = make_new_member_promo(main_module)
    username = next_username()

    member = main_module.member_repository.create_member(
        MemberCreateRequest(
            username=username, pin="1234", confirm_pin="1234",
            amount=Decimal("100.00"), payment_method="cash", cash_received=Decimal("100.00"),
            expected_promo_id=promo.promo_id,
        ),
        cashier,
    )
    assert member.time_balance_seconds == 25200
    assert member.vip_balance_seconds == 0
    assert member.creation_base_seconds == 25200
    assert member.creation_benefit_seconds == 25200 - 6 * 60 * 60
    assert member.creation_applied_promo is not None
    assert member.creation_applied_promo.promo_id == promo.promo_id
    assert member.creation_payment is not None
    assert member.creation_payment.amount == Decimal("100.00")

    with get_connection() as connection:
        member_row = connection.execute("SELECT * FROM members WHERE member_id = ?", (member.member_id,)).fetchone()
        payment_row = connection.execute(
            "SELECT * FROM payments WHERE payment_id = ?", (member.creation_payment.payment_id,)
        ).fetchone()
        ledger_rows = connection.execute(
            "SELECT * FROM member_time_transactions WHERE member_id = ?", (member.member_id,)
        ).fetchall()
        redemption_row = connection.execute(
            "SELECT * FROM promo_redemptions WHERE member_id = ?", (member.member_id,)
        ).fetchone()

    assert member_row is not None
    assert payment_row is not None
    assert payment_row["status"] == "completed"
    # Exactly ONE initial-membership ledger credit, never base+bonus.
    assert len(ledger_rows) == 1
    assert ledger_rows[0]["transaction_type"] == "membership_purchase"
    assert ledger_rows[0]["seconds_delta"] == 25200
    assert ledger_rows[0]["balance_type"] == "standard"
    assert ledger_rows[0]["promo_redemption_id"] == redemption_row["redemption_id"]
    assert redemption_row["base_transaction_id"] == ledger_rows[0]["transaction_id"]
    assert redemption_row["bonus_transaction_id"] is None
    assert redemption_row["session_id"] is None
    assert redemption_row["payment_id"] == payment_row["payment_id"]


def test_total_limit_enforced_atomically(temp_directory: str) -> None:
    from server.schemas import MemberCreateRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "total-limit.sqlite3")
    make_new_member_promo(main_module, total_redemption_limit=1)

    first = main_module.member_repository.create_member(
        MemberCreateRequest(
            username=next_username(), pin="1234", confirm_pin="1234",
            amount=Decimal("100.00"), payment_method="cash", cash_received=Decimal("100.00"),
        ),
        cashier,
    )
    assert first.time_balance_seconds == 25200

    try:
        main_module.member_repository.create_member(
            MemberCreateRequest(
                username=next_username(), pin="1234", confirm_pin="1234",
                amount=Decimal("100.00"), payment_method="cash", cash_received=Decimal("100.00"),
            ),
            cashier,
        )
        raise AssertionError("expected 409: no permanent product exists at P100 in this test, and the promo's slot is exhausted")
    except HTTPException as error:
        assert error.status_code == 409


def test_idempotent_retry_creates_nothing_extra(temp_directory: str) -> None:
    from server.database import get_connection
    from server.schemas import MemberCreateRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "idempotent.sqlite3")
    make_new_member_promo(main_module)
    key = "stage5-idempotency-key"
    username = next_username()

    request = MemberCreateRequest(
        username=username, pin="1234", confirm_pin="1234",
        amount=Decimal("100.00"), payment_method="cash", cash_received=Decimal("100.00"),
        idempotency_key=key,
    )
    first = main_module.member_repository.create_member(request, cashier)
    second = main_module.member_repository.create_member(request, cashier)

    assert second.member_id == first.member_id
    assert second.time_balance_seconds == first.time_balance_seconds == 25200

    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM member_time_transactions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM promo_redemptions").fetchone()[0] == 1


def test_duplicate_username_rolls_back_everything(temp_directory: str) -> None:
    from server.database import get_connection
    from server.schemas import MemberCreateRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "duplicate-username.sqlite3")
    make_new_member_promo(main_module, total_redemption_limit=None)
    username = next_username()

    main_module.member_repository.create_member(
        MemberCreateRequest(
            username=username, pin="1234", confirm_pin="1234",
            amount=Decimal("100.00"), payment_method="cash", cash_received=Decimal("100.00"),
        ),
        cashier,
    )
    with get_connection() as connection:
        counts_before = (
            connection.execute("SELECT COUNT(*) FROM members").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM member_time_transactions").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM promo_redemptions").fetchone()[0],
        )

    try:
        main_module.member_repository.create_member(
            MemberCreateRequest(
                username=username, pin="5678", confirm_pin="5678",  # same username, different everything else
                amount=Decimal("100.00"), payment_method="cash", cash_received=Decimal("100.00"),
            ),
            cashier,
        )
        raise AssertionError("expected 409 for a duplicate username")
    except HTTPException as error:
        assert error.status_code == 409

    with get_connection() as connection:
        counts_after = (
            connection.execute("SELECT COUNT(*) FROM members").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM member_time_transactions").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM promo_redemptions").fetchone()[0],
        )
    assert counts_before == counts_after, "the failed duplicate-username attempt must roll back its own payment/ledger/redemption too"


def test_failed_payment_rolls_back_member_creation(temp_directory: str) -> None:
    from server.database import get_connection
    from server.schemas import MemberCreateRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "failed-payment.sqlite3")
    make_new_member_promo(main_module)

    try:
        main_module.member_repository.create_member(
            MemberCreateRequest(
                username=next_username(), pin="1234", confirm_pin="1234",
                amount=Decimal("100.00"), payment_method="cash", cash_received=Decimal("50.00"),  # insufficient cash
            ),
            cashier,
        )
        raise AssertionError("expected 400 for insufficient cash_received")
    except HTTPException as error:
        assert error.status_code == 400

    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0


# --- 8. Refund (reuses the existing generic refund_purchase path) ---------


def test_refund_follows_existing_policy_and_reverses_atomically(temp_directory: str) -> None:
    from server.schemas import MemberCreateRequest, MemberPurchaseRefundRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "refund-policy.sqlite3")
    make_new_member_promo(main_module)

    member = main_module.member_repository.create_member(
        MemberCreateRequest(
            username=next_username(), pin="1234", confirm_pin="1234",
            amount=Decimal("100.00"), payment_method="cash", cash_received=Decimal("100.00"),
        ),
        cashier,
    )
    assert member.time_balance_seconds == 25200

    refund = main_module.member_repository.refund_purchase(
        member.creation_payment.payment_id, MemberPurchaseRefundRequest(reason="Other", notes="test"), admin
    )
    assert refund.base_seconds == 25200
    assert refund.bonus_seconds == 0
    assert refund.total_seconds_reversed == 25200
    assert main_module.member_repository.get_member(member.member_id).time_balance_seconds == 0

    from server.database import get_connection

    with get_connection() as connection:
        payment_row = connection.execute(
            "SELECT status FROM payments WHERE payment_id = ?", (member.creation_payment.payment_id,)
        ).fetchone()
        redemption_row = connection.execute(
            "SELECT voided_at FROM promo_redemptions WHERE payment_id = ?", (member.creation_payment.payment_id,)
        ).fetchone()
    assert payment_row["status"] == "voided"
    assert redemption_row["voided_at"] is not None
    # Account itself is preserved, not deleted -- matches existing refund policy.
    assert main_module.member_repository.get_member(member.member_id) is not None


def test_total_limit_freed_after_refund(temp_directory: str) -> None:
    from server.schemas import MemberCreateRequest, MemberPurchaseRefundRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "limit-freed.sqlite3")
    make_new_member_promo(main_module, total_redemption_limit=1)

    first = main_module.member_repository.create_member(
        MemberCreateRequest(
            username=next_username(), pin="1234", confirm_pin="1234",
            amount=Decimal("100.00"), payment_method="cash", cash_received=Decimal("100.00"),
        ),
        cashier,
    )
    main_module.member_repository.refund_purchase(
        first.creation_payment.payment_id, MemberPurchaseRefundRequest(reason="Other", notes="free the slot"), admin
    )

    second = main_module.member_repository.create_member(
        MemberCreateRequest(
            username=next_username(), pin="1234", confirm_pin="1234",
            amount=Decimal("100.00"), payment_method="cash", cash_received=Decimal("100.00"),
        ),
        cashier,
    )
    assert second.time_balance_seconds == 25200


def test_generic_payment_void_cannot_bypass_new_member_balance_safeguards(temp_directory: str) -> None:
    from server.schemas import MemberCreateRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "generic-void-blocked.sqlite3")
    make_new_member_promo(main_module)

    member = main_module.member_repository.create_member(
        MemberCreateRequest(
            username=next_username(), pin="1234", confirm_pin="1234",
            amount=Decimal("100.00"), payment_method="cash", cash_received=Decimal("100.00"),
        ),
        cashier,
    )
    try:
        main_module.billing_service.void_payment(member.creation_payment.payment_id, "trying generic void", admin.user_id)
        raise AssertionError("the generic session-payment void must reject an account-creation payment")
    except HTTPException as error:
        assert error.status_code == 400

    assert main_module.member_repository.get_member(member.member_id).time_balance_seconds == 25200


# --- 9. Attribution --------------------------------------------------------


def test_attribution_snapshots_correct(temp_directory: str) -> None:
    from server.schemas import MemberCreateRequest, ShiftOpenRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "attribution.sqlite3")
    make_new_member_promo(main_module)
    main_module.open_shift(ShiftOpenRequest(opening_cash=Decimal("0.00")), cashier)

    member = main_module.member_repository.create_member(
        MemberCreateRequest(
            username=next_username(), pin="1234", confirm_pin="1234",
            amount=Decimal("100.00"), payment_method="cash", cash_received=Decimal("100.00"),
        ),
        cashier,
    )
    # A brand-new member has no active session anywhere -- attribution is
    # always pending, never invented from nothing. Matches existing policy
    # for any purchase resolved with no active session.
    assert member.creation_payment.attribution_status == "pending"
    assert member.creation_payment.recorded_by_user_id == cashier.user_id
    assert member.creation_payment.shift_id is not None

    activity = main_module.list_operator_activity("all", None, "payment", 50, owner)
    matching = [item for item in activity if item.amount == Decimal("100.00") and item.operator_id == cashier.user_id]
    assert len(matching) >= 1


# --- Compatibility ----------------------------------------------------------


def test_permanent_initial_membership_product_unchanged(temp_directory: str) -> None:
    from server.schemas import MemberCreateRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "product-unchanged.sqlite3")
    before = main_module.member_repository.get_product("membership-fee")
    make_new_member_promo(main_module)

    main_module.member_repository.create_member(
        MemberCreateRequest(
            username=next_username(), pin="1234", confirm_pin="1234",
            amount=Decimal("100.00"), payment_method="cash", cash_received=Decimal("100.00"),
        ),
        cashier,
    )
    after = main_module.member_repository.get_product("membership-fee")
    assert before == after


def test_non_promo_member_creation_unchanged(temp_directory: str) -> None:
    from server.schemas import MemberCreateRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "non-promo-unchanged.sqlite3")
    make_new_member_promo(main_module)  # promo exists but amount is omitted entirely

    member = main_module.member_repository.create_member(
        MemberCreateRequest(username=next_username(), pin="1234", confirm_pin="1234"), cashier
    )
    assert member.time_balance_seconds == 0
    assert member.creation_payment is None
    assert member.creation_applied_promo is None
    assert member.creation_base_seconds is None

    from server.database import get_connection

    with get_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM payments WHERE session_id IS NULL"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM member_time_transactions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM promo_redemptions").fetchone()[0] == 0


def test_amount_without_matching_promo_rejected_cleanly(temp_directory: str) -> None:
    """No ordinary paid-signup path exists on this route -- if `amount`
    doesn't resolve to a promo, the request is rejected outright rather
    than silently creating a free account or discarding the payment."""
    from server.database import get_connection
    from server.schemas import MemberCreateRequest

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "amount-no-promo.sqlite3")
    # No promo configured at all.
    try:
        main_module.member_repository.create_member(
            MemberCreateRequest(
                username=next_username(), pin="1234", confirm_pin="1234",
                amount=Decimal("100.00"), payment_method="cash", cash_received=Decimal("100.00"),
            ),
            cashier,
        )
        raise AssertionError("expected 409 when amount is supplied but no promo matches")
    except HTTPException as error:
        assert error.status_code == 409

    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 0


def main() -> None:
    # Only the eligibility / quote / validation tests -- the New Member
    # promo claim no longer has a reachable entry point (see module note).
    tests = [
        test_minimum_amount_mode_rejected_for_new_member,
        test_per_member_limit_rejected_for_new_member,
        test_exact_amount_eligibility,
        test_active_scheduled_expired_disabled_behavior,
        test_promo_better_than_same_price_product_selected,
        test_equal_or_worse_promo_falls_back_to_product,
        test_different_price_promo_works_as_temporary_offer,
        test_quote_makes_zero_writes,
        test_invalid_amount_handled_clearly,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
        print(f"{test.__name__} passed.")
    print("Promo Maker Stage 5 (New Member) smoke tests passed.")


if __name__ == "__main__":
    main()

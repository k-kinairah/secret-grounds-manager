"""Stage 3: VIP balance purchase/credit.

Covers: wallet separation, atomic purchase (payment + credit in one
transaction), idempotent retry safety, non-refundable enforcement (both the
dedicated refund endpoint and the generic session-payment void endpoint),
and that Standard purchases/top-ups are entirely unaffected by the new
balance_type-aware crediting path.
"""

import os
import sys
import tempfile
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
    from server.schemas import UserCreateRequest
    from server.session_repository import SessionRepository
    from server.shift_repository import ShiftRepository
    from server.user_repository import UserRepository

    user_repository = UserRepository()
    cashier_public = user_repository.create_user(
        UserCreateRequest(username="vip-cashier", display_name="VIP Cashier", password="cashier-pass", role="cashier")
    )
    actor = user_repository.get_user(cashier_public.user_id)
    assert actor is not None

    payment_repository = PaymentRepository()
    shift_repository = ShiftRepository()
    member_repository = MemberRepository(payment_repository, shift_repository)
    billing_service = BillingService(SessionRepository(), payment_repository, shift_repository, user_repository)
    return member_repository, payment_repository, billing_service, actor


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


def test_new_member_starts_with_zero_vip_balance(temp_directory: str) -> None:
    member_repository, *_ = create_repositories(Path(temp_directory) / "new-member.sqlite3")
    member = create_test_member(member_repository, "fresh.member")
    assert member.time_balance_seconds == 0
    assert member.vip_balance_seconds == 0
    reloaded = member_repository.get_member(member.member_id)
    assert reloaded is not None
    assert reloaded.vip_balance_seconds == 0


def test_standard_purchases_unaffected_by_balance_type_infrastructure(temp_directory: str) -> None:
    member_repository, payment_repository, _, actor = create_repositories(
        Path(temp_directory) / "standard-purchases.sqlite3"
    )
    member = create_test_member(member_repository, "standard.member")

    # ₱100 account creation = 6 Standard hours, 0 VIP.
    response = member_repository.purchase(member.member_id, make_purchase_request("membership-fee"), actor)
    assert response.member.time_balance_seconds == 6 * 3600
    assert response.member.vip_balance_seconds == 0
    assert response.transaction.balance_type == "standard"

    # ₱50 top-up = +4 Standard hours, +0 VIP.
    response = member_repository.purchase(member.member_id, make_purchase_request("member-promo"), actor)
    assert response.member.time_balance_seconds == (6 + 4) * 3600
    assert response.member.vip_balance_seconds == 0
    assert response.transaction.balance_type == "standard"

    # ₱100 top-up = +8 Standard hours, +0 VIP.
    response = member_repository.purchase(member.member_id, make_purchase_request("member-topup-100"), actor)
    assert response.member.time_balance_seconds == (6 + 4 + 8) * 3600
    assert response.member.vip_balance_seconds == 0
    assert response.transaction.balance_type == "standard"


def test_vip_promo_purchase_and_accumulation(temp_directory: str) -> None:
    member_repository, payment_repository, _, actor = create_repositories(
        Path(temp_directory) / "vip-purchase.sqlite3"
    )
    member = create_test_member(member_repository, "vip.member")

    # Seed a pre-existing Standard balance to prove it is never touched.
    member_repository.apply_balance_delta(
        member.member_id, "manual_adjustment", 5000, actor.user_id, balance_type="standard"
    )

    first = member_repository.purchase(member.member_id, make_purchase_request("vip-promo-250"), actor)
    assert first.product.product_id == "vip-promo-250"
    assert first.product.balance_type == "vip"
    assert first.transaction.balance_type == "vip"
    assert first.transaction.seconds_delta == 36000
    assert first.member.vip_balance_seconds == 36000, "₱250 must credit exactly 36,000 seconds (10 hours)"
    assert first.member.time_balance_seconds == 5000, "Standard balance must remain completely unchanged"

    # Second purchase accumulates: 7h existing + 10h purchase = 17h, scaled
    # here as 10h + 10h = 20h to also prove repeatability.
    second = member_repository.purchase(member.member_id, make_purchase_request("vip-promo-250"), actor)
    assert second.member.vip_balance_seconds == 72000
    assert second.member.time_balance_seconds == 5000, "Standard balance must still be untouched after a 2nd VIP purchase"
    assert second.transaction.balance_before_seconds == 36000
    assert second.transaction.balance_after_seconds == 72000


def test_vip_promo_requires_member() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        member_repository, *_, actor = create_repositories(Path(temp_directory) / "no-member.sqlite3")
        try:
            member_repository.purchase("does-not-exist", make_purchase_request("vip-promo-250"), actor)
        except HTTPException as error:
            assert error.status_code == 404
        else:
            raise AssertionError("VIP promo must require an existing member account")


def test_inactive_or_invalid_product_cannot_be_purchased(temp_directory: str) -> None:
    member_repository, *_, actor = create_repositories(Path(temp_directory) / "inactive-product.sqlite3")
    member = create_test_member(member_repository, "inactive.product.member")

    try:
        member_repository.purchase(member.member_id, make_purchase_request("does-not-exist"), actor)
    except HTTPException as error:
        assert error.status_code == 404
    else:
        raise AssertionError("Purchasing a nonexistent product must be rejected")

    member_repository.set_product_active("vip-promo-250", False)
    try:
        member_repository.purchase(member.member_id, make_purchase_request("vip-promo-250"), actor)
    except HTTPException as error:
        assert error.status_code == 404
    else:
        raise AssertionError("Purchasing an inactive product must be rejected")
    finally:
        member_repository.set_product_active("vip-promo-250", True)


def test_failed_purchase_leaves_no_partial_state(temp_directory: str) -> None:
    member_repository, payment_repository, _, actor = create_repositories(
        Path(temp_directory) / "failed-purchase.sqlite3"
    )
    member = create_test_member(member_repository, "failure.member")

    original_apply_balance_delta = member_repository.apply_balance_delta

    def failing_apply_balance_delta(*args, **kwargs):
        raise RuntimeError("simulated crash mid-purchase")

    member_repository.apply_balance_delta = failing_apply_balance_delta  # type: ignore[method-assign]
    idempotency_key = "vip-purchase-failure-test-key"
    try:
        try:
            member_repository.purchase(member.member_id, make_purchase_request("vip-promo-250", idempotency_key), actor)
        except RuntimeError:
            pass
        else:
            raise AssertionError("The simulated failure must propagate")
    finally:
        member_repository.apply_balance_delta = original_apply_balance_delta  # type: ignore[method-assign]

    # Neither side of the atomic pair persisted: no payment, no balance
    # change, no ledger row.
    assert payment_repository.get_payment_by_idempotency_key(idempotency_key) is None
    reloaded = member_repository.get_member(member.member_id)
    assert reloaded is not None
    assert reloaded.vip_balance_seconds == 0
    assert reloaded.time_balance_seconds == 0
    assert member_repository.list_ledger(member.member_id) == []

    # A normal retry (fresh key or same key) now succeeds exactly once.
    result = member_repository.purchase(member.member_id, make_purchase_request("vip-promo-250", idempotency_key), actor)
    assert result.member.vip_balance_seconds == 36000


def test_idempotent_retry_does_not_double_credit(temp_directory: str) -> None:
    member_repository, *_, actor = create_repositories(Path(temp_directory) / "idempotent-retry.sqlite3")
    member = create_test_member(member_repository, "retry.member")
    idempotency_key = "vip-retry-key"

    first = member_repository.purchase(member.member_id, make_purchase_request("vip-promo-250", idempotency_key), actor)
    assert first.member.vip_balance_seconds == 36000

    retry = member_repository.purchase(member.member_id, make_purchase_request("vip-promo-250", idempotency_key), actor)
    assert retry.member.vip_balance_seconds == 36000, "A retried request with the same idempotency key must not double-credit"
    assert retry.payment.payment_id == first.payment.payment_id
    assert retry.transaction.transaction_id == first.transaction.transaction_id


def test_completed_vip_promo_cannot_be_refunded(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRefundRequest

    member_repository, *_, actor = create_repositories(Path(temp_directory) / "vip-refund.sqlite3")
    member = create_test_member(member_repository, "refund.member")
    purchase = member_repository.purchase(member.member_id, make_purchase_request("vip-promo-250"), actor)

    try:
        member_repository.refund_purchase(
            purchase.payment.payment_id,
            MemberPurchaseRefundRequest(reason="Customer cancellation"),
            actor,
        )
    except HTTPException as error:
        assert error.status_code == 409
        assert "non-refundable" in error.detail.lower()
    else:
        raise AssertionError("A completed VIP promo purchase must never be refundable")

    # Balance and payment are untouched by the rejected attempt.
    reloaded = member_repository.get_member(member.member_id)
    assert reloaded is not None
    assert reloaded.vip_balance_seconds == 36000
    payment = member_repository.payment_repository.get_payment(purchase.payment.payment_id)
    assert payment is not None
    assert payment.status == "completed"

    # A Standard purchase remains refundable -- the block is VIP-specific.
    standard_purchase = member_repository.purchase(member.member_id, make_purchase_request("member-promo"), actor)
    refunded = member_repository.refund_purchase(
        standard_purchase.payment.payment_id,
        MemberPurchaseRefundRequest(reason="Customer cancellation"),
        actor,
    )
    assert refunded.member.time_balance_seconds == 0


def test_generic_void_endpoint_cannot_reverse_membership_purchase(temp_directory: str) -> None:
    from server.schemas import VoidPaymentRequest

    member_repository, _, billing_service, actor = create_repositories(
        Path(temp_directory) / "generic-void.sqlite3"
    )
    member = create_test_member(member_repository, "void.member")
    vip_purchase = member_repository.purchase(member.member_id, make_purchase_request("vip-promo-250"), actor)

    try:
        billing_service.void_payment(vip_purchase.payment.payment_id, "attempted bypass", actor.user_id)
    except HTTPException as error:
        assert error.status_code == 400
    else:
        raise AssertionError("The generic session-payment void endpoint must reject a membership purchase")

    payment = member_repository.payment_repository.get_payment(vip_purchase.payment.payment_id)
    assert payment is not None
    assert payment.status == "completed", "the payment must not be left half-voided by the rejected attempt"
    reloaded = member_repository.get_member(member.member_id)
    assert reloaded is not None
    assert reloaded.vip_balance_seconds == 36000, "VIP balance must be untouched by the rejected void attempt"


def test_member_detail_returns_both_balances(temp_directory: str) -> None:
    member_repository, *_, actor = create_repositories(Path(temp_directory) / "member-detail.sqlite3")
    member = create_test_member(member_repository, "detail.member")
    member_repository.purchase(member.member_id, make_purchase_request("membership-fee"), actor)
    member_repository.purchase(member.member_id, make_purchase_request("vip-promo-250"), actor)

    reloaded = member_repository.get_member(member.member_id)
    assert reloaded is not None
    assert reloaded.time_balance_seconds == 6 * 3600
    assert reloaded.vip_balance_seconds == 36000

    listed = member_repository.list_members(reloaded.username)
    assert len(listed) == 1
    assert listed[0].time_balance_seconds == 6 * 3600
    assert listed[0].vip_balance_seconds == 36000


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_new_member_starts_with_zero_vip_balance(temp_directory)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_standard_purchases_unaffected_by_balance_type_infrastructure(temp_directory)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_vip_promo_purchase_and_accumulation(temp_directory)
    test_vip_promo_requires_member()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_inactive_or_invalid_product_cannot_be_purchased(temp_directory)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_failed_purchase_leaves_no_partial_state(temp_directory)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_idempotent_retry_does_not_double_credit(temp_directory)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_completed_vip_promo_cannot_be_refunded(temp_directory)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_generic_void_endpoint_cannot_reverse_membership_purchase(temp_directory)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_member_detail_returns_both_balances(temp_directory)
    print("VIP purchase smoke test passed.")


if __name__ == "__main__":
    main()

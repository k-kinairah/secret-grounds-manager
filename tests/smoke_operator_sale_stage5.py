"""Stage 5 tests: authoritative Operator's Sale read path.

Covers: GET /dashboard/operator-sale resolves the on-duty operator the
exact same way payment attribution does (get_on_duty_operator), reports
a clean shape when nobody is on duty or the on-duty operator has no open
shift, reuses ShiftRepository.calculate_totals() verbatim (net of
voided), counts pending-income-attribution member purchases immediately,
survives ordinary logout/login, and correctly goes stale after End Shift.
"""

import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from fastapi import Response

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def create_env(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    import server.main as main_module

    return main_module


def create_cashier(main_module, username: str):
    from server.schemas import UserCreateRequest

    public = main_module.user_repository.create_user(
        UserCreateRequest(username=username, display_name=username, password=f"{username}-pass", role="cashier")
    )
    record = main_module.user_repository.get_user(public.user_id)
    assert record is not None
    return record


def login(main_module, username: str, password: str) -> None:
    from server.schemas import LoginRequest

    main_module.login(LoginRequest(username=username, password=password), Response())


def create_session_and_pay(main_module, session_id: str, pc_name: str, amount: Decimal, actor, payment_method: str = "cash"):
    from server.schemas import PaymentCreateRequest, SessionRecord
    from server.time_utils import current_utc_time

    now = current_utc_time()
    main_module.session_repository.create_session(
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
    kwargs = {"payment_method": payment_method, "amount": amount}
    if payment_method == "cash":
        kwargs["cash_received"] = amount
    else:
        kwargs["reference_number"] = "REF-1"
    return main_module.record_payment(session_id, PaymentCreateRequest(**kwargs), actor)


def make_purchase_request(product_id: str):
    from server.schemas import MemberPurchaseRequest

    return MemberPurchaseRequest(product_id=product_id, payment_method="cash", cash_received="1000.00")


def test_no_on_duty_operator_reports_clean_zero(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "no-on-duty.sqlite3")
    summary = main_module.get_dashboard_operator_sale(None)
    assert summary.has_open_shift is False
    assert summary.operator_user_id is None
    assert summary.operator_sale == Decimal("0.00")


def test_on_duty_operator_no_open_shift_reports_zero(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "no-open-shift.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.noshift")
    login(main_module, "lalaine.noshift", "lalaine.noshift-pass")

    summary = main_module.get_dashboard_operator_sale(None)
    assert summary.has_open_shift is False
    assert summary.operator_user_id == lalaine.user_id
    assert summary.operator_name == "lalaine.noshift"
    assert summary.operator_sale == Decimal("0.00")


def test_open_shift_no_sales_reports_zero(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "open-no-sales.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.zerosale")
    login(main_module, "lalaine.zerosale", "lalaine.zerosale-pass")
    opened = main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), lalaine)

    summary = main_module.get_dashboard_operator_sale(None)
    assert summary.has_open_shift is True
    assert summary.shift_id == opened.shift.shift_id
    assert summary.operator_sale == Decimal("0.00")


def test_single_session_payment_appears(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "single-session.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.single")
    login(main_module, "lalaine.single", "lalaine.single-pass")
    main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), lalaine)
    create_session_and_pay(main_module, "session-single-1", "PC-SINGLE", Decimal("20.00"), lalaine)

    summary = main_module.get_dashboard_operator_sale(None)
    assert summary.operator_sale == Decimal("20.00")
    assert summary.cash_payments == Decimal("20.00")
    assert summary.payment_count == 1


def test_member_purchase_appears(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "member-purchase.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.member")
    login(main_module, "lalaine.member", "lalaine.member-pass")
    main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), lalaine)

    from server.schemas import MemberCreateRequest

    member = main_module.member_repository.create_member(
        MemberCreateRequest(username="stage5.member", display_name="stage5.member", pin="1234", confirm_pin="1234"),
        None,
    )
    main_module.member_repository.purchase(member.member_id, make_purchase_request("member-promo"), lalaine)

    summary = main_module.get_dashboard_operator_sale(None)
    assert summary.operator_sale == Decimal("50.00")


def test_pending_attribution_purchase_counts_immediately(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "pending-purchase.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.pending")
    login(main_module, "lalaine.pending", "lalaine.pending-pass")
    main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), lalaine)

    from server.schemas import MemberCreateRequest

    member = main_module.member_repository.create_member(
        MemberCreateRequest(username="stage5.pending", display_name="stage5.pending", pin="1234", confirm_pin="1234"),
        None,
    )
    # No active session -> this purchase is attribution_status='pending'
    # per Stage 2/3, but the cash has already been received.
    purchase = main_module.member_repository.purchase(member.member_id, make_purchase_request("member-promo"), lalaine)
    assert purchase.payment.attribution_status == "pending"

    summary = main_module.get_dashboard_operator_sale(None)
    assert summary.operator_sale == Decimal("50.00"), "a pending-attribution purchase must still count immediately"


def test_mixed_payment_types_sum_correctly(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "mixed-types.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.mixed")
    login(main_module, "lalaine.mixed", "lalaine.mixed-pass")
    main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), lalaine)

    create_session_and_pay(main_module, "session-mixed-1", "PC-MIXED-1", Decimal("20.00"), lalaine, "cash")
    create_session_and_pay(main_module, "session-mixed-2", "PC-MIXED-2", Decimal("30.00"), lalaine, "gcash")

    from server.schemas import MemberCreateRequest

    member = main_module.member_repository.create_member(
        MemberCreateRequest(username="stage5.mixed", display_name="stage5.mixed", pin="1234", confirm_pin="1234"),
        None,
    )
    main_module.member_repository.purchase(member.member_id, make_purchase_request("vip-promo-250"), lalaine)

    summary = main_module.get_dashboard_operator_sale(None)
    assert summary.operator_sale == Decimal("20.00") + Decimal("30.00") + Decimal("250.00")
    # The VIP promo purchase is also paid in cash, so cash_payments
    # includes both the ₱20 session payment and the ₱250 VIP purchase.
    assert summary.cash_payments == Decimal("20.00") + Decimal("250.00")
    assert summary.gcash_payments == Decimal("30.00")
    assert summary.payment_count == 3


def test_void_reduces_net_correctly(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "void-reduces.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.void")
    login(main_module, "lalaine.void", "lalaine.void-pass")
    main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), lalaine)

    billing = create_session_and_pay(main_module, "session-void-1", "PC-VOID", Decimal("40.00"), lalaine)
    payment_id = billing.payments[0].payment_id

    before = main_module.get_dashboard_operator_sale(None)
    assert before.operator_sale == Decimal("40.00")

    from server.schemas import VoidPaymentRequest

    main_module.void_payment(payment_id, VoidPaymentRequest(reason="Refund requested"), lalaine)

    after = main_module.get_dashboard_operator_sale(None)
    assert after.operator_sale == Decimal("0.00")
    assert after.voided_payments == Decimal("40.00")


def test_ordinary_logout_does_not_change_amount(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "logout-persist.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.logout")
    from server.schemas import LoginRequest

    login_response = Response()
    main_module.login(LoginRequest(username="lalaine.logout", password="lalaine.logout-pass"), login_response)
    from http.cookies import SimpleCookie

    set_cookie = next(value.decode("latin-1") for name, value in login_response.raw_headers if name == b"set-cookie")
    token = SimpleCookie(set_cookie)["sg_session"].value

    main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), lalaine)
    create_session_and_pay(main_module, "session-logout-1", "PC-LOGOUT", Decimal("150.00"), lalaine)

    before = main_module.get_dashboard_operator_sale(None)
    assert before.operator_sale == Decimal("150.00")

    main_module.logout(Response(), token)

    after = main_module.get_dashboard_operator_sale(None)
    assert after.operator_sale == Decimal("150.00"), "ordinary Sign Out must never change Operator's Sale"
    assert after.has_open_shift is True


def test_login_again_returns_same_shift_same_amount(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "login-again.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.again")
    login(main_module, "lalaine.again", "lalaine.again-pass")
    opened = main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), lalaine)
    create_session_and_pay(main_module, "session-again-1", "PC-AGAIN", Decimal("75.00"), lalaine)

    login(main_module, "lalaine.again", "lalaine.again-pass")

    summary = main_module.get_dashboard_operator_sale(None)
    assert summary.shift_id == opened.shift.shift_id
    assert summary.operator_sale == Decimal("75.00")


def test_another_browser_user_does_not_change_whose_sale_is_shown(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "another-browser.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.other")
    other = create_cashier(main_module, "other.browser")

    login(main_module, "lalaine.other", "lalaine.other-pass")
    main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), lalaine)
    create_session_and_pay(main_module, "session-otherbrowser-1", "PC-OTHERBROWSER", Decimal("90.00"), lalaine)

    # `other` never logs in (on-duty singleton still points at lalaine).
    # Calling the endpoint as if a different browser/cookie session were
    # asking must not change whose sale is reported -- the endpoint takes
    # no current_user parameter at all, proving cookie identity plays no
    # role in the answer.
    summary = main_module.get_dashboard_operator_sale(None)
    assert summary.operator_user_id == lalaine.user_id
    assert summary.operator_sale == Decimal("90.00")
    assert other.user_id != summary.operator_user_id


def test_resolution_helper_is_shared_with_payment_attribution(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "shared-resolution.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.shared")
    login(main_module, "lalaine.shared", "lalaine.shared-pass")

    on_duty_from_read_path = main_module.get_on_duty_operator()
    resolved_actor_for_writes = main_module.get_on_duty_actor_user_id(lalaine)
    assert on_duty_from_read_path.operator is not None
    assert on_duty_from_read_path.operator.user_id == resolved_actor_for_writes == lalaine.user_id


def test_end_shift_makes_operator_sale_unavailable(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "end-shift-unavailable.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.endshift")
    login(main_module, "lalaine.endshift", "lalaine.endshift-pass")
    opened = main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), lalaine)
    create_session_and_pay(main_module, "session-endshift-1", "PC-ENDSHIFT", Decimal("60.00"), lalaine)

    before = main_module.get_dashboard_operator_sale(None)
    assert before.operator_sale == Decimal("60.00")
    assert before.has_open_shift is True

    main_module.close_shift(opened.shift.shift_id, main_module.ShiftCloseRequest(actual_cash=Decimal("60.00")), Response(), lalaine)

    after = main_module.get_dashboard_operator_sale(None)
    assert after.has_open_shift is False
    assert after.operator_user_id is None, "on-duty state was cleared by End Shift, so nobody is reported on duty"
    assert after.operator_sale == Decimal("0.00")


def test_another_operators_shift_is_never_included(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "other-shift-excluded.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.isolated")
    other = create_cashier(main_module, "other.isolated")

    login(main_module, "lalaine.isolated", "lalaine.isolated-pass")
    main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), lalaine)
    create_session_and_pay(main_module, "session-isolated-lalaine", "PC-ISOLATED-1", Decimal("10.00"), lalaine)

    login(main_module, "other.isolated", "other.isolated-pass")
    main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), other)
    create_session_and_pay(main_module, "session-isolated-other", "PC-ISOLATED-2", Decimal("999.00"), other)

    summary = main_module.get_dashboard_operator_sale(None)
    assert summary.operator_user_id == other.user_id, "on-duty is now `other` (logged in most recently)"
    assert summary.operator_sale == Decimal("999.00"), "lalaine's ₱10 must never leak into other's reported sale"


def main() -> None:
    tests = [
        test_no_on_duty_operator_reports_clean_zero,
        test_on_duty_operator_no_open_shift_reports_zero,
        test_open_shift_no_sales_reports_zero,
        test_single_session_payment_appears,
        test_member_purchase_appears,
        test_pending_attribution_purchase_counts_immediately,
        test_mixed_payment_types_sum_correctly,
        test_void_reduces_net_correctly,
        test_ordinary_logout_does_not_change_amount,
        test_login_again_returns_same_shift_same_amount,
        test_another_browser_user_does_not_change_whose_sale_is_shown,
        test_resolution_helper_is_shared_with_payment_attribution,
        test_end_shift_makes_operator_sale_unavailable,
        test_another_operators_shift_is_never_included,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
    print("Stage 5 Operator's Sale smoke tests passed.")


if __name__ == "__main__":
    main()

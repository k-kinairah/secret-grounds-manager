"""Stage 4 tests: End Shift lifecycle correctness.

Covers: ordinary Sign Out never touches the shift; a shift's sales/totals
survive logout/login and are never duplicated; End Shift finalizes the
shift, force-logs-out the operator from every device, clears the global
on-duty state only if it still points at the closed operator, and never
touches a different operator's sessions/on-duty state; a failed close
leaves auth/on-duty state completely untouched.
"""

import os
import sys
import tempfile
from decimal import Decimal
from http.cookies import SimpleCookie
from pathlib import Path

from fastapi import HTTPException, Response

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


def create_owner(main_module, username: str):
    from server.schemas import UserCreateRequest

    public = main_module.user_repository.create_user(
        UserCreateRequest(username=username, display_name=username, password=f"{username}-pass", role="owner")
    )
    record = main_module.user_repository.get_user(public.user_id)
    assert record is not None
    return record


def login(main_module, username: str, password: str) -> str:
    """Real login through the actual route function -- mints a genuine
    auth_sessions row/cookie and updates the operator_state singleton,
    exactly like a real browser login would."""
    from server.schemas import LoginRequest

    response = Response()
    main_module.login(LoginRequest(username=username, password=password), response)
    set_cookie = next(
        value.decode("latin-1") for name, value in response.raw_headers if name == b"set-cookie"
    )
    return SimpleCookie(set_cookie)["sg_session"].value


def create_session_and_pay(main_module, session_id: str, pc_name: str, amount: Decimal, actor):
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
    return main_module.record_payment(
        session_id,
        PaymentCreateRequest(payment_method="cash", amount=amount, cash_received=amount),
        actor,
    )


def token_is_valid(main_module, token: str) -> bool:
    try:
        main_module.get_current_user(sg_session=token)
    except HTTPException as error:
        assert error.status_code == 401
        return False
    return True


# --- 1: ordinary Sign Out ----------------------------------------------


def test_ordinary_sign_out_leaves_shift_and_totals_untouched(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "signout-basic.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.signout")
    token = login(main_module, "lalaine.signout", "lalaine.signout-pass")

    main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), lalaine)
    create_session_and_pay(main_module, "session-signout-1", "PC-SIGNOUT", Decimal("400.00"), lalaine)

    before = main_module.shift_repository.get_open_shift_for_user(lalaine.user_id)
    assert before is not None
    assert before.status == "open"

    main_module.logout(Response(), token)

    after = main_module.shift_repository.get_open_shift_for_user(lalaine.user_id)
    assert after is not None
    assert after.shift_id == before.shift_id
    assert after.status == "open"
    detail = main_module.shift_repository.get_shift(after.shift_id, lalaine)
    assert detail.totals.gross_session_sales == Decimal("400.00")
    assert token_is_valid(main_module, token) is False, "logout must still revoke the current token"


def test_operator_logs_back_in_resumes_same_shift_no_duplicate(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "signout-resume.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.resume")
    first_token = login(main_module, "lalaine.resume", "lalaine.resume-pass")

    opened = main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), lalaine)
    create_session_and_pay(main_module, "session-resume-1", "PC-RESUME", Decimal("150.00"), lalaine)
    main_module.logout(Response(), first_token)

    login(main_module, "lalaine.resume", "lalaine.resume-pass")

    resumed = main_module.shift_repository.get_open_shift_for_user(lalaine.user_id)
    assert resumed is not None
    assert resumed.shift_id == opened.shift.shift_id
    detail = main_module.shift_repository.get_shift(resumed.shift_id, lalaine)
    assert detail.totals.gross_session_sales == Decimal("150.00")

    with main_module.get_connection() as connection:
        open_count = connection.execute(
            "SELECT COUNT(*) FROM cashier_shifts WHERE user_id = ? AND status = 'open'",
            (lalaine.user_id,),
        ).fetchone()[0]
    assert open_count == 1, "logging back in must never create a second open shift"


# --- 2: End Shift closes/finalizes --------------------------------------


def test_end_shift_closes_and_preserves_frozen_totals(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "end-shift-totals.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.close")
    login(main_module, "lalaine.close", "lalaine.close-pass")

    opened = main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("100.00")), lalaine)
    create_session_and_pay(main_module, "session-close-1", "PC-CLOSE", Decimal("250.00"), lalaine)

    pre_close_totals = main_module.shift_repository.get_shift(opened.shift.shift_id, lalaine).totals

    closed = main_module.close_shift(
        opened.shift.shift_id,
        main_module.ShiftCloseRequest(actual_cash=Decimal("350.00")),
        Response(),
        lalaine,
    )
    assert closed.shift.status == "closed"
    assert closed.shift.gross_session_sales == pre_close_totals.gross_session_sales == Decimal("250.00")
    assert closed.shift.cash_payments == pre_close_totals.cash_payments
    assert closed.shift.payment_count == pre_close_totals.payment_count
    assert closed.shift.sessions_handled == pre_close_totals.sessions_handled
    assert closed.shift.opening_cash == Decimal("100.00")
    assert closed.shift.expected_cash == Decimal("350.00")
    assert closed.shift.actual_cash == Decimal("350.00")
    assert closed.shift.difference == Decimal("0.00")

    still_open = main_module.shift_repository.get_open_shift_for_user(lalaine.user_id)
    assert still_open is None, "operator must have no open shift once End Shift succeeds"


# --- 3: force logout everywhere -----------------------------------------


def test_end_shift_revokes_every_session_and_clears_cookie(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "end-shift-revoke.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.revoke")
    token_device_a = login(main_module, "lalaine.revoke", "lalaine.revoke-pass")
    token_device_b = login(main_module, "lalaine.revoke", "lalaine.revoke-pass")
    assert token_is_valid(main_module, token_device_a)
    assert token_is_valid(main_module, token_device_b)

    opened = main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), lalaine)
    create_session_and_pay(main_module, "session-revoke-1", "PC-REVOKE", Decimal("75.00"), lalaine)

    close_response = Response()
    main_module.close_shift(
        opened.shift.shift_id,
        main_module.ShiftCloseRequest(actual_cash=Decimal("75.00")),
        close_response,
        lalaine,
    )

    assert token_is_valid(main_module, token_device_a) is False, "device A session must be invalidated"
    assert token_is_valid(main_module, token_device_b) is False, "device B session must be invalidated too"
    set_cookie_headers = [value for name, value in close_response.raw_headers if name == b"set-cookie"]
    assert any(b"sg_session=" in header and (b'sg_session=""' in header or b"Max-Age=0" in header) for header in set_cookie_headers), (
        "self-close must clear the requesting browser's own cookie"
    )


# --- 4: global on-duty operator state ------------------------------------


def test_operator_state_cleared_when_it_points_to_the_closed_operator(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "operator-state-cleared.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.state")
    login(main_module, "lalaine.state", "lalaine.state-pass")

    on_duty_before = main_module.operator_state_repository.get_current(main_module.user_repository.get_user)
    assert on_duty_before.operator is not None
    assert on_duty_before.operator.user_id == lalaine.user_id

    opened = main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), lalaine)
    main_module.close_shift(
        opened.shift.shift_id, main_module.ShiftCloseRequest(actual_cash=Decimal("0.00")), Response(), lalaine
    )

    on_duty_after = main_module.operator_state_repository.get_current(main_module.user_repository.get_user)
    assert on_duty_after.operator is None, "on-duty singleton must be cleared once its operator's shift ends"


def test_other_operator_state_and_sessions_untouched_by_end_shift(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "operator-state-other.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.other")
    other = create_cashier(main_module, "other.operator")

    login(main_module, "lalaine.other", "lalaine.other-pass")
    opened = main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), lalaine)
    create_session_and_pay(main_module, "session-other-1", "PC-OTHER", Decimal("60.00"), lalaine)

    # Someone else logs in afterward -- the global singleton now points at
    # `other`, not at the shift owner (`lalaine`) whose shift is about to close.
    other_token = login(main_module, "other.operator", "other.operator-pass")
    on_duty = main_module.operator_state_repository.get_current(main_module.user_repository.get_user)
    assert on_duty.operator is not None and on_duty.operator.user_id == other.user_id

    # lalaine can still close her own shift regardless of who is currently on duty.
    main_module.close_shift(
        opened.shift.shift_id, main_module.ShiftCloseRequest(actual_cash=Decimal("60.00")), Response(), lalaine
    )

    on_duty_after = main_module.operator_state_repository.get_current(main_module.user_repository.get_user)
    assert on_duty_after.operator is not None
    assert on_duty_after.operator.user_id == other.user_id, "a different on-duty operator must never be cleared"
    assert token_is_valid(main_module, other_token) is True, "a different operator's session must never be revoked"


def test_force_close_by_owner_does_not_touch_owners_own_cookie_or_session(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "force-close-owner.sqlite3")
    cashier = create_cashier(main_module, "closed.cashier")
    owner = create_owner(main_module, "closing.owner")

    cashier_token = login(main_module, "closed.cashier", "closed.cashier-pass")
    opened = main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), cashier)
    create_session_and_pay(main_module, "session-force-1", "PC-FORCE", Decimal("30.00"), cashier)

    # Owner logs in after the cashier -- on-duty now points at the owner,
    # not at the shift being force-closed.
    owner_token = login(main_module, "closing.owner", "closing.owner-pass")

    owner_response = Response()
    main_module.close_shift(
        opened.shift.shift_id,
        main_module.ShiftCloseRequest(actual_cash=Decimal("30.00"), force_close_reason="Cashier forgot to end shift"),
        owner_response,
        owner,
    )

    assert token_is_valid(main_module, cashier_token) is False, "the shift owner's session must be revoked"
    assert token_is_valid(main_module, owner_token) is True, "the closing owner's own session must survive"
    owner_set_cookie_headers = [value for name, value in owner_response.raw_headers if name == b"set-cookie"]
    assert owner_set_cookie_headers == [], "force-closing someone else's shift must never clear the actor's own cookie"

    on_duty_after = main_module.operator_state_repository.get_current(main_module.user_repository.get_user)
    assert on_duty_after.operator is not None
    assert on_duty_after.operator.user_id == owner.user_id, "on-duty must stay on the owner, who is not the closed shift's operator"


# --- 5: failure ordering --------------------------------------------------


def test_failed_end_shift_leaves_auth_and_on_duty_state_intact(temp_directory: str) -> None:
    from server.schemas import SessionRecord
    from server.time_utils import current_utc_time

    main_module = create_env(Path(temp_directory) / "end-shift-failure.sqlite3")
    lalaine = create_cashier(main_module, "lalaine.failure")
    token = login(main_module, "lalaine.failure", "lalaine.failure-pass")

    opened = main_module.open_shift(main_module.ShiftOpenRequest(opening_cash=Decimal("0.00")), lalaine)

    now = current_utc_time()
    main_module.session_repository.create_session(
        SessionRecord(
            session_id="session-failure-pending",
            pc_name="PC-FAILURE",
            status="settlement_pending",
            started_at=now,
            scheduled_end_at=now,
            ended_at=now,
            original_seconds=0,
            total_seconds=0,
            created_at=now,
            total_amount=Decimal("5.00"),
            session_mode="open_time",
            ended_reason="cashier_test",
            settlement_pending=True,
            created_by_user_id=lalaine.user_id,
        )
    )

    try:
        main_module.close_shift(
            opened.shift.shift_id, main_module.ShiftCloseRequest(actual_cash=Decimal("0.00")), Response(), lalaine
        )
    except HTTPException as error:
        assert error.status_code == 409
    else:
        raise AssertionError("Closing with an unresolved settlement must be rejected before finalizing")

    assert token_is_valid(main_module, token) is True, "a failed close must leave the operator logged in"
    on_duty = main_module.operator_state_repository.get_current(main_module.user_repository.get_user)
    assert on_duty.operator is not None and on_duty.operator.user_id == lalaine.user_id
    still_open = main_module.shift_repository.get_open_shift_for_user(lalaine.user_id)
    assert still_open is not None and still_open.status == "open"


def main() -> None:
    tests = [
        test_ordinary_sign_out_leaves_shift_and_totals_untouched,
        test_operator_logs_back_in_resumes_same_shift_no_duplicate,
        test_end_shift_closes_and_preserves_frozen_totals,
        test_end_shift_revokes_every_session_and_clears_cookie,
        test_operator_state_cleared_when_it_points_to_the_closed_operator,
        test_other_operator_state_and_sessions_untouched_by_end_shift,
        test_force_close_by_owner_does_not_touch_owners_own_cookie_or_session,
        test_failed_end_shift_leaves_auth_and_on_duty_state_intact,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
    print("Stage 4 shift-lifecycle smoke tests passed.")


if __name__ == "__main__":
    main()

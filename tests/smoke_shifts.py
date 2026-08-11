import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException, Response


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "shifts.sqlite3")

        from server.database import get_connection, initialize_database
        from server.main import (
            close_shift,
            get_shift,
            list_shifts,
            open_shift,
            record_payment,
            session_repository,
            shift_repository,
            void_payment,
        )
        from server.schemas import (
            PaymentCreateRequest,
            SessionRecord,
            ShiftCloseRequest,
            ShiftOpenRequest,
            UserCreateRequest,
            VoidPaymentRequest,
        )
        from server.time_utils import current_utc_time
        from server.user_repository import UserRepository

        initialize_database()
        user_repository = UserRepository()
        owner = user_repository.create_user(
            UserCreateRequest(
                username="owner",
                display_name="Owner",
                password="owner-pass",
                role="owner",
            )
        )
        cashier = user_repository.create_user(
            UserCreateRequest(
                username="cashier",
                display_name="Cashier",
                password="cashier-pass",
                role="cashier",
            )
        )
        other_cashier = user_repository.create_user(
            UserCreateRequest(
                username="other",
                display_name="Other Cashier",
                password="other-pass",
                role="cashier",
            )
        )
        owner_user = user_repository.get_user(owner.user_id)
        cashier_user = user_repository.get_user(cashier.user_id)
        other_user = user_repository.get_user(other_cashier.user_id)
        assert owner_user is not None
        assert cashier_user is not None
        assert other_user is not None

        now = current_utc_time()
        session_repository.create_session(
            SessionRecord(
                session_id="shift-session",
                pc_name="PC-SHIFT",
                status="active",
                started_at=now,
                scheduled_end_at=now,
                ended_at=None,
                original_seconds=300,
                added_seconds=0,
                total_seconds=300,
                amount_paid=None,
                ended_reason=None,
                created_at=now,
                base_amount=Decimal("150.00"),
                total_amount=Decimal("150.00"),
                created_by_user_id=cashier.user_id,
            )
        )

        paid_without_shift = record_payment(
            "shift-session",
            PaymentCreateRequest(
                payment_method="cash",
                amount=Decimal("10.00"),
                cash_received=Decimal("10.00"),
            ),
            cashier_user,
        )
        no_shift_payment = paid_without_shift.payments[-1]
        assert no_shift_payment.shift_id is None
        assert no_shift_payment.recorded_by_user_id == cashier.user_id

        opened = open_shift(ShiftOpenRequest(opening_cash=Decimal("1000.00")), cashier_user)
        assert opened.shift.status == "open"
        assert opened.shift.opening_cash == Decimal("1000.00")

        try:
            open_shift(ShiftOpenRequest(opening_cash=Decimal("10.00")), cashier_user)
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Duplicate open shift should be rejected")

        paid_cash = record_payment(
            "shift-session",
            PaymentCreateRequest(
                payment_method="cash",
                amount=Decimal("100.00"),
                cash_received=Decimal("500.00"),
            ),
            cashier_user,
        )
        cash_payment = paid_cash.payments[-1]
        assert cash_payment.shift_id == opened.shift.shift_id
        assert cash_payment.change_given == Decimal("400.00")

        paid_digital = record_payment(
            "shift-session",
            PaymentCreateRequest(
                payment_method="gcash",
                amount=Decimal("40.00"),
                reference_number="GCASH-1",
                idempotency_key="shift-digital-payment-key",
            ),
            cashier_user,
        )
        assert paid_digital.payments[-1].shift_id == opened.shift.shift_id
        duplicate_digital = record_payment(
            "shift-session",
            PaymentCreateRequest(
                payment_method="gcash",
                amount=Decimal("40.00"),
                reference_number="GCASH-1",
                idempotency_key="shift-digital-payment-key",
            ),
            cashier_user,
        )
        assert len(duplicate_digital.payments) == len(paid_digital.payments)
        assert duplicate_digital.payments[-1].payment_id == paid_digital.payments[-1].payment_id

        current_totals = shift_repository.get_shift(opened.shift.shift_id, cashier_user)
        assert current_totals is not None
        assert current_totals.totals.cash_payments == Decimal("100.00")
        assert current_totals.totals.gcash_payments == Decimal("40.00")
        assert current_totals.totals.expected_cash == Decimal("1100.00")
        assert current_totals.totals.payment_count == 2
        assert current_totals.totals.sessions_handled == 1

        voided = void_payment(
            cash_payment.payment_id,
            VoidPaymentRequest(reason="Cash count correction"),
            owner_user,
        )
        voided_cash_payment = next(
            payment for payment in voided.payments if payment.payment_id == cash_payment.payment_id
        )
        assert voided_cash_payment.status == "voided"
        after_void = shift_repository.get_shift(opened.shift.shift_id, cashier_user)
        assert after_void is not None
        assert after_void.totals.expected_cash == Decimal("1000.00")
        assert after_void.totals.voided_payments == Decimal("100.00")

        pending_session = SessionRecord(
            session_id="pending-open-time",
            pc_name="PC-PENDING",
            status="settlement_pending",
            started_at=now,
            scheduled_end_at=now,
            ended_at=now,
            original_seconds=0,
            added_seconds=0,
            total_seconds=0,
            amount_paid=None,
            ended_reason="cashier_test",
            created_at=now,
            total_amount=Decimal("5.00"),
            session_mode="open_time",
            settlement_pending=True,
            created_by_user_id=cashier.user_id,
        )
        session_repository.create_session(pending_session)
        try:
            close_shift(
                opened.shift.shift_id,
                ShiftCloseRequest(actual_cash=Decimal("1000.00")),
                Response(),
                cashier_user,
            )
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Cashier close should be blocked by unresolved items")

        closed = close_shift(
            opened.shift.shift_id,
            ShiftCloseRequest(
                actual_cash=Decimal("1005.00"),
                closing_notes="Manager closed with known pending settlement",
                force_close_reason="Cashier left before settlement",
            ),
            Response(),
            owner_user,
        )
        assert closed.shift.status == "closed"
        assert closed.shift.expected_cash == Decimal("1000.00")
        assert closed.shift.actual_cash == Decimal("1005.00")
        assert closed.shift.difference == Decimal("5.00")
        assert closed.shift.closed_by_user_id == owner.user_id
        assert closed.shift.settlement_pending_sessions == 1

        try:
            close_shift(
                opened.shift.shift_id,
                ShiftCloseRequest(actual_cash=Decimal("1000.00")),
                Response(),
                owner_user,
            )
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Closed shift should not close twice")

        try:
            get_shift(opened.shift.shift_id, other_user)
        except HTTPException as error:
            assert error.status_code == 403
        else:
            raise AssertionError("Cashier should not view another cashier shift")

        assert any(shift.shift_id == opened.shift.shift_id for shift in list_shifts(owner_user))
        owner_view = get_shift(opened.shift.shift_id, owner_user)
        assert owner_view.shift.gcash_payments == Decimal("40.00")

        with get_connection() as connection:
            payment_row = connection.execute(
                "SELECT shift_id, recorded_by_user_id FROM payments WHERE payment_id = ?",
                (cash_payment.payment_id,),
            ).fetchone()
            no_shift_row = connection.execute(
                "SELECT shift_id, recorded_by_user_id FROM payments WHERE payment_id = ?",
                (no_shift_payment.payment_id,),
            ).fetchone()
        assert payment_row["shift_id"] == opened.shift.shift_id
        assert payment_row["recorded_by_user_id"] == cashier.user_id
        assert no_shift_row["shift_id"] is None
        assert no_shift_row["recorded_by_user_id"] == cashier.user_id

        print("Shift smoke test passed.")


if __name__ == "__main__":
    main()

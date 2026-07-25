import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from server.database import get_connection
from server.payment_repository import row_to_payment
from server.schemas import (
    CashierShiftDetail,
    CashierShiftRecord,
    PaymentRecord,
    ShiftCloseRequest,
    ShiftOpenRequest,
    ShiftTotals,
    UserRecord,
)
from server.time_utils import current_utc_time


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def decimal_to_storage(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(money(value))


def decimal_from_row(row: Any, key: str, default: str = "0.00") -> Decimal:
    value = row[key]
    return Decimal(value if value is not None else default)


def row_to_shift(row: Any) -> CashierShiftRecord:
    return CashierShiftRecord(
        shift_id=row["shift_id"],
        user_id=row["user_id"],
        cashier_name=row["cashier_name"] if "cashier_name" in row.keys() else None,
        status=row["status"],
        opened_at=datetime.fromisoformat(row["opened_at"]),
        closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
        opening_cash=decimal_from_row(row, "opening_cash"),
        expected_cash=(
            Decimal(row["expected_cash"]) if row["expected_cash"] is not None else None
        ),
        actual_cash=Decimal(row["actual_cash"]) if row["actual_cash"] is not None else None,
        difference=Decimal(row["difference"]) if row["difference"] is not None else None,
        closing_notes=row["closing_notes"],
        force_close_reason=row["force_close_reason"],
        opened_by_user_id=row["opened_by_user_id"],
        closed_by_user_id=row["closed_by_user_id"],
        gross_session_sales=decimal_from_row(row, "gross_session_sales"),
        cash_payments=decimal_from_row(row, "cash_payments"),
        gcash_payments=decimal_from_row(row, "gcash_payments"),
        maya_payments=decimal_from_row(row, "maya_payments"),
        other_payments=decimal_from_row(row, "other_payments"),
        voided_payments=decimal_from_row(row, "voided_payments"),
        payment_count=row["payment_count"],
        sessions_handled=row["sessions_handled"],
        outstanding_unsettled_balance=decimal_from_row(row, "outstanding_unsettled_balance"),
        review_required_payments=row["review_required_payments"],
        settlement_pending_sessions=row["settlement_pending_sessions"],
    )


def row_to_totals(row: Any, opening_cash: Decimal) -> ShiftTotals:
    cash_payments = decimal_from_row(row, "cash_payments")
    cash_received = decimal_from_row(row, "cash_received")
    cash_change = decimal_from_row(row, "cash_change")
    return ShiftTotals(
        expected_cash=money(opening_cash + cash_received - cash_change),
        gross_session_sales=decimal_from_row(row, "gross_session_sales"),
        cash_payments=cash_payments,
        gcash_payments=decimal_from_row(row, "gcash_payments"),
        maya_payments=decimal_from_row(row, "maya_payments"),
        other_payments=decimal_from_row(row, "other_payments"),
        voided_payments=decimal_from_row(row, "voided_payments"),
        payment_count=row["payment_count"] or 0,
        sessions_handled=row["sessions_handled"] or 0,
        outstanding_unsettled_balance=Decimal("0.00"),
        review_required_payments=0,
        settlement_pending_sessions=0,
    )


class ShiftRepository:
    """Persistence and drawer reconciliation rules for cashier shifts."""

    def open_shift(
        self,
        request: ShiftOpenRequest,
        user: UserRecord,
    ) -> CashierShiftDetail:
        now = current_utc_time()
        shift_id = str(uuid4())
        with get_connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO cashier_shifts (
                        shift_id,
                        user_id,
                        status,
                        opened_at,
                        opening_cash,
                        opened_by_user_id
                    )
                    VALUES (?, ?, 'open', ?, ?, ?)
                    """,
                    (
                        shift_id,
                        user.user_id,
                        now.isoformat(),
                        decimal_to_storage(request.opening_cash),
                        user.user_id,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(
                    status_code=409,
                    detail="This user already has an open shift",
                ) from error

        detail = self.get_shift(shift_id, viewer=user)
        if detail is None:
            raise HTTPException(status_code=500, detail="Shift could not be loaded")
        return detail

    def get_open_shift_for_user(self, user_id: str) -> CashierShiftRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT cashier_shifts.*, users.display_name AS cashier_name
                FROM cashier_shifts
                LEFT JOIN users ON users.user_id = cashier_shifts.user_id
                WHERE cashier_shifts.user_id = ?
                  AND cashier_shifts.status = 'open'
                ORDER BY cashier_shifts.opened_at DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
        return row_to_shift(row) if row is not None else None

    def require_payment_shift(self, user: UserRecord) -> str:
        shift = self.get_open_shift_for_user(user.user_id)
        if shift is None:
            raise HTTPException(
                status_code=409,
                detail="Start a cashier shift before recording payments",
            )
        return shift.shift_id

    def list_shifts(self, viewer: UserRecord) -> list[CashierShiftRecord]:
        parameters: tuple[str, ...] = ()
        where_clause = ""
        if viewer.role == "cashier":
            where_clause = "WHERE cashier_shifts.user_id = ?"
            parameters = (viewer.user_id,)

        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT cashier_shifts.*, users.display_name AS cashier_name
                FROM cashier_shifts
                LEFT JOIN users ON users.user_id = cashier_shifts.user_id
                {where_clause}
                ORDER BY cashier_shifts.opened_at DESC
                """,
                parameters,
            ).fetchall()
        return [row_to_shift(row) for row in rows]

    def get_shift(
        self,
        shift_id: str,
        viewer: UserRecord,
    ) -> CashierShiftDetail | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT cashier_shifts.*, users.display_name AS cashier_name
                FROM cashier_shifts
                LEFT JOIN users ON users.user_id = cashier_shifts.user_id
                WHERE cashier_shifts.shift_id = ?
                """,
                (shift_id,),
            ).fetchone()
            if row is None:
                return None

            shift = row_to_shift(row)
            if viewer.role == "cashier" and shift.user_id != viewer.user_id:
                raise HTTPException(status_code=403, detail="Cannot view another cashier's shift")

            payments = connection.execute(
                """
                SELECT * FROM payments
                WHERE shift_id = ?
                ORDER BY created_at ASC
                """,
                (shift_id,),
            ).fetchall()

        totals = self.calculate_totals(shift)
        if shift.status == "closed":
            totals = ShiftTotals(
                expected_cash=shift.expected_cash or Decimal("0.00"),
                gross_session_sales=shift.gross_session_sales,
                cash_payments=shift.cash_payments,
                gcash_payments=shift.gcash_payments,
                maya_payments=shift.maya_payments,
                other_payments=shift.other_payments,
                voided_payments=shift.voided_payments,
                payment_count=shift.payment_count,
                sessions_handled=shift.sessions_handled,
                outstanding_unsettled_balance=shift.outstanding_unsettled_balance,
                review_required_payments=shift.review_required_payments,
                settlement_pending_sessions=shift.settlement_pending_sessions,
            )
        return CashierShiftDetail(
            shift=shift,
            totals=totals,
            payments=[row_to_payment(payment) for payment in payments],
        )

    def calculate_totals(self, shift: CashierShiftRecord) -> ShiftTotals:
        with get_connection() as connection:
            totals_row = connection.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN status = 'completed' THEN amount ELSE 0 END), 0) AS gross_session_sales,
                    COALESCE(SUM(CASE WHEN status = 'completed' AND payment_method = 'cash' THEN amount ELSE 0 END), 0) AS cash_payments,
                    COALESCE(SUM(CASE WHEN status = 'completed' AND payment_method = 'gcash' THEN amount ELSE 0 END), 0) AS gcash_payments,
                    COALESCE(SUM(CASE WHEN status = 'completed' AND payment_method = 'maya' THEN amount ELSE 0 END), 0) AS maya_payments,
                    COALESCE(SUM(CASE WHEN status = 'completed' AND payment_method = 'other' THEN amount ELSE 0 END), 0) AS other_payments,
                    COALESCE(SUM(CASE WHEN status = 'completed' AND payment_method = 'cash' THEN COALESCE(cash_received, amount) ELSE 0 END), 0) AS cash_received,
                    COALESCE(SUM(CASE WHEN status = 'completed' AND payment_method = 'cash' THEN COALESCE(change_given, '0.00') ELSE 0 END), 0) AS cash_change,
                    COALESCE(SUM(CASE WHEN status = 'voided' THEN amount ELSE 0 END), 0) AS voided_payments,
                    COUNT(*) AS payment_count,
                    COUNT(DISTINCT session_id) AS sessions_handled
                FROM payments
                WHERE shift_id = ?
                """,
                (shift.shift_id,),
            ).fetchone()
            settlement_row = connection.execute(
                """
                SELECT
                    COUNT(*) AS settlement_pending_sessions,
                    COALESCE(SUM(total_amount), 0) AS outstanding_unsettled_balance
                FROM sessions
                WHERE status = 'settlement_pending'
                  AND settlement_pending = 1
                  AND created_by_user_id = ?
                """,
                (shift.user_id,),
            ).fetchone()
            review_row = connection.execute(
                """
                SELECT COUNT(*) AS review_required_payments
                FROM payments
                WHERE review_required = 1
                  AND status = 'completed'
                  AND recorded_by_user_id = ?
                """,
                (shift.user_id,),
            ).fetchone()

        totals = row_to_totals(totals_row, shift.opening_cash)
        return totals.model_copy(
            update={
                "outstanding_unsettled_balance": money(
                    Decimal(str(settlement_row["outstanding_unsettled_balance"] or "0.00"))
                ),
                "review_required_payments": review_row["review_required_payments"] or 0,
                "settlement_pending_sessions": settlement_row["settlement_pending_sessions"] or 0,
            }
        )

    def close_shift(
        self,
        shift_id: str,
        request: ShiftCloseRequest,
        actor: UserRecord,
    ) -> CashierShiftDetail:
        detail = self.get_shift(shift_id, viewer=actor)
        if detail is None:
            raise HTTPException(status_code=404, detail="Shift not found")

        shift = detail.shift
        if shift.status == "closed":
            raise HTTPException(status_code=409, detail="Shift is already closed")
        if actor.role == "cashier" and shift.user_id != actor.user_id:
            raise HTTPException(status_code=403, detail="Cannot close another cashier's shift")

        force_reason = (request.force_close_reason or "").strip() or None
        if actor.role in {"owner", "admin"} and shift.user_id != actor.user_id and force_reason is None:
            raise HTTPException(
                status_code=400,
                detail="A force-close reason is required for another user's shift",
            )

        totals = self.calculate_totals(shift)
        if actor.role == "cashier" and (
            totals.settlement_pending_sessions > 0 or totals.review_required_payments > 0
        ):
            raise HTTPException(
                status_code=409,
                detail="Resolve pending settlements or review-required payments before closing shift",
            )

        actual_cash = money(request.actual_cash)
        difference = money(actual_cash - totals.expected_cash)
        closed_at = current_utc_time()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE cashier_shifts
                SET
                    status = 'closed',
                    closed_at = ?,
                    expected_cash = ?,
                    actual_cash = ?,
                    difference = ?,
                    closing_notes = ?,
                    force_close_reason = ?,
                    closed_by_user_id = ?,
                    gross_session_sales = ?,
                    cash_payments = ?,
                    gcash_payments = ?,
                    maya_payments = ?,
                    other_payments = ?,
                    voided_payments = ?,
                    payment_count = ?,
                    sessions_handled = ?,
                    outstanding_unsettled_balance = ?,
                    review_required_payments = ?,
                    settlement_pending_sessions = ?
                WHERE shift_id = ?
                  AND status = 'open'
                """,
                (
                    closed_at.isoformat(),
                    decimal_to_storage(totals.expected_cash),
                    decimal_to_storage(actual_cash),
                    decimal_to_storage(difference),
                    request.closing_notes,
                    force_reason,
                    actor.user_id,
                    decimal_to_storage(totals.gross_session_sales),
                    decimal_to_storage(totals.cash_payments),
                    decimal_to_storage(totals.gcash_payments),
                    decimal_to_storage(totals.maya_payments),
                    decimal_to_storage(totals.other_payments),
                    decimal_to_storage(totals.voided_payments),
                    totals.payment_count,
                    totals.sessions_handled,
                    decimal_to_storage(totals.outstanding_unsettled_balance),
                    totals.review_required_payments,
                    totals.settlement_pending_sessions,
                    shift_id,
                ),
            )

        closed_detail = self.get_shift(shift_id, viewer=actor)
        if closed_detail is None:
            raise HTTPException(status_code=500, detail="Closed shift could not be loaded")
        return closed_detail

    def list_shift_payments(self, shift_id: str) -> list[PaymentRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM payments WHERE shift_id = ? ORDER BY created_at ASC",
                (shift_id,),
            ).fetchall()
        return [row_to_payment(row) for row in rows]

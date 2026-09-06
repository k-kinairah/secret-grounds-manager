"""Net Income / bills persistence.

Two tables (see server/database.py):
  * shop_bills          -- recurring expense definitions. kind='fixed'
                           carries its own amount every month; kind='variable'
                           is a recurring name whose amount is typed per month.
  * shop_bill_amounts   -- either a per-month amount for a bill (bill_id set)
                           or a standalone one-off expense for a month
                           (bill_id NULL, label set). Month key is 'YYYY-MM'.

A bill applies to month M if it was created in M or earlier and was not
archived before M. Month comparison is on the stored (UTC) timestamp's
'YYYY-MM'; a few hours' slop at a month boundary does not matter for
expense tracking.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException

from server.database import get_connection
from server.schemas import (
    FinancesMonthSummary,
    MonthlyBillLine,
    OneOffExpenseRequest,
    ShopBillCreateRequest,
    ShopBillRecord,
)
from server.time_utils import current_utc_time


def _money(value: object) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _bill_from_row(row) -> ShopBillRecord:
    return ShopBillRecord(
        bill_id=row["bill_id"],
        name=row["name"],
        kind=row["kind"],
        fixed_amount=_money(row["fixed_amount"]) if row["fixed_amount"] is not None else None,
        created_at=row["created_at"],
        archived_at=row["archived_at"],
    )


class FinancesRepository:
    def list_bills(self, include_archived: bool = True) -> list[ShopBillRecord]:
        query = "SELECT * FROM shop_bills"
        if not include_archived:
            query += " WHERE archived_at IS NULL"
        query += " ORDER BY archived_at IS NOT NULL, kind, LOWER(name)"
        with get_connection() as connection:
            rows = connection.execute(query).fetchall()
        return [_bill_from_row(row) for row in rows]

    def create_bill(self, request: ShopBillCreateRequest, actor_user_id: str | None) -> ShopBillRecord:
        now = current_utc_time().isoformat()
        bill_id = str(uuid4())
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO shop_bills (
                    bill_id, name, kind, fixed_amount, created_by_user_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    bill_id,
                    request.name,
                    request.kind,
                    str(request.fixed_amount) if request.fixed_amount is not None else None,
                    actor_user_id,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM shop_bills WHERE bill_id = ?", (bill_id,)
            ).fetchone()
        return _bill_from_row(row)

    def archive_bill(self, bill_id: str) -> ShopBillRecord:
        now = current_utc_time().isoformat()
        with get_connection() as connection:
            cursor = connection.execute(
                "UPDATE shop_bills SET archived_at = ? WHERE bill_id = ? AND archived_at IS NULL",
                (now, bill_id),
            )
            row = connection.execute(
                "SELECT * FROM shop_bills WHERE bill_id = ?", (bill_id,)
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Bill not found.")
        _ = cursor
        return _bill_from_row(row)

    def set_bill_amount(
        self, bill_id: str, month: str, amount: Decimal, actor_user_id: str | None
    ) -> None:
        now = current_utc_time().isoformat()
        with get_connection() as connection:
            bill = connection.execute(
                "SELECT bill_id FROM shop_bills WHERE bill_id = ?", (bill_id,)
            ).fetchone()
            if bill is None:
                raise HTTPException(status_code=404, detail="Bill not found.")
            existing = connection.execute(
                """
                SELECT amount_id FROM shop_bill_amounts
                WHERE bill_id = ? AND month = ? AND voided_at IS NULL
                """,
                (bill_id, month),
            ).fetchone()
            if existing is not None:
                connection.execute(
                    "UPDATE shop_bill_amounts SET amount = ?, entered_by_user_id = ?, entered_at = ? "
                    "WHERE amount_id = ?",
                    (str(amount), actor_user_id, now, existing["amount_id"]),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO shop_bill_amounts (
                        amount_id, bill_id, label, month, amount, entered_by_user_id, entered_at
                    )
                    VALUES (?, ?, NULL, ?, ?, ?, ?)
                    """,
                    (str(uuid4()), bill_id, month, str(amount), actor_user_id, now),
                )

    def clear_bill_amount(self, bill_id: str, month: str) -> None:
        now = current_utc_time().isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE shop_bill_amounts SET voided_at = ?
                WHERE bill_id = ? AND month = ? AND voided_at IS NULL
                """,
                (now, bill_id, month),
            )

    def add_one_off(self, request: OneOffExpenseRequest, actor_user_id: str | None) -> None:
        now = current_utc_time().isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO shop_bill_amounts (
                    amount_id, bill_id, label, month, amount, entered_by_user_id, entered_at
                )
                VALUES (?, NULL, ?, ?, ?, ?, ?)
                """,
                (str(uuid4()), request.label, request.month, str(request.amount), actor_user_id, now),
            )

    def void_one_off(self, amount_id: str) -> None:
        now = current_utc_time().isoformat()
        with get_connection() as connection:
            row = connection.execute(
                "SELECT amount_id FROM shop_bill_amounts "
                "WHERE amount_id = ? AND bill_id IS NULL AND voided_at IS NULL",
                (amount_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="One-off expense not found.")
            connection.execute(
                "UPDATE shop_bill_amounts SET voided_at = ? WHERE amount_id = ?",
                (now, amount_id),
            )

    def resolve_month(self, month: str, income: Decimal) -> FinancesMonthSummary:
        with get_connection() as connection:
            bills = connection.execute(
                """
                SELECT * FROM shop_bills
                WHERE substr(created_at, 1, 7) <= ?
                  AND (archived_at IS NULL OR substr(archived_at, 1, 7) >= ?)
                ORDER BY kind, LOWER(name)
                """,
                (month, month),
            ).fetchall()
            amount_rows = connection.execute(
                """
                SELECT amount_id, bill_id, label, amount FROM shop_bill_amounts
                WHERE month = ? AND voided_at IS NULL
                """,
                (month,),
            ).fetchall()

        by_bill = {row["bill_id"]: row for row in amount_rows if row["bill_id"] is not None}
        one_offs = [row for row in amount_rows if row["bill_id"] is None]

        lines: list[MonthlyBillLine] = []
        total = Decimal("0.00")

        for bill in bills:
            entry = by_bill.get(bill["bill_id"])
            if bill["kind"] == "fixed":
                amount = _money(entry["amount"]) if entry is not None else _money(bill["fixed_amount"])
                lines.append(
                    MonthlyBillLine(
                        source="fixed",
                        bill_id=bill["bill_id"],
                        amount_id=entry["amount_id"] if entry is not None else None,
                        label=bill["name"],
                        amount=amount,
                        applied=True,
                    )
                )
                total += amount
            else:
                amount = _money(entry["amount"]) if entry is not None else None
                lines.append(
                    MonthlyBillLine(
                        source="variable",
                        bill_id=bill["bill_id"],
                        amount_id=entry["amount_id"] if entry is not None else None,
                        label=bill["name"],
                        amount=amount,
                        applied=amount is not None,
                    )
                )
                if amount is not None:
                    total += amount

        for row in sorted(one_offs, key=lambda item: item["label"].lower()):
            amount = _money(row["amount"])
            lines.append(
                MonthlyBillLine(
                    source="one_off",
                    bill_id=None,
                    amount_id=row["amount_id"],
                    label=row["label"],
                    amount=amount,
                    applied=True,
                )
            )
            total += amount

        total = total.quantize(Decimal("0.01"))
        return FinancesMonthSummary(
            month=month,
            income=income,
            total_bills=total,
            net_income=(income - total).quantize(Decimal("0.01")),
            lines=lines,
        )

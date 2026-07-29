from datetime import datetime
from decimal import Decimal
from typing import Any

from server.database import get_connection
from server.schemas import PaymentRecord


def decimal_to_storage(value: Decimal | None) -> str | None:
    if value is None:
        return None

    return str(value.quantize(Decimal("0.01")))


def row_to_payment(row: Any) -> PaymentRecord:
    keys = set(row.keys())
    return PaymentRecord(
        payment_id=row["payment_id"],
        session_id=row["session_id"],
        amount=Decimal(row["amount"]),
        payment_method=row["payment_method"],
        cash_received=(
            Decimal(row["cash_received"]) if row["cash_received"] is not None else None
        ),
        change_given=(
            Decimal(row["change_given"]) if row["change_given"] is not None else None
        ),
        reference_number=row["reference_number"],
        notes=row["notes"],
        action_type=row["action_type"],
        related_command_id=row["related_command_id"],
        review_required=bool(row["review_required"]),
        shift_id=row["shift_id"],
        recorded_by_user_id=row["recorded_by_user_id"],
        idempotency_key=row["idempotency_key"] if "idempotency_key" in keys else None,
        voided_by_user_id=row["voided_by_user_id"],
        voided_at=(
            datetime.fromisoformat(row["voided_at"])
            if row["voided_at"] is not None
            else None
        ),
        void_reason=row["void_reason"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class PaymentRepository:
    """SQLite persistence boundary for auditable session payments."""

    def create_payment(self, payment: PaymentRecord) -> PaymentRecord:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO payments (
                    payment_id,
                    session_id,
                    amount,
                    payment_method,
                    cash_received,
                    change_given,
                    reference_number,
                    notes,
                    action_type,
                    related_command_id,
                    review_required,
                    shift_id,
                    recorded_by_user_id,
                    idempotency_key,
                    voided_by_user_id,
                    voided_at,
                    void_reason,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payment.payment_id,
                    payment.session_id,
                    decimal_to_storage(payment.amount),
                    payment.payment_method,
                    decimal_to_storage(payment.cash_received),
                    decimal_to_storage(payment.change_given),
                    payment.reference_number,
                    payment.notes,
                    payment.action_type,
                    payment.related_command_id,
                    1 if payment.review_required else 0,
                    payment.shift_id,
                    payment.recorded_by_user_id,
                    payment.idempotency_key,
                    payment.voided_by_user_id,
                    payment.voided_at.isoformat() if payment.voided_at is not None else None,
                    payment.void_reason,
                    payment.status,
                    payment.created_at.isoformat(),
                ),
            )

        return payment

    def get_payment_by_idempotency_key(self, idempotency_key: str) -> PaymentRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM payments WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()

        return row_to_payment(row) if row is not None else None

    def get_payment(self, payment_id: str) -> PaymentRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM payments WHERE payment_id = ?",
                (payment_id,),
            ).fetchone()

        return row_to_payment(row) if row is not None else None

    def list_payments(self, session_id: str) -> list[PaymentRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM payments
                WHERE session_id = ?
                ORDER BY created_at ASC
                """,
                (session_id,),
            ).fetchall()

        return [row_to_payment(row) for row in rows]

    def void_payment(
        self,
        payment_id: str,
        voided_by_user_id: str | None,
        reason: str,
    ) -> PaymentRecord | None:
        existing = self.get_payment(payment_id)
        if existing is None:
            return None

        from server.time_utils import current_utc_time

        now = current_utc_time()
        updated = existing.model_copy(
            update={
                "status": "voided",
                "voided_by_user_id": voided_by_user_id,
                "voided_at": now,
                "void_reason": reason,
            }
        )
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE payments
                SET status = 'voided', voided_by_user_id = ?, voided_at = ?, void_reason = ?
                WHERE payment_id = ?
                """,
                (voided_by_user_id, now.isoformat(), reason, payment_id),
            )

        return updated

    def mark_review_required(
        self,
        payment_id: str,
        related_command_id: str | None = None,
    ) -> PaymentRecord | None:
        existing = self.get_payment(payment_id)
        if existing is None:
            return None

        updated = existing.model_copy(
            update={
                "review_required": True,
                "related_command_id": related_command_id or existing.related_command_id,
            }
        )
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE payments
                SET review_required = 1, related_command_id = ?
                WHERE payment_id = ?
                """,
                (updated.related_command_id, updated.payment_id),
            )

        return updated

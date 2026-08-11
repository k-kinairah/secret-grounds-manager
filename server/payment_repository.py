import sqlite3
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
        pc_name_snapshot=row["pc_name_snapshot"] if "pc_name_snapshot" in keys else None,
        income_group_id_snapshot=(
            row["income_group_id_snapshot"] if "income_group_id_snapshot" in keys else None
        ),
        income_group_name_snapshot=(
            row["income_group_name_snapshot"] if "income_group_name_snapshot" in keys else None
        ),
        attribution_status=(
            row["attribution_status"] if "attribution_status" in keys else "assigned"
        ),
        attribution_assigned_at=(
            datetime.fromisoformat(row["attribution_assigned_at"])
            if "attribution_assigned_at" in keys and row["attribution_assigned_at"] is not None
            else None
        ),
        attribution_session_id=(
            row["attribution_session_id"] if "attribution_session_id" in keys else None
        ),
    )


class PaymentRepository:
    """SQLite persistence boundary for auditable session payments."""

    def create_payment(
        self,
        payment: PaymentRecord,
        connection: sqlite3.Connection | None = None,
    ) -> PaymentRecord:
        """Inserts the payment row. When `connection` is supplied, the
        insert joins the caller's transaction (no commit/close here) so a
        payment and whatever it funds can be made atomic together -- see
        MemberRepository.purchase()."""

        def _insert(active_connection: sqlite3.Connection) -> None:
            active_connection.execute(
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
                    created_at,
                    pc_name_snapshot,
                    income_group_id_snapshot,
                    income_group_name_snapshot,
                    attribution_status,
                    attribution_assigned_at,
                    attribution_session_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    payment.pc_name_snapshot,
                    payment.income_group_id_snapshot,
                    payment.income_group_name_snapshot,
                    payment.attribution_status,
                    (
                        payment.attribution_assigned_at.isoformat()
                        if payment.attribution_assigned_at is not None
                        else None
                    ),
                    payment.attribution_session_id,
                ),
            )

        if connection is not None:
            _insert(connection)
        else:
            with get_connection() as owned_connection:
                _insert(owned_connection)

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

    def assign_pending_known_pc_purchases_for_member(
        self,
        member_id: str,
        include_vip: bool,
        pc_name: str,
        income_group_id_snapshot: str | None,
        income_group_name_snapshot: str | None,
        attribution_status: str,
        attribution_assigned_at: datetime | None,
        attribution_session_id: str | None,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """Bulk-resolves this member's still-'PC not yet known' pending,
        completed purchases (pc_name_snapshot IS NULL) whose balance type
        is eligible for pc_name. Never touches a row that already has a
        pc_name_snapshot (that row belongs to the PC-configuration
        resolution path instead, not a member-session sweep) or a row
        that is not both 'pending' and 'completed'. See
        income_attribution.sweep_pending_purchases_for_member for the
        eligibility/attribution decision this persists."""
        balance_types = ("standard", "vip") if include_vip else ("standard",)
        placeholders = ", ".join("?" for _ in balance_types)
        query = f"""
            UPDATE payments
            SET pc_name_snapshot = ?,
                income_group_id_snapshot = ?,
                income_group_name_snapshot = ?,
                attribution_status = ?,
                attribution_assigned_at = ?,
                attribution_session_id = ?
            WHERE attribution_status = 'pending'
              AND status = 'completed'
              AND pc_name_snapshot IS NULL
              AND payment_id IN (
                  SELECT p.payment_id FROM payments p
                  JOIN member_time_transactions mt ON mt.related_payment_id = p.payment_id
                  WHERE mt.member_id = ?
                    AND mt.transaction_type IN ('membership_purchase', 'promo_topup')
                    AND mt.balance_type IN ({placeholders})
              )
        """
        params = (
            pc_name,
            income_group_id_snapshot,
            income_group_name_snapshot,
            attribution_status,
            attribution_assigned_at.isoformat() if attribution_assigned_at is not None else None,
            attribution_session_id,
            member_id,
            *balance_types,
        )

        def _run(active_connection: sqlite3.Connection) -> int:
            cursor = active_connection.execute(query, params)
            return cursor.rowcount

        if connection is not None:
            return _run(connection)
        with get_connection() as owned_connection:
            return _run(owned_connection)

    def assign_pending_payments_for_configured_pc(
        self,
        pc_name: str,
        income_group_id_snapshot: str,
        income_group_name_snapshot: str,
        attribution_assigned_at: datetime,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """Bulk-resolves completed, still-pending payments that already
        know pc_name (pc_name_snapshot = pc_name) but had no income group
        to assign at the time. Deliberately never touches pc_name_snapshot
        or attribution_session_id, and only ever matches rows where
        income_group_id_snapshot IS NULL, so an already-assigned payment
        (and its historical group) is never rewritten by a later PC
        reassignment."""
        query = """
            UPDATE payments
            SET income_group_id_snapshot = ?,
                income_group_name_snapshot = ?,
                attribution_status = 'assigned',
                attribution_assigned_at = ?
            WHERE attribution_status = 'pending'
              AND status = 'completed'
              AND pc_name_snapshot = ?
              AND income_group_id_snapshot IS NULL
        """
        params = (
            income_group_id_snapshot,
            income_group_name_snapshot,
            attribution_assigned_at.isoformat(),
            pc_name,
        )

        def _run(active_connection: sqlite3.Connection) -> int:
            cursor = active_connection.execute(query, params)
            return cursor.rowcount

        if connection is not None:
            return _run(connection)
        with get_connection() as owned_connection:
            return _run(owned_connection)

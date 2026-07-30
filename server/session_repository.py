from datetime import datetime
from decimal import Decimal
from typing import Any

from server.database import get_connection
from server.schemas import SessionEventRecord, SessionRecord


def datetime_to_storage(value: datetime | None) -> str | None:
    if value is None:
        return None

    return value.isoformat()


def decimal_to_storage(value: Decimal | None) -> str | None:
    if value is None:
        return None

    return str(value.quantize(Decimal("0.01")))


def row_to_session(row: Any) -> SessionRecord:
    return SessionRecord(
        session_id=row["session_id"],
        pc_name=row["pc_name"],
        status=row["status"],
        started_at=datetime.fromisoformat(row["started_at"]),
        scheduled_end_at=datetime.fromisoformat(row["scheduled_end_at"]),
        ended_at=(
            datetime.fromisoformat(row["ended_at"])
            if row["ended_at"] is not None
            else None
        ),
        original_seconds=row["original_seconds"],
        added_seconds=row["added_seconds"],
        total_seconds=row["total_seconds"],
        amount_paid=row["amount_paid"],
        ended_reason=row["ended_reason"],
        created_at=datetime.fromisoformat(row["created_at"]),
        rate_id=row["rate_id"],
        rate_name_snapshot=row["rate_name_snapshot"],
        base_amount=Decimal(row["base_amount"]),
        additional_amount=Decimal(row["additional_amount"]),
        total_amount=Decimal(row["total_amount"]),
        session_mode=row["session_mode"],
        actual_elapsed_seconds=row["actual_elapsed_seconds"],
        billed_seconds=row["billed_seconds"],
        final_computed_amount=(
            Decimal(row["final_computed_amount"])
            if row["final_computed_amount"] is not None
            else None
        ),
        metered_rate_price_snapshot=(
            Decimal(row["metered_rate_price_snapshot"])
            if row["metered_rate_price_snapshot"] is not None
            else None
        ),
        metered_rate_duration_seconds_snapshot=row[
            "metered_rate_duration_seconds_snapshot"
        ],
        settlement_pending=bool(row["settlement_pending"]),
        created_by_user_id=row["created_by_user_id"],
        ended_by_user_id=row["ended_by_user_id"],
        member_id=row["member_id"],
        member_code_snapshot=row["member_code_snapshot"],
        member_name_snapshot=row["member_name_snapshot"],
        member_balance_before_seconds=row["member_balance_before_seconds"],
        member_balance_after_seconds=row["member_balance_after_seconds"],
        grace_started_at=(
            datetime.fromisoformat(row["grace_started_at"])
            if "grace_started_at" in row.keys() and row["grace_started_at"] is not None
            else None
        ),
        shutdown_at=(
            datetime.fromisoformat(row["shutdown_at"])
            if "shutdown_at" in row.keys() and row["shutdown_at"] is not None
            else None
        ),
    )


def row_to_event(row: Any) -> SessionEventRecord:
    return SessionEventRecord(
        event_id=row["event_id"],
        session_id=row["session_id"],
        event_type=row["event_type"],
        seconds=row["seconds"],
        message=row["message"],
        created_at=datetime.fromisoformat(row["created_at"]),
        rate_id=row["rate_id"],
        rate_name_snapshot=row["rate_name_snapshot"],
        amount=Decimal(row["amount"]) if row["amount"] is not None else None,
        payment_id=row["payment_id"],
        payment_method=row["payment_method"],
        performed_by_user_id=row["performed_by_user_id"],
    )


class SessionRepository:
    """SQLite persistence boundary for sessions and their audit events."""

    def create_session(self, session: SessionRecord) -> SessionRecord:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id,
                    pc_name,
                    status,
                    started_at,
                    scheduled_end_at,
                    ended_at,
                    original_seconds,
                    added_seconds,
                    total_seconds,
                    amount_paid,
                    ended_reason,
                    created_at,
                    rate_id,
                    rate_name_snapshot,
                    base_amount,
                    additional_amount,
                    total_amount,
                    session_mode,
                    actual_elapsed_seconds,
                    billed_seconds,
                    final_computed_amount,
                    metered_rate_price_snapshot,
                    metered_rate_duration_seconds_snapshot,
                    settlement_pending,
                    created_by_user_id,
                    ended_by_user_id,
                    member_id,
                    member_code_snapshot,
                    member_name_snapshot,
                    member_balance_before_seconds,
                    member_balance_after_seconds,
                    grace_started_at,
                    shutdown_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.pc_name,
                    session.status,
                    datetime_to_storage(session.started_at),
                    datetime_to_storage(session.scheduled_end_at),
                    datetime_to_storage(session.ended_at),
                    session.original_seconds,
                    session.added_seconds,
                    session.total_seconds,
                    session.amount_paid,
                    session.ended_reason,
                    datetime_to_storage(session.created_at),
                    session.rate_id,
                    session.rate_name_snapshot,
                    decimal_to_storage(session.base_amount),
                    decimal_to_storage(session.additional_amount),
                    decimal_to_storage(session.total_amount),
                    session.session_mode,
                    session.actual_elapsed_seconds,
                    session.billed_seconds,
                    decimal_to_storage(session.final_computed_amount),
                    decimal_to_storage(session.metered_rate_price_snapshot),
                    session.metered_rate_duration_seconds_snapshot,
                    1 if session.settlement_pending else 0,
                    session.created_by_user_id,
                    session.ended_by_user_id,
                    session.member_id,
                    session.member_code_snapshot,
                    session.member_name_snapshot,
                    session.member_balance_before_seconds,
                    session.member_balance_after_seconds,
                    datetime_to_storage(session.grace_started_at),
                    datetime_to_storage(session.shutdown_at),
                ),
            )

        return session

    def add_event(self, event: SessionEventRecord) -> SessionEventRecord:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO session_events (
                    event_id,
                    session_id,
                    event_type,
                    seconds,
                    message,
                    created_at,
                    rate_id,
                    rate_name_snapshot,
                    amount,
                    payment_id,
                    payment_method,
                    performed_by_user_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.session_id,
                    event.event_type,
                    event.seconds,
                    event.message,
                    datetime_to_storage(event.created_at),
                    event.rate_id,
                    event.rate_name_snapshot,
                    decimal_to_storage(event.amount),
                    event.payment_id,
                    event.payment_method,
                    event.performed_by_user_id,
                ),
            )

        return event

    def get_session(self, session_id: str) -> SessionRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        if row is None:
            return None

        return row_to_session(row)

    def get_active_session_for_pc(self, pc_name: str) -> SessionRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM sessions
                WHERE pc_name = ? AND status IN ('active', 'grace_period', 'shutting_down', 'ending', 'settlement_pending')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (pc_name,),
            ).fetchone()

        if row is None:
            return None

        return row_to_session(row)

    def get_active_member_session(self, member_id: str) -> SessionRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM sessions
                WHERE member_id = ?
                  AND session_mode = 'member_balance'
                  AND status IN ('active', 'grace_period', 'shutting_down', 'ending')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (member_id,),
            ).fetchone()

        if row is None:
            return None

        return row_to_session(row)

    def list_active_sessions(self) -> list[SessionRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sessions
                WHERE status IN ('active', 'grace_period', 'shutting_down', 'ending', 'settlement_pending')
                ORDER BY created_at DESC
                """
            ).fetchall()

        return [row_to_session(row) for row in rows]

    def list_history(self) -> list[SessionRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC"
            ).fetchall()

        return [row_to_session(row) for row in rows]

    def update_session(self, session: SessionRecord) -> SessionRecord:
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET
                    pc_name = ?,
                    status = ?,
                    started_at = ?,
                    scheduled_end_at = ?,
                    ended_at = ?,
                    original_seconds = ?,
                    added_seconds = ?,
                    total_seconds = ?,
                    amount_paid = ?,
                    ended_reason = ?,
                    created_at = ?,
                    rate_id = ?,
                    rate_name_snapshot = ?,
                    base_amount = ?,
                    additional_amount = ?,
                    total_amount = ?,
                    session_mode = ?,
                    actual_elapsed_seconds = ?,
                    billed_seconds = ?,
                    final_computed_amount = ?,
                    metered_rate_price_snapshot = ?,
                    metered_rate_duration_seconds_snapshot = ?,
                    settlement_pending = ?,
                    created_by_user_id = ?,
                    ended_by_user_id = ?,
                    member_id = ?,
                    member_code_snapshot = ?,
                    member_name_snapshot = ?,
                    member_balance_before_seconds = ?,
                    member_balance_after_seconds = ?,
                    grace_started_at = ?,
                    shutdown_at = ?
                WHERE session_id = ?
                """,
                (
                    session.pc_name,
                    session.status,
                    datetime_to_storage(session.started_at),
                    datetime_to_storage(session.scheduled_end_at),
                    datetime_to_storage(session.ended_at),
                    session.original_seconds,
                    session.added_seconds,
                    session.total_seconds,
                    session.amount_paid,
                    session.ended_reason,
                    datetime_to_storage(session.created_at),
                    session.rate_id,
                    session.rate_name_snapshot,
                    decimal_to_storage(session.base_amount),
                    decimal_to_storage(session.additional_amount),
                    decimal_to_storage(session.total_amount),
                    session.session_mode,
                    session.actual_elapsed_seconds,
                    session.billed_seconds,
                    decimal_to_storage(session.final_computed_amount),
                    decimal_to_storage(session.metered_rate_price_snapshot),
                    session.metered_rate_duration_seconds_snapshot,
                    1 if session.settlement_pending else 0,
                    session.created_by_user_id,
                    session.ended_by_user_id,
                    session.member_id,
                    session.member_code_snapshot,
                    session.member_name_snapshot,
                    session.member_balance_before_seconds,
                    session.member_balance_after_seconds,
                    datetime_to_storage(session.grace_started_at),
                    datetime_to_storage(session.shutdown_at),
                    session.session_id,
                ),
            )

        return session

    def event_exists(self, session_id: str, event_type: str) -> bool:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM session_events
                WHERE session_id = ? AND event_type = ?
                LIMIT 1
                """,
                (session_id, event_type),
            ).fetchone()

        return row is not None

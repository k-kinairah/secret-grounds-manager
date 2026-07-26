import json
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from server.database import get_connection
from server.schemas import PaymentCreateRequest, PreparedSessionRecord
from server.time_utils import current_utc_time


ACTIVE_PREPARED_STATUSES = (
    "prepared",
    "waking",
    "waiting_for_client",
    "starting",
)


def decimal_to_storage(value: Decimal | None) -> str | None:
    return str(value.quantize(Decimal("0.01"))) if value is not None else None


def payment_to_json(payment: PaymentCreateRequest | None) -> str | None:
    if payment is None:
        return None
    return json.dumps(payment.model_dump(mode="json"), sort_keys=True)


def payment_from_json(value: str | None) -> PaymentCreateRequest | None:
    if value is None:
        return None
    return PaymentCreateRequest.model_validate(json.loads(value))


def row_to_prepared_session(row: Any) -> PreparedSessionRecord:
    return PreparedSessionRecord(
        prepared_session_id=row["prepared_session_id"],
        pc_name=row["pc_name"],
        status=row["status"],
        session_mode=row["session_mode"],
        rate_id=row["rate_id"],
        rate_name_snapshot=row["rate_name_snapshot"],
        duration_seconds=row["duration_seconds"],
        amount=Decimal(row["amount"]) if row["amount"] is not None else None,
        payment_json=row["payment_json"],
        member_id=row["member_id"],
        member_pin=row["member_pin"],
        member_name_snapshot=row["member_name_snapshot"],
        member_open_play=bool(row["member_open_play"]),
        prepared_by_user_id=row["prepared_by_user_id"],
        prepared_operator_id=row["prepared_operator_id"],
        prepared_at=datetime.fromisoformat(row["prepared_at"]),
        wake_requested_at=(
            datetime.fromisoformat(row["wake_requested_at"])
            if row["wake_requested_at"] is not None
            else None
        ),
        wake_status=row["wake_status"],
        startup_status=row["startup_status"],
        failure_reason=row["failure_reason"],
        expires_at=datetime.fromisoformat(row["expires_at"]),
        activated_session_id=row["activated_session_id"],
        activated_at=(
            datetime.fromisoformat(row["activated_at"])
            if row["activated_at"] is not None
            else None
        ),
        cancelled_by_user_id=row["cancelled_by_user_id"],
        cancelled_at=(
            datetime.fromisoformat(row["cancelled_at"])
            if row["cancelled_at"] is not None
            else None
        ),
    )


class PreparedSessionRepository:
    """Non-billable offline startup requests waiting for a client to boot."""

    def create(self, prepared: PreparedSessionRecord) -> PreparedSessionRecord:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO prepared_sessions (
                    prepared_session_id,
                    pc_name,
                    status,
                    session_mode,
                    rate_id,
                    rate_name_snapshot,
                    duration_seconds,
                    amount,
                    payment_json,
                    member_id,
                    member_pin,
                    member_name_snapshot,
                    member_open_play,
                    prepared_by_user_id,
                    prepared_operator_id,
                    prepared_at,
                    wake_requested_at,
                    wake_status,
                    startup_status,
                    failure_reason,
                    expires_at,
                    activated_session_id,
                    activated_at,
                    cancelled_by_user_id,
                    cancelled_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prepared.prepared_session_id,
                    prepared.pc_name,
                    prepared.status,
                    prepared.session_mode,
                    prepared.rate_id,
                    prepared.rate_name_snapshot,
                    prepared.duration_seconds,
                    decimal_to_storage(prepared.amount),
                    prepared.payment_json,
                    prepared.member_id,
                    prepared.member_pin,
                    prepared.member_name_snapshot,
                    1 if prepared.member_open_play else 0,
                    prepared.prepared_by_user_id,
                    prepared.prepared_operator_id,
                    prepared.prepared_at.isoformat(),
                    (
                        prepared.wake_requested_at.isoformat()
                        if prepared.wake_requested_at is not None
                        else None
                    ),
                    prepared.wake_status,
                    prepared.startup_status,
                    prepared.failure_reason,
                    prepared.expires_at.isoformat(),
                    prepared.activated_session_id,
                    (
                        prepared.activated_at.isoformat()
                        if prepared.activated_at is not None
                        else None
                    ),
                    prepared.cancelled_by_user_id,
                    (
                        prepared.cancelled_at.isoformat()
                        if prepared.cancelled_at is not None
                        else None
                    ),
                ),
            )
        return prepared

    def get(self, prepared_session_id: str) -> PreparedSessionRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM prepared_sessions WHERE prepared_session_id = ?",
                (prepared_session_id,),
            ).fetchone()
        return row_to_prepared_session(row) if row else None

    def get_active_for_pc(self, pc_name: str) -> PreparedSessionRecord | None:
        placeholders = ", ".join("?" for _ in ACTIVE_PREPARED_STATUSES)
        with get_connection() as connection:
            row = connection.execute(
                f"""
                SELECT * FROM prepared_sessions
                WHERE pc_name = ?
                  AND status IN ({placeholders})
                ORDER BY prepared_at DESC
                LIMIT 1
                """,
                (pc_name, *ACTIVE_PREPARED_STATUSES),
            ).fetchone()
        return row_to_prepared_session(row) if row else None

    def list_active(self) -> list[PreparedSessionRecord]:
        placeholders = ", ".join("?" for _ in ACTIVE_PREPARED_STATUSES)
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM prepared_sessions
                WHERE status IN ({placeholders})
                ORDER BY prepared_at DESC
                """,
                ACTIVE_PREPARED_STATUSES,
            ).fetchall()
        return [row_to_prepared_session(row) for row in rows]

    def update(self, prepared: PreparedSessionRecord) -> PreparedSessionRecord:
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE prepared_sessions
                SET
                    pc_name = ?,
                    status = ?,
                    session_mode = ?,
                    rate_id = ?,
                    rate_name_snapshot = ?,
                    duration_seconds = ?,
                    amount = ?,
                    payment_json = ?,
                    member_id = ?,
                    member_pin = ?,
                    member_name_snapshot = ?,
                    member_open_play = ?,
                    prepared_by_user_id = ?,
                    prepared_operator_id = ?,
                    prepared_at = ?,
                    wake_requested_at = ?,
                    wake_status = ?,
                    startup_status = ?,
                    failure_reason = ?,
                    expires_at = ?,
                    activated_session_id = ?,
                    activated_at = ?,
                    cancelled_by_user_id = ?,
                    cancelled_at = ?
                WHERE prepared_session_id = ?
                """,
                (
                    prepared.pc_name,
                    prepared.status,
                    prepared.session_mode,
                    prepared.rate_id,
                    prepared.rate_name_snapshot,
                    prepared.duration_seconds,
                    decimal_to_storage(prepared.amount),
                    prepared.payment_json,
                    prepared.member_id,
                    prepared.member_pin,
                    prepared.member_name_snapshot,
                    1 if prepared.member_open_play else 0,
                    prepared.prepared_by_user_id,
                    prepared.prepared_operator_id,
                    prepared.prepared_at.isoformat(),
                    (
                        prepared.wake_requested_at.isoformat()
                        if prepared.wake_requested_at is not None
                        else None
                    ),
                    prepared.wake_status,
                    prepared.startup_status,
                    prepared.failure_reason,
                    prepared.expires_at.isoformat(),
                    prepared.activated_session_id,
                    (
                        prepared.activated_at.isoformat()
                        if prepared.activated_at is not None
                        else None
                    ),
                    prepared.cancelled_by_user_id,
                    (
                        prepared.cancelled_at.isoformat()
                        if prepared.cancelled_at is not None
                        else None
                    ),
                    prepared.prepared_session_id,
                ),
            )
        return prepared

    def new_id(self) -> str:
        return str(uuid4())

    def expire_old_preparations(self) -> list[PreparedSessionRecord]:
        now = current_utc_time()
        expired: list[PreparedSessionRecord] = []
        for prepared in self.list_active():
            if prepared.expires_at <= now:
                expired_prepared = prepared.model_copy(
                    update={
                        "status": "failed",
                        "startup_status": "failed",
                        "failure_reason": "Client did not reconnect before preparation expired",
                    }
                )
                expired.append(self.update(expired_prepared))
        return expired

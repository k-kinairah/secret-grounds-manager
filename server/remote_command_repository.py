import json
from datetime import datetime
from typing import Any

from server.database import get_connection
from server.schemas import RemoteCommandRecord, RemoteCommandStatus, RemoteCommandType
from server.time_utils import current_utc_time


def row_to_remote_command(row: Any) -> RemoteCommandRecord:
    return RemoteCommandRecord(
        command_id=row["command_id"],
        pc_name=row["pc_name"],
        command_type=row["command_type"],
        payload_json=row["payload_json"],
        status=row["status"],
        requested_by_user_id=row["requested_by_user_id"],
        requested_by_display_name=row["requested_by_display_name"] if "requested_by_display_name" in row.keys() else None,
        requested_at=datetime.fromisoformat(row["requested_at"]),
        received_at=datetime.fromisoformat(row["received_at"]) if row["received_at"] else None,
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        failed_at=datetime.fromisoformat(row["failed_at"]) if row["failed_at"] else None,
        failure_reason=row["failure_reason"],
        client_response=row["client_response"],
        forced=bool(row["forced"]),
        force_reason=row["force_reason"],
    )


class RemoteCommandRepository:
    """SQLite persistence for production remote control audit records."""

    def create(
        self,
        command_id: str,
        pc_name: str,
        command_type: RemoteCommandType,
        payload: dict[str, object] | None,
        requested_by_user_id: str | None,
        forced: bool = False,
        force_reason: str | None = None,
        status: RemoteCommandStatus = "pending",
    ) -> RemoteCommandRecord:
        requested_at = current_utc_time()
        payload_json = json.dumps(payload, sort_keys=True) if payload is not None else None
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO remote_commands (
                    command_id,
                    pc_name,
                    command_type,
                    payload_json,
                    status,
                    requested_by_user_id,
                    requested_at,
                    forced,
                    force_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command_id,
                    pc_name,
                    command_type,
                    payload_json,
                    status,
                    requested_by_user_id,
                    requested_at.isoformat(),
                    1 if forced else 0,
                    force_reason,
                ),
            )
        record = self.get(command_id)
        if record is None:
            raise RuntimeError("Remote command record was not persisted")
        return record

    def get(self, command_id: str) -> RemoteCommandRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT rc.*, users.display_name AS requested_by_display_name
                FROM remote_commands rc
                LEFT JOIN users ON users.user_id = rc.requested_by_user_id
                WHERE rc.command_id = ?
                """,
                (command_id,),
            ).fetchone()
        return row_to_remote_command(row) if row else None

    def mark_received(self, command_id: str, client_response: str | None = None) -> None:
        now = current_utc_time()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE remote_commands
                SET status = 'received', received_at = COALESCE(received_at, ?), client_response = ?
                WHERE command_id = ?
                """,
                (now.isoformat(), client_response, command_id),
            )

    def mark_completed(self, command_id: str, client_response: str | None = None) -> None:
        now = current_utc_time()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE remote_commands
                SET status = 'completed', completed_at = ?, client_response = ?
                WHERE command_id = ?
                """,
                (now.isoformat(), client_response, command_id),
            )

    def mark_failed(
        self,
        command_id: str,
        status: RemoteCommandStatus,
        reason: str,
        client_response: str | None = None,
    ) -> None:
        now = current_utc_time()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE remote_commands
                SET status = ?, failed_at = ?, failure_reason = ?, client_response = ?
                WHERE command_id = ?
                """,
                (status, now.isoformat(), reason, client_response, command_id),
            )

    def list_commands(
        self,
        role: str,
        actor_user_id: str | None,
        limit: int = 100,
    ) -> list[RemoteCommandRecord]:
        with get_connection() as connection:
            if role == "cashier":
                rows = connection.execute(
                    """
                    SELECT rc.*, users.display_name AS requested_by_display_name
                    FROM remote_commands rc
                    LEFT JOIN users ON users.user_id = rc.requested_by_user_id
                    WHERE rc.requested_by_user_id = ?
                      AND rc.command_type IN ('show_message', 'show_announcement')
                    ORDER BY rc.requested_at DESC
                    LIMIT ?
                    """,
                    (actor_user_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT rc.*, users.display_name AS requested_by_display_name
                    FROM remote_commands rc
                    LEFT JOIN users ON users.user_id = rc.requested_by_user_id
                    ORDER BY rc.requested_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [row_to_remote_command(row) for row in rows]


class PCMetadataRepository:
    """Persistence boundary for saved client metadata that survives restarts."""

    def get_mac_address(self, pc_name: str) -> str | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT mac_address FROM pc_metadata WHERE pc_name = ?",
                (pc_name,),
            ).fetchone()
        return row["mac_address"] if row and row["mac_address"] else None

    def set_mac_address(self, pc_name: str, mac_address: str | None) -> str | None:
        now = current_utc_time()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO pc_metadata (pc_name, mac_address, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(pc_name) DO UPDATE SET
                    mac_address = excluded.mac_address,
                    updated_at = excluded.updated_at
                """,
                (pc_name, mac_address, now.isoformat()),
            )
        return mac_address

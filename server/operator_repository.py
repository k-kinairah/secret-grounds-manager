import json
import re
from datetime import datetime
from typing import Any
from uuid import uuid4

import sqlite3
from fastapi import HTTPException

from server.database import get_connection
from server.passwords import hash_password
from server.schemas import (
    OperatorCreateRequest,
    OperatorManagementRecord,
    OperatorPasswordResetRequest,
    OperatorUpdateRequest,
    UserRecord,
)
from server.time_utils import current_utc_time


USERNAME_PATTERN = re.compile(r"^[a-z0-9_]{3,40}$")
OPERATOR_ORDER_SQL = """
CASE username_normalized
    WHEN 'operator_mama' THEN 10
    WHEN 'operator_neil' THEN 20
    WHEN 'operator_tyrone' THEN 30
    WHEN 'operator_rogene' THEN 40
    WHEN 'operator_rojan' THEN 50
    WHEN 'operator_bel' THEN 60
    WHEN 'operator_karen' THEN 70
    WHEN 'admin' THEN 90
    ELSE 80
END
"""


def normalize_operator_username(username: str) -> str:
    value = username.strip().casefold()
    if value.startswith("@"):
        value = value[1:]
    if value.startswith("operator_"):
        value = value.removeprefix("operator_")
    if not USERNAME_PATTERN.fullmatch(value):
        raise HTTPException(
            status_code=422,
            detail="Username must be 3-40 lowercase letters, numbers, or underscores.",
        )
    return "admin" if value == "admin" else value


def operator_from_row(row: Any) -> OperatorManagementRecord:
    access_method = row["access_method"] if row["access_method"] in {"quick", "password"} else "password"
    return OperatorManagementRecord(
        operator_id=row["user_id"],
        username=row["username"],
        display_name=row["display_name"],
        access_method=access_method,
        password_required=access_method == "password",
        role=row["role"],
        is_active=bool(row["is_active"]),
        is_protected=bool(row["is_protected"]),
        active_session_count=int(row["active_session_count"]) if "active_session_count" in row.keys() else 0,
        archived_at=datetime.fromisoformat(row["archived_at"]) if row["archived_at"] else None,
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
        last_login_at=datetime.fromisoformat(row["last_login_at"]) if row["last_login_at"] else None,
    )


class OperatorRepository:
    def list_operators(self, include_archived: bool = False) -> list[OperatorManagementRecord]:
        where = "is_operator = 1"
        if not include_archived:
            where += " AND archived_at IS NULL"
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT users.*,
                    (
                        SELECT COUNT(*) FROM auth_sessions
                        WHERE auth_sessions.user_id = users.user_id
                          AND auth_sessions.revoked_at IS NULL
                    ) AS active_session_count
                FROM users
                WHERE {where}
                ORDER BY {OPERATOR_ORDER_SQL}, display_name COLLATE NOCASE ASC
                """
            ).fetchall()
        return [operator_from_row(row) for row in rows]

    def get_operator(self, operator_id: str) -> OperatorManagementRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT users.*,
                    (
                        SELECT COUNT(*) FROM auth_sessions
                        WHERE auth_sessions.user_id = users.user_id
                          AND auth_sessions.revoked_at IS NULL
                    ) AS active_session_count
                FROM users WHERE user_id = ? AND is_operator = 1
                """,
                (operator_id,),
            ).fetchone()
        return operator_from_row(row) if row is not None else None

    def get_active_operator_user(self, operator_id: str) -> UserRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT users.*,
                    (
                        SELECT COUNT(*) FROM auth_sessions
                        WHERE auth_sessions.user_id = users.user_id
                          AND auth_sessions.revoked_at IS NULL
                    ) AS active_session_count
                FROM users
                WHERE user_id = ?
                  AND is_operator = 1
                  AND is_active = 1
                  AND archived_at IS NULL
                """,
                (operator_id,),
            ).fetchone()
        if row is None:
            return None
        from server.user_repository import row_to_user

        return row_to_user(row)

    def list_login_operators(self) -> list[OperatorManagementRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM users
                WHERE is_operator = 1
                  AND is_active = 1
                  AND archived_at IS NULL
                ORDER BY {OPERATOR_ORDER_SQL}, display_name COLLATE NOCASE ASC
                """
            ).fetchall()
        return [operator_from_row(row) for row in rows]

    def create_operator(self, request: OperatorCreateRequest, actor: UserRecord) -> OperatorManagementRecord:
        username = normalize_operator_username(request.username)
        if username == "admin":
            raise HTTPException(status_code=400, detail="Admin operator already exists.")
        if request.access_method == "password" and not request.password:
            raise HTTPException(status_code=422, detail="Password is required for password access.")
        now = current_utc_time()
        user_id = str(uuid4())
        password_hash = hash_password(request.password if request.access_method == "password" else str(uuid4()))
        with get_connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO users (
                        user_id, username, username_normalized, display_name, password_hash,
                        role, is_active, created_at, updated_at, last_login_at,
                        is_operator, access_method, is_protected, archived_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'cashier', ?, ?, ?, NULL, 1, ?, 0, NULL)
                    """,
                    (
                        user_id,
                        username,
                        username,
                        request.display_name.strip(),
                        password_hash,
                        1 if request.is_active else 0,
                        now.isoformat(),
                        now.isoformat(),
                        request.access_method,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(status_code=409, detail="Username is already in use.") from error
            row = connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            self._record_audit(
                connection,
                "operator_created",
                actor,
                user_id,
                request.display_name.strip(),
                {"username": username, "access_method": request.access_method, "is_active": request.is_active},
            )
        return operator_from_row(row)

    def update_operator(self, operator_id: str, request: OperatorUpdateRequest, actor: UserRecord) -> OperatorManagementRecord:
        existing = self.get_operator(operator_id)
        if existing is None or existing.archived_at is not None:
            raise HTTPException(status_code=404, detail="Operator not found.")
        username = normalize_operator_username(request.username)
        if existing.is_protected:
            if username != "admin" or request.access_method != "password" or not request.is_active:
                raise HTTPException(status_code=400, detail="Protected Admin rules cannot be changed.")
        elif username == "admin":
            raise HTTPException(status_code=400, detail="Username admin is reserved.")
        changed: dict[str, dict[str, object]] = {}
        for field, old, new in [
            ("username", existing.username, username),
            ("display_name", existing.display_name, request.display_name.strip()),
            ("access_method", existing.access_method, request.access_method),
            ("is_active", existing.is_active, request.is_active),
        ]:
            if old != new:
                changed[field] = {"old": old, "new": new}
        if existing.access_method == "quick" and request.access_method == "password":
            raise HTTPException(status_code=400, detail="Use Reset Password before converting to password access.")
        now = current_utc_time()
        with get_connection() as connection:
            try:
                connection.execute(
                    """
                    UPDATE users
                    SET username = ?, username_normalized = ?, display_name = ?,
                        access_method = ?, is_active = ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (
                        username,
                        username,
                        request.display_name.strip(),
                        request.access_method,
                        1 if request.is_active else 0,
                        now.isoformat(),
                        operator_id,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(status_code=409, detail="Username is already in use.") from error
            if not request.is_active:
                self._revoke_sessions(connection, operator_id, now.isoformat())
            row = connection.execute("SELECT * FROM users WHERE user_id = ?", (operator_id,)).fetchone()
            if changed:
                self._record_audit(connection, "operator_updated", actor, operator_id, request.display_name.strip(), changed)
                if "access_method" in changed:
                    self._record_audit(connection, "operator_access_method_changed", actor, operator_id, request.display_name.strip(), changed["access_method"])
        return operator_from_row(row)

    def set_enabled(self, operator_id: str, enabled: bool, actor: UserRecord) -> OperatorManagementRecord:
        existing = self.get_operator(operator_id)
        if existing is None or existing.archived_at is not None:
            raise HTTPException(status_code=404, detail="Operator not found.")
        if existing.is_protected:
            raise HTTPException(status_code=400, detail="Protected Admin cannot be disabled.")
        now = current_utc_time()
        with get_connection() as connection:
            connection.execute(
                "UPDATE users SET is_active = ?, updated_at = ? WHERE user_id = ?",
                (1 if enabled else 0, now.isoformat(), operator_id),
            )
            if not enabled:
                self._revoke_sessions(connection, operator_id, now.isoformat())
            row = connection.execute("SELECT * FROM users WHERE user_id = ?", (operator_id,)).fetchone()
            self._record_audit(
                connection,
                "operator_enabled" if enabled else "operator_disabled",
                actor,
                operator_id,
                existing.display_name,
                {"is_active": {"old": existing.is_active, "new": enabled}},
            )
        return operator_from_row(row)

    def archive_operator(self, operator_id: str, actor: UserRecord) -> OperatorManagementRecord:
        existing = self.get_operator(operator_id)
        if existing is None or existing.archived_at is not None:
            raise HTTPException(status_code=404, detail="Operator not found.")
        if existing.is_protected:
            raise HTTPException(status_code=400, detail="Protected Admin cannot be archived.")
        now = current_utc_time()
        with get_connection() as connection:
            connection.execute(
                "UPDATE users SET is_active = 0, archived_at = ?, updated_at = ? WHERE user_id = ?",
                (now.isoformat(), now.isoformat(), operator_id),
            )
            self._revoke_sessions(connection, operator_id, now.isoformat())
            row = connection.execute("SELECT * FROM users WHERE user_id = ?", (operator_id,)).fetchone()
            self._record_audit(connection, "operator_archived", actor, operator_id, existing.display_name, {"archived_at": now.isoformat()})
        return operator_from_row(row)

    def reset_password(self, operator_id: str, request: OperatorPasswordResetRequest, actor: UserRecord) -> OperatorManagementRecord:
        existing = self.get_operator(operator_id)
        if existing is None or existing.archived_at is not None:
            raise HTTPException(status_code=404, detail="Operator not found.")
        now = current_utc_time()
        with get_connection() as connection:
            connection.execute(
                "UPDATE users SET password_hash = ?, access_method = 'password', updated_at = ? WHERE user_id = ?",
                (hash_password(request.password), now.isoformat(), operator_id),
            )
            self._revoke_sessions(connection, operator_id, now.isoformat())
            row = connection.execute("SELECT * FROM users WHERE user_id = ?", (operator_id,)).fetchone()
            self._record_audit(connection, "operator_password_reset", actor, operator_id, existing.display_name, {"password": "reset"})
        return operator_from_row(row)

    def sign_out_operator(self, operator_id: str, actor: UserRecord) -> OperatorManagementRecord:
        existing = self.get_operator(operator_id)
        if existing is None or existing.archived_at is not None:
            raise HTTPException(status_code=404, detail="Operator not found.")
        if existing.is_protected:
            raise HTTPException(status_code=400, detail="Protected Admin cannot be signed out from Staff.")
        if existing.active_session_count <= 0:
            raise HTTPException(status_code=409, detail="Operator is not currently signed in.")
        now = current_utc_time()
        with get_connection() as connection:
            self._revoke_sessions(connection, operator_id, now.isoformat())
            row = connection.execute("SELECT * FROM users WHERE user_id = ?", (operator_id,)).fetchone()
            self._record_audit(connection, "operator_signed_out", actor, operator_id, existing.display_name, {"dashboard_sessions": "ended"})
        return operator_from_row(row)

    def revoke_all_sessions(self, user_id: str) -> None:
        """Plain, business-rule-free session revocation -- reuses the same
        _revoke_sessions query every admin-initiated revocation above
        already uses. Deliberately does not apply sign_out_operator's
        Staff-panel guard rails (protected-Admin block, "must currently be
        signed in" 409): End Shift (server/main.py close_shift) must be
        able to force-logout any operator, including a protected account
        ending their own shift, and must never fail merely because the
        operator happened to have no active session at close time."""
        now = current_utc_time()
        with get_connection() as connection:
            self._revoke_sessions(connection, user_id, now.isoformat())

    def _record_audit(
        self,
        connection: sqlite3.Connection,
        event_type: str,
        actor: UserRecord,
        target_user_id: str,
        target_display_name: str,
        metadata: object,
    ) -> None:
        connection.execute(
            """
            INSERT INTO operator_audit_events (
                event_id, event_type, operator_user_id, operator_display_name,
                selected_by_user_id, created_at, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                event_type,
                target_user_id,
                target_display_name,
                actor.user_id,
                current_utc_time().isoformat(),
                json.dumps(metadata, sort_keys=True),
            ),
        )

    def _revoke_sessions(self, connection: sqlite3.Connection, user_id: str, revoked_at: str) -> None:
        connection.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (revoked_at, user_id),
        )

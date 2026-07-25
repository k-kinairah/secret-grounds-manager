from datetime import datetime
import sqlite3
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from server.database import get_connection
from server.passwords import hash_password
from server.schemas import (
    PasswordResetRequest,
    UserAdminRecord,
    UserCreateRequest,
    UserRecord,
    UserStatusUpdate,
    UserUpdateRequest,
)
from server.time_utils import current_utc_time


def normalize_username(username: str) -> str:
    return username.strip().casefold()


def row_to_user(row: Any) -> UserRecord:
    return UserRecord(
        user_id=row["user_id"],
        username=row["username"],
        display_name=row["display_name"],
        password_hash=row["password_hash"],
        role=row["role"],
        is_active=bool(row["is_active"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        last_login_at=(
            datetime.fromisoformat(row["last_login_at"])
            if row["last_login_at"] is not None
            else None
        ),
    )


def to_admin_record(user: UserRecord) -> UserAdminRecord:
    return UserAdminRecord(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_login_at=user.last_login_at,
    )


class UserRepository:
    def list_users(self) -> list[UserAdminRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM users ORDER BY username_normalized ASC"
            ).fetchall()
        return [to_admin_record(row_to_user(row)) for row in rows]

    def get_user(self, user_id: str) -> UserRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row_to_user(row) if row is not None else None

    def get_by_username(self, username: str) -> UserRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username_normalized = ?",
                (normalize_username(username),),
            ).fetchone()
        return row_to_user(row) if row is not None else None

    def count_active_owners(self) -> int:
        with get_connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'owner' AND is_active = 1"
                ).fetchone()[0]
            )

    def create_user(self, request: UserCreateRequest) -> UserAdminRecord:
        now = current_utc_time()
        user = UserRecord(
            user_id=str(uuid4()),
            username=request.username.strip(),
            display_name=request.display_name.strip(),
            password_hash=hash_password(request.password),
            role=request.role,
            is_active=True,
            created_at=now,
            updated_at=now,
            last_login_at=None,
        )
        with get_connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO users (
                        user_id,
                        username,
                        username_normalized,
                        display_name,
                        password_hash,
                        role,
                        is_active,
                        created_at,
                        updated_at,
                        last_login_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user.user_id,
                        user.username,
                        normalize_username(user.username),
                        user.display_name,
                        user.password_hash,
                        user.role,
                        1,
                        user.created_at.isoformat(),
                        user.updated_at.isoformat(),
                        None,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(status_code=409, detail="Username is already in use") from error
        return to_admin_record(user)

    def update_user(self, user_id: str, request: UserUpdateRequest) -> UserAdminRecord | None:
        existing = self.get_user(user_id)
        if existing is None:
            return None
        if existing.role == "owner" and request.role != "owner" and self.count_active_owners() <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last active owner")
        updated = existing.model_copy(
            update={
                "username": request.username.strip(),
                "display_name": request.display_name.strip(),
                "role": request.role,
                "updated_at": current_utc_time(),
            }
        )
        with get_connection() as connection:
            try:
                connection.execute(
                    """
                    UPDATE users
                    SET username = ?, username_normalized = ?, display_name = ?, role = ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (
                        updated.username,
                        normalize_username(updated.username),
                        updated.display_name,
                        updated.role,
                        updated.updated_at.isoformat(),
                        updated.user_id,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(status_code=409, detail="Username is already in use") from error
        return to_admin_record(updated)

    def set_active(self, user_id: str, request: UserStatusUpdate) -> UserAdminRecord | None:
        existing = self.get_user(user_id)
        if existing is None:
            return None
        if existing.role == "owner" and existing.is_active and not request.is_active and self.count_active_owners() <= 1:
            raise HTTPException(status_code=400, detail="Cannot disable the last active owner")
        updated = existing.model_copy(
            update={"is_active": request.is_active, "updated_at": current_utc_time()}
        )
        with get_connection() as connection:
            connection.execute(
                "UPDATE users SET is_active = ?, updated_at = ? WHERE user_id = ?",
                (1 if updated.is_active else 0, updated.updated_at.isoformat(), updated.user_id),
            )
        return to_admin_record(updated)

    def reset_password(self, user_id: str, request: PasswordResetRequest) -> UserAdminRecord | None:
        existing = self.get_user(user_id)
        if existing is None:
            return None
        updated_at = current_utc_time()
        with get_connection() as connection:
            connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE user_id = ?",
                (hash_password(request.password), updated_at.isoformat(), user_id),
            )
            connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (updated_at.isoformat(), user_id),
            )
        return to_admin_record(existing.model_copy(update={"updated_at": updated_at}))

    def mark_login(self, user_id: str) -> None:
        now = current_utc_time().isoformat()
        with get_connection() as connection:
            connection.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE user_id = ?",
                (now, now, user_id),
            )

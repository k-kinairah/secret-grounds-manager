import hashlib
import secrets
from datetime import timedelta
from typing import Any
from uuid import uuid4

from server.database import get_connection
from server.time_utils import current_utc_time


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthSessionRepository:
    def create_session(self, user_id: str, lifetime_seconds: int) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        session_id = str(uuid4())
        now = current_utc_time()
        expires_at = now + timedelta(seconds=lifetime_seconds)
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO auth_sessions (
                    session_id,
                    user_id,
                    token_hash,
                    expires_at,
                    created_at,
                    revoked_at
                )
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (
                    session_id,
                    user_id,
                    hash_token(token),
                    expires_at.isoformat(),
                    now.isoformat(),
                ),
            )
        return token, session_id

    def get_user_id_for_token(self, token: str) -> str | None:
        now = current_utc_time().isoformat()
        with get_connection() as connection:
            row: Any = connection.execute(
                """
                SELECT user_id FROM auth_sessions
                WHERE token_hash = ?
                  AND revoked_at IS NULL
                  AND expires_at > ?
                """,
                (hash_token(token), now),
            ).fetchone()
        return str(row["user_id"]) if row is not None else None

    def revoke_token(self, token: str) -> None:
        with get_connection() as connection:
            connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE token_hash = ?",
                (current_utc_time().isoformat(), hash_token(token)),
            )

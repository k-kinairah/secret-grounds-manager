import hmac
import os
import time
from collections import defaultdict
from typing import Callable

from fastapi import Cookie, Depends, HTTPException, Request, Response

from server.auth_repository import AuthSessionRepository
from server.operator_repository import OperatorRepository
from server.passwords import verify_password
from server.schemas import LoginRequest, OperatorAccessRecord, UserPublic, UserRecord, UserRole
from server.user_repository import UserRepository


AUTH_COOKIE_NAME = "sg_session"
SESSION_LIFETIME_SECONDS = int(os.getenv("SECRET_GROUNDS_AUTH_SECONDS", str(8 * 60 * 60)))
FAILED_LOGIN_COUNTS: dict[str, int] = defaultdict(int)
FAILED_ADMIN_OPERATOR_ATTEMPTS: dict[str, int] = defaultdict(int)


user_repository = UserRepository()
auth_session_repository = AuthSessionRepository()
operator_repository = OperatorRepository()


ROLE_RANK: dict[UserRole, int] = {
    "cashier": 1,
    "admin": 2,
    "owner": 3,
}


def auth_cookie_secure() -> bool:
    return os.getenv("SECRET_GROUNDS_AUTH_SECURE_COOKIE", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def auth_cookie_samesite() -> str:
    configured = os.getenv("SECRET_GROUNDS_AUTH_SAMESITE", "lax").strip().lower()
    return configured if configured in {"lax", "strict", "none"} else "lax"


def to_public_user(user: UserRecord) -> UserPublic:
    return UserPublic(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        is_protected=getattr(user, "is_protected", False),
    )


def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=SESSION_LIFETIME_SECONDS,
        httponly=True,
        secure=auth_cookie_secure(),
        samesite=auth_cookie_samesite(),
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        AUTH_COOKIE_NAME,
        path="/",
        secure=auth_cookie_secure(),
        samesite=auth_cookie_samesite(),
        httponly=True,
    )


def authenticate_login(request: LoginRequest) -> UserRecord:
    username_key = request.username.strip().casefold()
    attempts = FAILED_LOGIN_COUNTS[username_key]
    if attempts >= 3:
        time.sleep(min(2.0, 0.25 * attempts))

    user = user_repository.get_by_username(request.username)
    if user is None or not user.is_active or not verify_password(request.password, user.password_hash):
        FAILED_LOGIN_COUNTS[username_key] += 1
        print(f"Failed login attempt for username={request.username!r}")
        raise HTTPException(status_code=401, detail="Invalid username or password")

    FAILED_LOGIN_COUNTS.pop(username_key, None)
    user_repository.mark_login(user.user_id)
    return user


def list_family_operator_access() -> list[OperatorAccessRecord]:
    records: list[OperatorAccessRecord] = []
    for operator in operator_repository.list_login_operators():
        user = user_repository.get_user(operator.operator_id)
        records.append(
            OperatorAccessRecord(
                operator_id=operator.operator_id,
                display_name=operator.display_name,
                password_required=operator.password_required,
                user=to_public_user(user) if user is not None and user.is_active else None,
            )
        )
    return records


def authenticate_quick_operator(operator_id: str) -> UserRecord:
    operator = operator_repository.get_operator(operator_id)
    if operator is None or operator.archived_at is not None or operator.password_required:
        raise HTTPException(status_code=403, detail="Operator quick access is not allowed")

    user = user_repository.get_user(operator.operator_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail="Operator is not configured")

    user_repository.mark_login(user.user_id)
    return user


def authenticate_password_operator(operator_id: str, password: str) -> UserRecord:
    operator = operator_repository.get_operator(operator_id)
    if operator is None or operator.archived_at is not None or not operator.password_required:
        raise HTTPException(status_code=403, detail="Password access is not allowed")

    user = user_repository.get_user(operator.operator_id)
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid operator password")

    user_repository.mark_login(user.user_id)
    return user


def authenticate_admin_operator(password: str, client_key: str = "admin") -> UserRecord:
    attempts = FAILED_ADMIN_OPERATOR_ATTEMPTS[client_key]
    if attempts >= 3:
        time.sleep(min(2.0, 0.35 * attempts))

    user = user_repository.get_by_username("admin")
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        FAILED_ADMIN_OPERATOR_ATTEMPTS[client_key] += 1
        print("Failed Admin operator login attempt")
        raise HTTPException(status_code=401, detail="Invalid Admin password")

    FAILED_ADMIN_OPERATOR_ATTEMPTS.pop(client_key, None)
    user_repository.mark_login(user.user_id)
    return user


def authenticate_assistant_token(request: Request | None) -> UserRecord | None:
    """Opt-in bearer-token auth for the read-only AI assistant (assistant/).

    Completely inert unless SG_ASSISTANT_TOKEN is set: returns None and the
    caller falls through to the normal 401. When set, a matching
    `Authorization: Bearer <token>` authenticates as the active staff user
    named by SG_ASSISTANT_USERNAME, whose normal role checks still apply.
    Phase 1 is read-only: any non-GET request with the token gets 403."""
    configured_token = os.getenv("SG_ASSISTANT_TOKEN", "").strip()
    if not configured_token or request is None:
        return None

    scheme, _, presented_token = request.headers.get("authorization", "").partition(" ")
    presented_token = presented_token.strip()
    if scheme.lower() != "bearer" or not presented_token:
        return None
    if not hmac.compare_digest(presented_token.encode("utf-8"), configured_token.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Authentication required")
    if request.method != "GET":
        raise HTTPException(status_code=403, detail="The assistant token is read-only")

    username = os.getenv("SG_ASSISTANT_USERNAME", "").strip()
    user = user_repository.get_by_username(username) if username else None
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def get_current_user(
    sg_session: str | None = Cookie(default=None, alias=AUTH_COOKIE_NAME),
    request: Request = None,  # type: ignore[assignment]  # injected by FastAPI; None for direct calls
) -> UserRecord:
    if not sg_session:
        assistant_user = authenticate_assistant_token(request)
        if assistant_user is not None:
            return assistant_user
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = auth_session_repository.get_user_id_for_token(sg_session)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = user_repository.get_user(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Authentication required")

    return user


def require_role(minimum_role: UserRole) -> Callable[[UserRecord], UserRecord]:
    def dependency(current_user: UserRecord = Depends(get_current_user)) -> UserRecord:
        if ROLE_RANK[current_user.role] < ROLE_RANK[minimum_role]:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return current_user

    return dependency


def require_owner(current_user: UserRecord = Depends(get_current_user)) -> UserRecord:
    if current_user.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return current_user


def require_system_admin(current_user: UserRecord = Depends(get_current_user)) -> UserRecord:
    """System-admin capability: reserved for the protected Admin account
    (is_protected == 1). This is deliberately NOT a role check and NOT a
    username check -- normal admin features stay role-based; only the
    infrastructure tier (System, Remote Commands, backups/restore, DB
    health/logs) is gated here."""
    if not getattr(current_user, "is_protected", False):
        raise HTTPException(
            status_code=403,
            detail="System administration is restricted to the protected Admin account.",
        )
    return current_user


def get_actor_user_id(current_user: object) -> str | None:
    return current_user.user_id if isinstance(current_user, UserRecord) else None

import os
import time
from collections import defaultdict
from typing import Callable

from fastapi import Cookie, Depends, HTTPException, Response

from server.auth_repository import AuthSessionRepository
from server.passwords import verify_password
from server.schemas import LoginRequest, UserPublic, UserRecord, UserRole
from server.user_repository import UserRepository


AUTH_COOKIE_NAME = "sg_session"
SESSION_LIFETIME_SECONDS = int(os.getenv("SECRET_GROUNDS_AUTH_SECONDS", str(8 * 60 * 60)))
FAILED_LOGIN_COUNTS: dict[str, int] = defaultdict(int)


user_repository = UserRepository()
auth_session_repository = AuthSessionRepository()


ROLE_RANK: dict[UserRole, int] = {
    "cashier": 1,
    "admin": 2,
    "owner": 3,
}


def to_public_user(user: UserRecord) -> UserPublic:
    return UserPublic(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
    )


def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=SESSION_LIFETIME_SECONDS,
        httponly=True,
        secure=os.getenv("SECRET_GROUNDS_AUTH_SECURE_COOKIE", "false").lower() == "true",
        samesite=os.getenv("SECRET_GROUNDS_AUTH_SAMESITE", "lax"),
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")


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


def get_current_user(
    sg_session: str | None = Cookie(default=None, alias=AUTH_COOKIE_NAME),
) -> UserRecord:
    if not sg_session:
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


def get_actor_user_id(current_user: object) -> str | None:
    return current_user.user_id if isinstance(current_user, UserRecord) else None

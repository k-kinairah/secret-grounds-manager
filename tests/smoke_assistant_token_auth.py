"""Opt-in assistant bearer-token auth (server/auth.py authenticate_assistant_token).

Drives a real uvicorn backend over HTTP on a free local port with a temp
database, once per environment configuration:
  (a) SG_ASSISTANT_TOKEN unset -> a bearer header is ignored, still 401
  (b) wrong token -> 401
  (c) valid token -> GET works, with the mapped user's role enforced
  (d) valid token -> non-GET is rejected with 403 and changes nothing
  (e) cookie auth works exactly as before, with and without the token set
"""

import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

TOKEN = "smoke-assistant-token-0123456789"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def bootstrap_users(database_path: str) -> None:
    os.environ["SECRET_GROUNDS_DB_PATH"] = database_path
    from server.database import initialize_database
    from server.schemas import UserCreateRequest
    from server.user_repository import UserRepository

    initialize_database()
    repository = UserRepository()
    for username, role in (("owner", "owner"), ("assistant", "cashier"), ("retired", "cashier")):
        if repository.get_by_username(username) is None:
            repository.create_user(
                UserCreateRequest(
                    username=username,
                    display_name=username.title(),
                    password=f"{username}-pass",
                    role=role,
                )
            )
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE users SET is_active = 0 WHERE username = 'retired'")


class Backend:
    def __init__(self, database_path: str, extra_env: dict[str, str]) -> None:
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        environment = os.environ.copy()
        environment.pop("SG_ASSISTANT_TOKEN", None)
        environment.pop("SG_ASSISTANT_USERNAME", None)
        environment["SECRET_GROUNDS_DB_PATH"] = database_path
        environment["SECRET_GROUNDS_TEST_MODE"] = "true"
        environment.update(extra_env)
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def __enter__(self) -> "Backend":
        for _ in range(60):
            try:
                if requests.get(f"{self.url}/", timeout=1).status_code == 200:
                    return self
            except requests.RequestException:
                pass
            time.sleep(0.25)
        self.__exit__()
        raise AssertionError("Backend did not start")

    def __exit__(self, *_: object) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def cookie_login(backend: Backend) -> requests.Session:
    session = requests.Session()
    response = session.post(
        f"{backend.url}/auth/login", json={"username": "owner", "password": "owner-pass"}, timeout=5
    )
    assert response.status_code == 200, response.text
    return session


def check_cookie_auth_unchanged(backend: Backend) -> None:
    # (e) Normal cookie flow: login, read, write, logout -- exactly as before.
    session = cookie_login(backend)
    me = session.get(f"{backend.url}/auth/me", timeout=5)
    assert me.status_code == 200 and me.json()["username"] == "owner", me.text
    assert session.get(f"{backend.url}/settings", timeout=5).status_code == 200
    opened = session.post(f"{backend.url}/shifts/open", json={"opening_cash": "0.00"}, timeout=5)
    assert opened.status_code == 200, opened.text
    assert session.post(f"{backend.url}/auth/logout", timeout=5).status_code == 200
    assert session.get(f"{backend.url}/auth/me", timeout=5).status_code == 401
    # No cookie, no header: still the same 401.
    assert requests.get(f"{backend.url}/auth/me", timeout=5).status_code == 401


def test_token_unset_is_inert(database_path: str) -> None:
    # (a) Feature off: a bearer header is ignored entirely.
    with Backend(database_path, {"SG_ASSISTANT_USERNAME": "assistant"}) as backend:
        for path in ("/auth/me", "/pcs", "/sessions/active"):
            response = requests.get(f"{backend.url}{path}", headers=bearer(TOKEN), timeout=5)
            assert response.status_code == 401, (path, response.status_code, response.text)
        # A POST with the header is not turned into 403 either -- same as today.
        response = requests.post(
            f"{backend.url}/shifts/open", json={"opening_cash": "0.00"}, headers=bearer(TOKEN), timeout=5
        )
        assert response.status_code == 401, response.text
        check_cookie_auth_unchanged(backend)

    # Empty string counts as unset.
    with Backend(database_path, {"SG_ASSISTANT_TOKEN": "", "SG_ASSISTANT_USERNAME": "assistant"}) as backend:
        assert requests.get(f"{backend.url}/auth/me", headers=bearer(""), timeout=5).status_code == 401
        assert requests.get(f"{backend.url}/auth/me", headers=bearer(TOKEN), timeout=5).status_code == 401


def test_token_set(database_path: str) -> None:
    env = {"SG_ASSISTANT_TOKEN": TOKEN, "SG_ASSISTANT_USERNAME": "assistant"}
    with Backend(database_path, env) as backend:
        # (b) Wrong / malformed token -> 401.
        for headers in (bearer("wrong-token"), bearer(TOKEN + "x"), {"Authorization": f"Basic {TOKEN}"}, {}):
            response = requests.get(f"{backend.url}/auth/me", headers=headers, timeout=5)
            assert response.status_code == 401, (headers, response.status_code)

        # (c) Valid token -> GET works as the mapped cashier user.
        me = requests.get(f"{backend.url}/auth/me", headers=bearer(TOKEN), timeout=5)
        assert me.status_code == 200, me.text
        assert me.json()["username"] == "assistant" and me.json()["role"] == "cashier"
        for path in ("/pcs", "/sessions/active", "/members", "/operators/current", "/dashboard/operator-sale"):
            response = requests.get(f"{backend.url}{path}", headers=bearer(TOKEN), timeout=5)
            assert response.status_code == 200, (path, response.status_code, response.text)
        report = requests.get(f"{backend.url}/sales/report", params={"period": "today"}, headers=bearer(TOKEN), timeout=5)
        assert report.status_code == 200, report.text
        # Existing role checks still apply: a cashier cannot read admin/owner/system routes.
        for path in ("/settings", "/income-groups", "/system/health"):
            response = requests.get(f"{backend.url}{path}", headers=bearer(TOKEN), timeout=5)
            assert response.status_code == 403, (path, response.status_code, response.text)

        # (d) Valid token + any non-GET -> 403, and nothing is written.
        response = requests.post(
            f"{backend.url}/shifts/open", json={"opening_cash": "0.00"}, headers=bearer(TOKEN), timeout=5
        )
        assert response.status_code == 403, response.text
        assert requests.get(f"{backend.url}/shifts/current", headers=bearer(TOKEN), timeout=5).json() is None
        for method in ("PUT", "PATCH", "DELETE"):
            response = requests.request(method, f"{backend.url}/members/anything", json={}, headers=bearer(TOKEN), timeout=5)
            assert response.status_code in {403, 405}, (method, response.status_code)
            assert response.status_code != 200
        response = requests.delete(f"{backend.url}/auth/me", headers=bearer(TOKEN), timeout=5)
        assert response.status_code in {403, 405}
        # Wrong token + POST stays 401 (does not reveal that the method is the problem).
        response = requests.post(
            f"{backend.url}/shifts/open", json={"opening_cash": "0.00"}, headers=bearer("nope"), timeout=5
        )
        assert response.status_code == 401

        # (e) Cookie flow unchanged while the token is configured.
        check_cookie_auth_unchanged(backend)


def test_token_user_must_exist_and_be_active(database_path: str) -> None:
    for username in ("retired", "does-not-exist", ""):
        env = {"SG_ASSISTANT_TOKEN": TOKEN, "SG_ASSISTANT_USERNAME": username}
        with Backend(database_path, env) as backend:
            response = requests.get(f"{backend.url}/auth/me", headers=bearer(TOKEN), timeout=5)
            assert response.status_code == 401, (username, response.status_code, response.text)


def test_direct_calls_unchanged() -> None:
    # get_current_user is also called directly (not via FastAPI) by other
    # smoke tests; with no request object the token path must stay inert.
    os.environ["SG_ASSISTANT_TOKEN"] = TOKEN
    os.environ["SG_ASSISTANT_USERNAME"] = "assistant"
    try:
        from fastapi import HTTPException

        from server.auth import authenticate_assistant_token, get_current_user

        assert authenticate_assistant_token(None) is None
        try:
            get_current_user(sg_session=None)
        except HTTPException as error:
            assert error.status_code == 401
        else:
            raise AssertionError("expected 401")
    finally:
        os.environ.pop("SG_ASSISTANT_TOKEN", None)
        os.environ.pop("SG_ASSISTANT_USERNAME", None)


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        # A fresh database per scenario, so a shift opened by one cookie
        # check can't change what the next one sees.
        for index, test in enumerate(
            (test_token_unset_is_inert, test_token_set, test_token_user_must_exist_and_be_active)
        ):
            database_path = str(Path(temp_directory) / f"assistant-token-{index}.sqlite3")
            bootstrap_users(database_path)
            test(database_path)
        test_direct_calls_unchanged()
    print("Assistant token auth smoke tests passed.")


if __name__ == "__main__":
    main()

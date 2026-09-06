"""Phase 1 -- unified backend admin authorization.

Every endpoint that was gated by ``require_admin_operator`` (username ==
"admin") is now gated by ``require_role("admin")`` (role rank >= admin).
``require_admin_operator`` itself is intentionally retained and unchanged
(other tests still import it).

Isolated: runs against a throwaway ``SECRET_GROUNDS_DB_PATH``. It never
opens, reads, or writes the real ``data/secret_grounds.db`` and changes no
role values.
"""

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# Every (method, path) that carried Depends(require_admin_operator) before
# Phase 1. If the list drifts from server/main.py this test fails loudly.
CONVERTED_ENDPOINTS = [
    ("GET", "/settings"),
    ("PUT", "/settings"),
    ("POST", "/settings/reset"),
    # NOTE: the /system/* routes were moved off require_role("admin") onto
    # the stricter require_system_admin (protected Admin only) in the
    # System-admin split. Their wiring is verified in
    # tests/smoke_system_admin_split.py instead.
    # NOTE: the /users management routes were removed in Phase 4; their
    # role-based gating was verified here while they existed.
    ("GET", "/admin/operators"),
    ("POST", "/admin/operators"),
    ("PUT", "/admin/operators/{operator_id}"),
    ("POST", "/admin/operators/{operator_id}/enable"),
    ("POST", "/admin/operators/{operator_id}/disable"),
    ("POST", "/admin/operators/{operator_id}/reset-password"),
    ("POST", "/admin/operators/{operator_id}/sign-out"),
    ("DELETE", "/admin/operators/{operator_id}"),
    ("GET", "/operator-activity"),
    # NOTE: /sales/report and /sales/export.csv were moved off
    # require_role("admin") onto require_role("cashier") -- Sales is a
    # read-only reporting view open to any operator. Their (now cashier)
    # gating is covered by the frontend access-model checks in
    # dashboard/src/tests/dashboard.test.ts.
]


def _all_dependency_calls(dependant, seen=None):
    if seen is None:
        seen = []
    if getattr(dependant, "call", None) is not None:
        seen.append(dependant.call)
    for sub in getattr(dependant, "dependencies", []) or []:
        _all_dependency_calls(sub, seen)
    return seen


def _is_require_role_admin(fn):
    if getattr(fn, "__qualname__", "") != "require_role.<locals>.dependency":
        return False
    closure = getattr(fn, "__closure__", None) or ()
    return any(getattr(cell, "cell_contents", None) == "admin" for cell in closure)


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        root = Path(temp_directory)
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(root / "authz.sqlite3")
        os.environ["SECRET_GROUNDS_BACKUP_DIR"] = str(root / "backups")
        os.environ["SECRET_GROUNDS_TEST_MODE"] = "true"

        from fastapi import HTTPException

        from server.database import initialize_database
        from server import main as m
        from server.auth import authenticate_quick_operator, require_role
        from server.schemas import UserRecord

        initialize_database()

        # --- 1. route wiring: each converted endpoint now depends on
        #        require_role("admin") and NOT on require_admin_operator ----
        routes_by_key = {}
        for route in m.app.routes:
            for method in getattr(route, "methods", None) or set():
                routes_by_key[(method, getattr(route, "path", None))] = route

        missing = []
        wrong = []
        for method, path in CONVERTED_ENDPOINTS:
            route = routes_by_key.get((method, path))
            if route is None or getattr(route, "dependant", None) is None:
                missing.append((method, path))
                continue
            calls = _all_dependency_calls(route.dependant)
            if m.require_admin_operator in calls:
                wrong.append((method, path, "still wired to require_admin_operator"))
            if not any(_is_require_role_admin(fn) for fn in calls):
                wrong.append((method, path, "missing require_role('admin') dependency"))
        assert not missing, f"converted routes not found on the app: {missing}"
        assert not wrong, f"converted routes with the wrong guard: {wrong}"
        # nothing anywhere in the app should still be wired to it
        still_wired = []
        for route in m.app.routes:
            if getattr(route, "dependant", None) is None:
                continue
            if m.require_admin_operator in _all_dependency_calls(route.dependant):
                still_wired.append((sorted(getattr(route, "methods", []) or []), getattr(route, "path", None)))
        assert not still_wired, f"routes still wired to require_admin_operator: {still_wired}"
        print(f"  {len(CONVERTED_ENDPOINTS)} endpoints verified on require_role('admin'); 0 left on require_admin_operator")

        # --- 2. guard semantics: cashier 403, admin allow, owner allow -----
        now = datetime.now(timezone.utc)

        def make_user(role: str, username: str) -> UserRecord:
            return UserRecord(
                user_id=f"user-{role}",
                username=username,
                display_name=role.title(),
                role=role,
                password_hash="x",
                is_active=True,
                created_at=now,
                updated_at=now,
                last_login_at=None,
            )

        admin_gate = require_role("admin")
        try:
            admin_gate(current_user=make_user("cashier", "cashier_one"))
            raise AssertionError("a cashier must be rejected by require_role('admin')")
        except HTTPException as error:
            assert error.status_code == 403, error.status_code
        # username is deliberately NOT "admin" -> proves the check is role-based
        assert admin_gate(current_user=make_user("admin", "some_admin")).role == "admin"
        assert admin_gate(current_user=make_user("owner", "some_owner")).role == "owner"
        print("  require_role('admin'): cashier -> 403, admin -> allow, owner -> allow")

        # --- 3. require_admin_operator retained + unchanged ----------------
        assert m.require_admin_operator(make_user("owner", "admin")).username == "admin"
        try:
            m.require_admin_operator(make_user("owner", "not_admin"))
            raise AssertionError("require_admin_operator must still gate on username == 'admin'")
        except HTTPException as error:
            assert error.status_code == 403
        print("  require_admin_operator still defined and still username-gated (unchanged)")

        # --- 4. legacy /users behaviour intact --------------------------
        users = m.user_repository.list_users()
        # Fresh DB: seed_family_operators seeds the 8 canonical operators.
        # Password-gated operators (Tyrone, Admin) default to role='owner';
        # quick-access operators default to role='cashier'. An owner can
        # promote anyone later via Staff.
        assert len(users) == 8, len(users)
        by_username = {u.username: u for u in users}
        assert "admin" in by_username
        assert all(u.role in ("owner", "admin", "cashier") for u in users)
        assert by_username["admin"].role == "owner"
        assert by_username["operator_tyrone"].role == "owner"
        assert all(
            by_username[name].role == "cashier"
            for name in (
                "operator_mama", "operator_neil", "operator_rogene",
                "operator_rojan", "operator_bel", "operator_karen",
            )
        ), [u.role for u in users]
        print(f"  /users repo path intact: {len(users)} seeded users, role column readable")

        # --- 5. operator authentication flow regression ------------------
        session_user = authenticate_quick_operator("operator_mama")
        assert session_user.user_id == "operator_mama"
        assert len(m.list_login_operators()) == 8
        print("  operator quick-login + login-operator listing unchanged")

    print("smoke_admin_authz_phase1 OK")


if __name__ == "__main__":
    main()

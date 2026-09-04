"""System-admin split -- infrastructure tier gated by is_protected.

Normal admin features stay role-based (``require_role("admin")``). A new
``require_system_admin`` dependency reserves the infrastructure tier for
the singular protected Admin account (``is_protected == 1``) -- NOT a role
check, NOT a username check. This covers:

* ``require_system_admin`` semantics: protected Admin allowed; a normal
  ``owner`` (e.g. Tyrone) -> 403; a cashier -> 403.
* ``require_role("admin")`` is unchanged: protected Admin and Tyrone both
  allowed; cashier denied.
* route wiring: every ``/system/*`` route plus ``GET /remote-commands``,
  ``POST /commands/test-ping``, and the PC power/wake/MAC routes
  (``POST /pcs/{pc_name}/power/{command_type}``, ``POST /pcs/{pc_name}/wake``,
  ``PUT /pcs/{pc_name}/mac``) depend on ``require_system_admin``;
  ``GET /operator-activity`` still depends on ``require_role("admin")`` and
  NOT on ``require_system_admin``.
* ``is_protected`` flows through ``row_to_user`` and ``to_public_user`` so
  ``/auth/me`` can surface it.

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

# Every route that must now depend on require_system_admin.
SYSTEM_ADMIN_ENDPOINTS = [
    ("GET", "/system/health"),
    ("GET", "/system/database"),
    ("GET", "/system/backups"),
    ("POST", "/system/backup"),
    ("POST", "/system/restore"),
    ("DELETE", "/system/backups/{filename}"),
    ("GET", "/system/backups/{filename}/download"),
    ("GET", "/system/storage"),
    ("GET", "/system/info"),
    ("GET", "/system/logs"),
    ("GET", "/system/clients"),
    ("GET", "/remote-commands"),
    ("POST", "/commands/test-ping"),
    # PC power controls + the MAC editor that feeds Wake-on-LAN are locked
    # to the protected Admin account (dashboard Power Controls / Advanced
    # Details are hidden for everyone else).
    ("POST", "/pcs/{pc_name}/power/{command_type}"),
    ("POST", "/pcs/{pc_name}/wake"),
    ("PUT", "/pcs/{pc_name}/mac"),
]

# Routes that stay on the plain role gate -- must NOT pick up
# require_system_admin.
ROLE_ADMIN_ENDPOINTS = [
    ("GET", "/operator-activity"),
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
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(root / "sysadmin.sqlite3")
        os.environ["SECRET_GROUNDS_BACKUP_DIR"] = str(root / "backups")
        os.environ["SECRET_GROUNDS_TEST_MODE"] = "true"

        from fastapi import HTTPException

        from server.database import initialize_database
        from server import main as m
        from server.auth import (
            require_role,
            require_system_admin,
            to_public_user,
        )
        from server.schemas import UserPublic, UserRecord

        initialize_database()

        # --- 1. route wiring --------------------------------------------------
        routes_by_key = {}
        for route in m.app.routes:
            for method in getattr(route, "methods", None) or set():
                routes_by_key[(method, getattr(route, "path", None))] = route

        missing = []
        wrong = []
        for method, path in SYSTEM_ADMIN_ENDPOINTS:
            route = routes_by_key.get((method, path))
            if route is None or getattr(route, "dependant", None) is None:
                missing.append((method, path))
                continue
            calls = _all_dependency_calls(route.dependant)
            if require_system_admin not in calls:
                wrong.append((method, path, "missing require_system_admin"))
            if any(_is_require_role_admin(fn) for fn in calls):
                wrong.append((method, path, "still carries require_role('admin')"))
        assert not missing, f"system-admin routes not found on the app: {missing}"
        assert not wrong, f"system-admin routes with the wrong guard: {wrong}"

        role_wrong = []
        for method, path in ROLE_ADMIN_ENDPOINTS:
            route = routes_by_key.get((method, path))
            assert route is not None and getattr(route, "dependant", None) is not None, (method, path)
            calls = _all_dependency_calls(route.dependant)
            if require_system_admin in calls:
                role_wrong.append((method, path, "must not depend on require_system_admin"))
            if not any(_is_require_role_admin(fn) for fn in calls):
                role_wrong.append((method, path, "lost require_role('admin')"))
        assert not role_wrong, f"role-gated routes drifted: {role_wrong}"
        print(
            f"  {len(SYSTEM_ADMIN_ENDPOINTS)} routes on require_system_admin; "
            f"{len(ROLE_ADMIN_ENDPOINTS)} still on require_role('admin')"
        )

        # --- 2. guard semantics --------------------------------------------
        now = datetime.now(timezone.utc)

        def make_user(role: str, username: str, is_protected: bool) -> UserRecord:
            return UserRecord(
                user_id=f"user-{username}",
                username=username,
                display_name=username.title(),
                role=role,
                is_protected=is_protected,
                password_hash="x",
                is_active=True,
                created_at=now,
                updated_at=now,
                last_login_at=None,
            )

        protected_admin = make_user("owner", "admin", True)
        tyrone = make_user("owner", "operator_admin", False)
        cashier = make_user("cashier", "operator_bel", False)

        # require_system_admin: only the protected account passes.
        assert require_system_admin(current_user=protected_admin).is_protected is True
        for denied in (tyrone, cashier):
            try:
                require_system_admin(current_user=denied)
                raise AssertionError(
                    f"{denied.username} (protected={denied.is_protected}) must be 403 by require_system_admin"
                )
            except HTTPException as error:
                assert error.status_code == 403, error.status_code
        print("  require_system_admin: protected Admin -> allow, owner Tyrone -> 403, cashier -> 403")

        # require_role('admin') unchanged: both owners allowed, cashier denied.
        admin_gate = require_role("admin")
        assert admin_gate(current_user=protected_admin).role == "owner"
        assert admin_gate(current_user=tyrone).role == "owner"
        try:
            admin_gate(current_user=cashier)
            raise AssertionError("cashier must be 403 by require_role('admin')")
        except HTTPException as error:
            assert error.status_code == 403, error.status_code
        print("  require_role('admin'): protected Admin + Tyrone -> allow, cashier -> 403")

        # --- 3. is_protected flows through the public surfaces -------------
        public = to_public_user(protected_admin)
        assert isinstance(public, UserPublic)
        assert public.is_protected is True
        assert to_public_user(tyrone).is_protected is False

        seeded_admin = m.user_repository.get_by_username("admin")
        assert seeded_admin is not None and seeded_admin.is_protected is True, seeded_admin
        others = [u for u in m.user_repository.list_users() if u.username != "admin"]
        assert others and all(u.is_protected is False for u in others), [
            (u.username, u.is_protected) for u in others
        ]
        print("  is_protected: seeded 'admin' -> True, every other operator -> False; to_public_user carries it")

    print("smoke_system_admin_split OK")


if __name__ == "__main__":
    main()

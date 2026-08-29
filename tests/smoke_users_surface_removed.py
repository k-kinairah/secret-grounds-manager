"""Phase 4 -- the legacy /users management surface is gone, the auth core is not.

Isolated: throwaway SECRET_GROUNDS_DB_PATH. No production DB access, no role
changes.
"""

import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

REMOVED = {
    ("GET", "/users"),
    ("POST", "/users"),
    ("PUT", "/users/{user_id}"),
    ("PATCH", "/users/{user_id}/status"),
    ("POST", "/users/{user_id}/reset-password"),
}


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "users_removed.sqlite3")
        os.environ["SECRET_GROUNDS_TEST_MODE"] = "true"

        from server.database import initialize_database
        from server import main as m

        initialize_database()

        # --- 1. the five /users management routes no longer exist ----------
        registered = set()
        for route in m.app.routes:
            for method in getattr(route, "methods", None) or set():
                registered.add((method, getattr(route, "path", None)))
        still_present = REMOVED & registered
        assert not still_present, f"/users management routes still registered: {sorted(still_present)}"
        # nothing else accidentally removed: no leftover /users* path at all
        leftover = {path for (_, path) in registered if path and path.startswith("/users")}
        assert not leftover, f"unexpected /users* paths still registered: {sorted(leftover)}"
        assert not hasattr(m, "list_users") or not callable(getattr(m, "list_users")), \
            "server.main.list_users route handler should be gone"
        print("  5 /users management routes removed; no /users* path remains")

        # --- 2. Staff /admin/operators remains available ------------------
        assert ("GET", "/admin/operators") in registered
        assert ("POST", "/admin/operators") in registered
        assert ("PUT", "/admin/operators/{operator_id}") in registered
        print("  /admin/operators Staff routes intact")

        # --- 3. Staff role creation / update still works ------------------
        from server.operator_repository import OperatorRepository
        from server.schemas import OperatorCreateRequest, OperatorUpdateRequest
        operators = OperatorRepository()
        actor = m.user_repository.get_by_username("admin")
        assert actor is not None
        made = operators.create_operator(
            OperatorCreateRequest(username="p4_admin", display_name="P4 Admin", access_method="quick", role="admin"),
            actor,
        )
        assert made.role == "admin"
        changed = operators.update_operator(
            made.operator_id,
            OperatorUpdateRequest(username="p4_admin", display_name="P4 Admin", access_method="quick", is_active=True, role="owner"),
            actor,
        )
        assert changed.role == "owner"
        print("  Staff create-with-role + role update still work")

        # --- 4. UserRepository auth-core methods remain intact -----------
        from server.user_repository import UserRepository
        users = UserRepository()
        for name in (
            "list_users", "create_user", "update_user", "set_active", "reset_password",
            "get_user", "get_by_username", "mark_login", "count_active_owners",
        ):
            assert callable(getattr(users, name, None)), f"UserRepository.{name} missing"
        assert users.get_by_username("admin") is not None
        assert users.count_active_owners() >= 1
        print("  UserRepository auth-core methods intact")

        # --- 5/6. UserRole + require_role("admin") remain intact ---------
        from server.schemas import UserRole  # noqa: F401
        from server.auth import require_role, ROLE_RANK
        from datetime import datetime, timezone
        from server.schemas import UserRecord
        now = datetime.now(timezone.utc)
        cashier = UserRecord(user_id="u-c", username="c", display_name="C", role="cashier",
                             password_hash="x", is_active=True, created_at=now, updated_at=now, last_login_at=None)
        owner = cashier.model_copy(update={"role": "owner"})
        gate = require_role("admin")
        from fastapi import HTTPException
        try:
            gate(current_user=cashier)
            raise AssertionError("cashier must be rejected")
        except HTTPException as e:
            assert e.status_code == 403
        assert gate(current_user=owner).role == "owner"
        assert ROLE_RANK == {"cashier": 1, "admin": 2, "owner": 3}
        print("  UserRole + require_role('admin') intact")

        # --- 7. /auth/login remains intact -----------------------------
        assert ("POST", "/auth/login") in registered
        from server.auth import authenticate_login
        assert callable(authenticate_login)
        # operator login flows also intact
        from server.auth import authenticate_quick_operator
        assert authenticate_quick_operator("operator_mama").user_id == "operator_mama"
        print("  /auth/login + operator authentication intact")

    print("smoke_users_surface_removed OK")


if __name__ == "__main__":
    main()

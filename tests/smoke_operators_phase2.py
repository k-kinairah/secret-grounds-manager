"""Phase 2 -- role management in the Staff/Operators editor.

OperatorRepository.create_operator / update_operator now carry a role, and
the last-active-owner protection (mirrored from UserRepository) applies to
operator demote / disable / archive. The protected Admin rules and the
existing UserRepository protection are unchanged.

Isolated: throwaway SECRET_GROUNDS_DB_PATH. Never touches the real
data/secret_grounds.db and changes no production role values.
"""

import os
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def expect_http_error(status_code, action, label, detail_contains=None):
    try:
        action()
    except HTTPException as error:
        assert error.status_code == status_code, f"{label}: got {error.status_code}"
        if detail_contains is not None:
            assert detail_contains.lower() in str(error.detail).lower(), f"{label}: {error.detail!r}"
        return
    raise AssertionError(label)


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "operators_phase2.sqlite3")
        os.environ["SECRET_GROUNDS_TEST_MODE"] = "true"

        from server.auth import authenticate_quick_operator
        from server.database import get_connection, initialize_database
        from server.operator_repository import OperatorRepository
        from server.user_repository import UserRepository
        from server.schemas import (
            OperatorCreateRequest,
            OperatorUpdateRequest,
            UserCreateRequest,
            UserUpdateRequest,
            UserStatusUpdate,
        )
        from server.main import list_login_operators

        initialize_database()
        operators = OperatorRepository()
        users = UserRepository()
        actor = users.get_by_username("admin")
        assert actor is not None

        def upd(username, display_name, access_method, is_active, role=None):
            return OperatorUpdateRequest(
                username=username,
                display_name=display_name,
                access_method=access_method,
                is_active=is_active,
                role=role,
            )

        # --- 1/2/3: create with each role -----------------------------------
        c1 = operators.create_operator(
            OperatorCreateRequest(username="cash_one", display_name="Cash One", access_method="quick"),
            actor,
        )
        assert c1.role == "cashier", c1.role  # default when not supplied
        a1 = operators.create_operator(
            OperatorCreateRequest(username="adm_one", display_name="Adm One", access_method="quick", role="admin"),
            actor,
        )
        assert a1.role == "admin", a1.role
        o1 = operators.create_operator(
            OperatorCreateRequest(username="own_one", display_name="Own One", access_method="quick", role="owner"),
            actor,
        )
        assert o1.role == "owner", o1.role
        # role is what the Staff list will show
        listed = {op.operator_id: op.role for op in operators.list_operators()}
        assert listed[c1.operator_id] == "cashier"
        assert listed[a1.operator_id] == "admin"
        assert listed[o1.operator_id] == "owner"
        print("  create operator with cashier / admin / owner")

        # --- 4: cashier -> admin ------------------------------------------
        r = operators.update_operator(c1.operator_id, upd("cash_one", "Cash One", "quick", True, "admin"), actor)
        assert r.role == "admin", r.role
        # --- 5: admin -> owner ------------------------------------------
        r = operators.update_operator(c1.operator_id, upd("cash_one", "Cash One", "quick", True, "owner"), actor)
        assert r.role == "owner", r.role
        print("  change cashier -> admin -> owner")

        # role change writes an operator_role_changed audit row
        with get_connection() as connection:
            role_events = connection.execute(
                "SELECT COUNT(*) FROM operator_audit_events WHERE event_type = 'operator_role_changed'"
            ).fetchone()[0]
        assert role_events >= 2, role_events

        # --- 6: owner -> cashier while another active owner exists ---------
        # 8 seeded owners + o1 + c1 (now owner) = plenty; demoting one is fine.
        r = operators.update_operator(c1.operator_id, upd("cash_one", "Cash One", "quick", True, "cashier"), actor)
        assert r.role == "cashier", r.role
        # Demote a genuine owner (o1) while admin + tyrone + others remain owners.
        assert operators.get_operator(o1.operator_id).role == "owner"
        r = operators.update_operator(o1.operator_id, upd("own_one", "Own One", "quick", True, "cashier"), actor)
        assert r.role == "cashier"
        print("  owner -> cashier allowed while other active owners remain")

        # --- 7/8/9: last-active-owner protection --------------------------
        # Build a state with exactly ONE active owner that is NOT protected:
        # neutralise every other owner directly in the throwaway DB, then
        # create a single plain owner. (Direct SQL is setup only -- this is a
        # temp test DB, never production.)
        with get_connection() as connection:
            connection.execute("UPDATE users SET role = 'cashier' WHERE role = 'owner'")
        solo = operators.create_operator(
            OperatorCreateRequest(username="solo_owner", display_name="Solo Owner", access_method="quick", role="owner"),
            actor,
        )
        with get_connection() as connection:
            active_owners = connection.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'owner' AND is_active = 1"
            ).fetchone()[0]
        assert active_owners == 1, active_owners

        expect_http_error(
            400,
            lambda: operators.update_operator(
                solo.operator_id, upd("solo_owner", "Solo Owner", "quick", True, "cashier"), actor
            ),
            "demoting the last active owner must be rejected",
            "last active owner",
        )
        expect_http_error(
            400,
            lambda: operators.update_operator(
                solo.operator_id, upd("solo_owner", "Solo Owner", "quick", False, "owner"), actor
            ),
            "deactivating the last active owner via update must be rejected",
            "last active owner",
        )
        expect_http_error(
            400,
            lambda: operators.set_enabled(solo.operator_id, False, actor),
            "disabling the last active owner must be rejected",
            "last active owner",
        )
        expect_http_error(
            400,
            lambda: operators.archive_operator(solo.operator_id, actor),
            "archiving the last active owner must be rejected",
            "last active owner",
        )
        # the guarded row is unchanged
        still = operators.get_operator(solo.operator_id)
        assert still is not None and still.role == "owner" and still.is_active and still.archived_at is None
        print("  reject demote / deactivate / disable / archive of the last active owner")

        # a second active owner unblocks all of the above
        operators.create_operator(
            OperatorCreateRequest(username="backup_owner", display_name="Backup Owner", access_method="quick", role="owner"),
            actor,
        )
        r = operators.update_operator(solo.operator_id, upd("solo_owner", "Solo Owner", "quick", True, "cashier"), actor)
        assert r.role == "cashier"
        print("  same operations succeed once a second active owner exists")

        # --- 10: protected Admin rules unchanged ------------------------
        expect_http_error(
            400,
            lambda: operators.set_enabled("operator_admin", False, actor),
            "protected Admin cannot be disabled",
            "protected admin",
        )
        expect_http_error(
            400,
            lambda: operators.archive_operator("operator_admin", actor),
            "protected Admin cannot be archived",
            "protected admin",
        )
        expect_http_error(
            400,
            lambda: operators.update_operator("operator_admin", upd("admin", "Admin", "password", True, "cashier"), actor),
            "protected Admin cannot be demoted to cashier",
            "protected admin rules",
        )
        admin_now = operators.get_operator("operator_admin")
        assert admin_now is not None and admin_now.is_protected
        # admin -> admin/owner is allowed by the protected policy (>= admin)
        r = operators.update_operator("operator_admin", upd("admin", "Admin", "password", True, "admin"), actor)
        assert r.role == "admin"
        r = operators.update_operator("operator_admin", upd("admin", "Admin", "password", True, "owner"), actor)
        assert r.role == "owner"
        assert r.is_protected
        print("  protected Admin: no disable/archive/demote-below-admin; admin<->owner allowed")

        # --- 11: operator authentication still works --------------------
        session_user = authenticate_quick_operator("operator_mama")
        assert session_user.user_id == "operator_mama"
        assert len(list_login_operators()) >= 8
        print("  operator quick-login + login-operator listing unchanged")

        # --- 12: legacy /users repo still works, protection intact ---------
        u = users.create_user(UserCreateRequest(username="legacy_user", display_name="Legacy", password="password123", role="cashier"))
        assert u.role == "cashier"
        u2 = users.update_user(u.user_id, UserUpdateRequest(username="legacy_user", display_name="Legacy", role="admin"))
        assert u2 is not None and u2.role == "admin"
        # its own last-owner guard still there (unchanged code path)
        with get_connection() as connection:
            connection.execute("UPDATE users SET role = 'cashier' WHERE role = 'owner'")
        lone = users.create_user(UserCreateRequest(username="lone_owner", display_name="Lone", password="password123", role="owner"))
        expect_http_error(
            400,
            lambda: users.update_user(lone.user_id, UserUpdateRequest(username="lone_owner", display_name="Lone", role="cashier")),
            "UserRepository last-owner demote guard still active",
            "last active owner",
        )
        expect_http_error(
            400,
            lambda: users.set_active(lone.user_id, UserStatusUpdate(is_active=False)),
            "UserRepository last-owner disable guard still active",
            "last active owner",
        )
        assert len(users.list_users()) >= 10
        print("  legacy UserRepository CRUD + its own last-owner protection unchanged")

    print("smoke_operators_phase2 OK")


if __name__ == "__main__":
    main()

import os
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException, Response

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def expect_http_error(status_code: int, action, label: str) -> None:
    try:
        action()
    except HTTPException as error:
        assert error.status_code == status_code, label
    else:
        raise AssertionError(label)


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "operators.sqlite3")
        os.environ["SECRET_GROUNDS_TEST_MODE"] = "true"

        from server.auth import authenticate_password_operator, authenticate_quick_operator
        from server.database import get_connection, initialize_database
        from server.main import (
            archive_admin_operator,
            create_admin_operator,
            disable_admin_operator,
            list_admin_operators,
            list_login_operators,
            password_operator_login,
            quick_operator_login,
            require_admin_operator,
            reset_admin_operator_password,
            sign_out_admin_operator,
            update_admin_operator,
        )
        from server.passwords import verify_password
        from server.schemas import (
            OperatorCreateRequest,
            OperatorPasswordResetRequest,
            OperatorUpdateRequest,
            PasswordOperatorLoginRequest,
            QuickOperatorLoginRequest,
        )
        from server.auth import user_repository

        initialize_database()
        admin = user_repository.get_by_username("admin")
        mama = user_repository.get_user("operator_mama")
        assert admin is not None and mama is not None
        assert require_admin_operator(admin).user_id == admin.user_id
        expect_http_error(403, lambda: require_admin_operator(mama), "Normal operator cannot manage operators")

        operators = list_admin_operators(admin)
        assert len(operators) == 8
        assert any(operator.username == "admin" and operator.is_protected for operator in operators)

        quick_operator = create_admin_operator(
            OperatorCreateRequest(
                display_name="Future Cashier",
                username="Cashier_2",
                access_method="quick",
                is_active=True,
            ),
            admin,
        )
        assert quick_operator.username == "cashier_2"
        assert quick_operator.role == "cashier"
        assert quick_operator.access_method == "quick"

        password_operator = create_admin_operator(
            OperatorCreateRequest(
                display_name="Password Cashier",
                username="password_cashier",
                access_method="password",
                password="cashier-pass",
                is_active=True,
            ),
            admin,
        )
        with get_connection() as connection:
            stored = connection.execute(
                "SELECT password_hash FROM users WHERE user_id = ?",
                (password_operator.operator_id,),
            ).fetchone()
        assert stored is not None
        assert stored["password_hash"] != "cashier-pass"
        assert verify_password("cashier-pass", stored["password_hash"])

        expect_http_error(
            409,
            lambda: create_admin_operator(
                OperatorCreateRequest(
                    display_name="Duplicate",
                    username="cashier_2",
                    access_method="quick",
                    is_active=True,
                ),
                admin,
            ),
            "Duplicate username rejected",
        )
        expect_http_error(
            422,
            lambda: create_admin_operator(
                OperatorCreateRequest(
                    display_name="Invalid",
                    username="bad user!",
                    access_method="quick",
                    is_active=True,
                ),
                admin,
            ),
            "Invalid username rejected",
        )

        login_operators = list_login_operators()
        assert any(operator.operator_id == quick_operator.operator_id for operator in login_operators)
        quick_response = quick_operator_login(
            QuickOperatorLoginRequest(operator_id=quick_operator.operator_id),
            Response(),
        )
        assert quick_response.user_id == quick_operator.operator_id
        signed_out = sign_out_admin_operator(quick_operator.operator_id, admin)
        assert signed_out.active_session_count == 0
        expect_http_error(
            409,
            lambda: sign_out_admin_operator(quick_operator.operator_id, admin),
            "Sign out rejects operators with no active dashboard session",
        )

        disabled = disable_admin_operator(quick_operator.operator_id, admin)
        assert disabled.is_active is False
        expect_http_error(
            404,
            lambda: authenticate_quick_operator(quick_operator.operator_id),
            "Disabled quick operator cannot authenticate",
        )
        assert all(operator.operator_id != quick_operator.operator_id for operator in list_login_operators())

        archived = archive_admin_operator(quick_operator.operator_id, admin)
        assert archived.archived_at is not None
        expect_http_error(
            403,
            lambda: authenticate_quick_operator(quick_operator.operator_id),
            "Archived quick operator cannot authenticate",
        )

        admin_operator = next(operator for operator in operators if operator.username == "admin")
        expect_http_error(
            400,
            lambda: disable_admin_operator(admin_operator.operator_id, admin),
            "Protected Admin cannot be disabled",
        )
        expect_http_error(
            400,
            lambda: archive_admin_operator(admin_operator.operator_id, admin),
            "Protected Admin cannot be archived",
        )
        expect_http_error(
            400,
            lambda: update_admin_operator(
                admin_operator.operator_id,
                OperatorUpdateRequest(
                    display_name="Admin",
                    username="admin",
                    access_method="quick",
                    is_active=True,
                ),
                admin,
            ),
            "Protected Admin cannot become Quick Access",
        )

        assert authenticate_password_operator(password_operator.operator_id, "cashier-pass").user_id == password_operator.operator_id
        assert password_operator_login(
            PasswordOperatorLoginRequest(operator_id=password_operator.operator_id, password="cashier-pass"),
            Response(),
        ).user_id == password_operator.operator_id
        reset_admin_operator_password(
            password_operator.operator_id,
            OperatorPasswordResetRequest(password="new-cashier-pass"),
            admin,
        )
        expect_http_error(
            401,
            lambda: authenticate_password_operator(password_operator.operator_id, "cashier-pass"),
            "Password reset invalidates old password",
        )
        assert authenticate_password_operator(password_operator.operator_id, "new-cashier-pass").user_id == password_operator.operator_id

        with get_connection() as connection:
            audit_events = [
                row["event_type"]
                for row in connection.execute(
                    "SELECT event_type FROM operator_audit_events WHERE event_type LIKE 'operator_%'"
                ).fetchall()
            ]
            historical_user = connection.execute(
                "SELECT display_name FROM users WHERE user_id = ?",
                (quick_operator.operator_id,),
            ).fetchone()
        assert historical_user["display_name"] == "Future Cashier"
        for event_type in [
            "operator_created",
            "operator_disabled",
            "operator_signed_out",
            "operator_archived",
            "operator_password_reset",
        ]:
            assert event_type in audit_events

        print("Operator management smoke test passed.")


if __name__ == "__main__":
    main()

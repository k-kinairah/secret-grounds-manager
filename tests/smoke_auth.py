import os
import sys
import tempfile
from http.cookies import SimpleCookie
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException, Response

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "auth.sqlite3")
        os.environ["SECRET_GROUNDS_APP_MODE"] = "production"
        os.environ["SECRET_GROUNDS_CORS_ORIGINS"] = "http://127.0.0.1:3001"
        os.environ["SECRET_GROUNDS_AUTH_SECURE_COOKIE"] = "false"
        os.environ["SECRET_GROUNDS_AUTH_SAMESITE"] = "lax"

        from server.auth import (
            authenticate_admin_operator,
            auth_session_repository,
            authenticate_login,
            authenticate_password_operator,
            authenticate_quick_operator,
            get_current_user,
            list_family_operator_access,
            require_owner,
            require_role,
            set_auth_cookie,
        )
        from server.database import get_connection, initialize_database
        from server.main import (
            quick_operator_login,
            create_rate,
            get_allowed_cors_origins,
            get_me,
            get_me_alias,
            get_rates,
            get_registered_pcs,
            list_admin_operators,
            list_members,
            list_operator_activity,
            list_operators,
            logout,
            open_shift,
            record_payment,
            require_admin_operator,
            session_repository,
        )
        from server.passwords import verify_password
        from server.schemas import (
            LoginRequest,
            PaymentCreateRequest,
            RateProfileCreate,
            ShiftOpenRequest,
            SessionRecord,
            UserCreateRequest,
            QuickOperatorLoginRequest,
            UserStatusUpdate,
            UserUpdateRequest,
        )
        from server.time_utils import current_utc_time
        from server.user_repository import UserRepository

        initialize_database()
        user_repository = UserRepository()

        operators = list_family_operator_access()
        assert [operator.display_name for operator in operators] == [
            "Mama",
            "Neil",
            "Tyrone",
            "Rogene",
            "Rojan",
            "Bel",
            "Karen",
            "Admin",
        ]
        assert all(
            operator.user is not None
            for operator in operators
        )
        assert authenticate_quick_operator("operator_mama").display_name == "Mama"
        assert authenticate_quick_operator("operator_neil").display_name == "Neil"
        for operator_id in [
            "operator_rogene",
            "operator_rojan",
            "operator_bel",
            "operator_karen",
        ]:
            assert authenticate_quick_operator(operator_id).user_id == operator_id
        # Tyrone is password-required (server/operator_config.py); quick access
        # must be refused and the seeded password login must succeed.
        try:
            authenticate_quick_operator("operator_tyrone")
        except HTTPException as error:
            assert error.status_code == 403
        else:
            raise AssertionError("Tyrone quick access should be rejected (password required)")
        assert (
            authenticate_password_operator("operator_tyrone", "admin12345").user_id
            == "operator_tyrone"
        )
        try:
            authenticate_quick_operator("operator_not_real")
        except HTTPException as error:
            assert error.status_code == 403
        else:
            raise AssertionError("Arbitrary operator quick access should be rejected")

        try:
            authenticate_admin_operator("wrong-password")
        except HTTPException as error:
            assert error.status_code == 401
        else:
            raise AssertionError("Wrong Admin password should be rejected")
        assert authenticate_admin_operator("admin12345").display_name == "Admin"
        seeded_admin = user_repository.get_by_username("admin")
        seeded_mama = user_repository.get_user("operator_mama")
        assert seeded_admin is not None
        assert seeded_mama is not None
        assert require_admin_operator(seeded_admin).user_id == seeded_admin.user_id
        try:
            require_admin_operator(seeded_mama)
        except HTTPException as error:
            assert error.status_code == 403
        else:
            raise AssertionError("Family operators must not access Admin-only pages")
        assert len(list_admin_operators(seeded_admin)) == 8
        operator_response = Response()
        quick_operator_login(
            QuickOperatorLoginRequest(operator_id="operator_rogene"),
            operator_response,
        )
        with get_connection() as connection:
            operator_audit_row = connection.execute(
                """
                SELECT operator_display_name
                FROM operator_audit_events
                WHERE event_type = 'operator_changed'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
        assert operator_audit_row["operator_display_name"] == "Rogene"

        assert get_allowed_cors_origins() == ["http://127.0.0.1:3001"]

        login_response = Response()
        public_operator = quick_operator_login(
            QuickOperatorLoginRequest(operator_id="operator_mama"),
            login_response,
        )
        assert public_operator.username == "operator_mama"
        set_cookie = next(
            value.decode("latin-1")
            for name, value in login_response.raw_headers
            if name == b"set-cookie"
        )
        assert "sg_session=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Path=/" in set_cookie
        assert "SameSite=lax" in set_cookie
        assert "Secure" not in set_cookie
        assert "Domain=" not in set_cookie
        parsed_cookie = SimpleCookie(set_cookie)
        session_token = parsed_cookie["sg_session"].value

        current_operator = get_current_user(sg_session=session_token)
        assert get_me(current_operator).username == "operator_mama"
        assert get_me_alias(current_operator).username == "operator_mama"
        assert isinstance(list_members(None, current_operator), list)
        assert isinstance(get_rates(False, current_operator), list)
        assert isinstance(list_operators(current_operator), list)
        assert isinstance(get_registered_pcs(current_operator), list)

        refreshed_operator = get_current_user(sg_session=session_token)
        assert refreshed_operator.username == "operator_mama"

        logout_response = Response()
        assert logout(logout_response, session_token) == {"message": "Logged out"}
        try:
            get_current_user(sg_session=session_token)
        except HTTPException as error:
            assert error.status_code == 401
        else:
            raise AssertionError("Logged-out session should be rejected")

        owner = user_repository.create_user(
            UserCreateRequest(
                username="Owner",
                display_name="Owner",
                password="owner-pass",
                role="owner",
            )
        )
        stored_owner = user_repository.get_user(owner.user_id)
        assert stored_owner is not None
        assert stored_owner.password_hash != "owner-pass"
        assert verify_password("owner-pass", stored_owner.password_hash)

        try:
            get_current_user(sg_session=None)
        except HTTPException as error:
            assert error.status_code == 401
        else:
            raise AssertionError("Missing auth cookie should be rejected")

        try:
            authenticate_login(LoginRequest(username="owner", password="wrong-password"))
        except HTTPException as error:
            assert error.status_code == 401
        else:
            raise AssertionError("Bad password should be rejected")

        logged_in_owner = authenticate_login(
            LoginRequest(username="owner", password="owner-pass")
        )
        response = Response()
        token, _ = auth_session_repository.create_session(logged_in_owner.user_id, 3600)
        set_auth_cookie(response, token)
        assert any(
            header_name == b"set-cookie" and b"httponly" in header_value.lower()
            for header_name, header_value in response.raw_headers
        )
        assert get_current_user(sg_session=token).user_id == owner.user_id

        cashier = user_repository.create_user(
            UserCreateRequest(
                username="cashier",
                display_name="Cashier",
                password="cashier-pass",
                role="cashier",
            )
        )
        admin = user_repository.create_user(
            UserCreateRequest(
                username="admin_test",
                display_name="Admin Test",
                password="admin-pass",
                role="admin",
            )
        )
        cashier_record = user_repository.get_user(cashier.user_id)
        admin_record = user_repository.get_user(admin.user_id)
        assert cashier_record is not None
        assert admin_record is not None

        try:
            user_repository.update_user(
                cashier.user_id,
                UserUpdateRequest(
                    username="admin",
                    display_name="Duplicate",
                    role="cashier",
                ),
            )
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Duplicate username edits should be rejected")

        assert require_role("cashier")(cashier_record).user_id == cashier.user_id
        assert require_role("admin")(admin_record).user_id == admin.user_id
        assert require_owner(stored_owner).user_id == owner.user_id

        try:
            require_role("admin")(cashier_record)
        except HTTPException as error:
            assert error.status_code == 403
        else:
            raise AssertionError("Cashier should not pass admin gate")

        try:
            require_owner(admin_record)
        except HTTPException as error:
            assert error.status_code == 403
        else:
            raise AssertionError("Admin should not pass owner gate")

        updated_owner_status = user_repository.set_active(
            owner.user_id,
            UserStatusUpdate(is_active=False),
        )
        assert updated_owner_status is not None
        assert updated_owner_status.is_active is False
        user_repository.set_active(
            owner.user_id,
            UserStatusUpdate(is_active=True),
        )
        quick_operator_login(
            QuickOperatorLoginRequest(operator_id="operator_rogene"),
            Response(),
        )

        now = current_utc_time()
        session_repository.create_session(
            SessionRecord(
                session_id="auth-session",
                pc_name="PC-AUTH",
                status="active",
                started_at=now,
                scheduled_end_at=now,
                ended_at=None,
                original_seconds=300,
                added_seconds=0,
                total_seconds=300,
                amount_paid=None,
                ended_reason=None,
                created_at=now,
                base_amount=Decimal("20.00"),
                total_amount=Decimal("20.00"),
                created_by_user_id=owner.user_id,
            )
        )
        open_shift(ShiftOpenRequest(opening_cash=Decimal("0.00")), stored_owner)
        billing = record_payment(
            "auth-session",
            PaymentCreateRequest(
                payment_method="cash",
                amount=Decimal("20.00"),
                cash_received=Decimal("20.00"),
            ),
            stored_owner,
        )
        assert billing.payment_status == "paid"
        with get_connection() as connection:
            payment_row = connection.execute(
                "SELECT recorded_by_user_id FROM payments WHERE session_id = ?",
                ("auth-session",),
            ).fetchone()
        assert payment_row["recorded_by_user_id"] == "operator_rogene"
        activity = list_operator_activity("all", "operator_rogene", "payment", 50, seeded_admin)
        assert any(
            item.operator_name == "Rogene"
            and item.amount == Decimal("20.00")
            and item.payment_method == "cash"
            and item.pc_name == "PC-AUTH"
            for item in activity
        )

        created_rate = create_rate(
            RateProfileCreate(
                name="Admin Package",
                duration_seconds=600,
                price=Decimal("1.00"),
                rate_type="package",
                sort_order=91,
            ),
            admin_record,
        )
        with get_connection() as connection:
            audit_row = connection.execute(
                """
                SELECT performed_by_user_id
                FROM rate_audit_events
                WHERE rate_id = ?
                """,
                (created_rate.rate_id,),
            ).fetchone()
        assert audit_row["performed_by_user_id"] == admin.user_id

        updated_cashier = user_repository.set_active(
            cashier.user_id,
            UserStatusUpdate(is_active=False),
        )
        assert updated_cashier is not None
        assert updated_cashier.is_active is False
        try:
            authenticate_login(LoginRequest(username="cashier", password="cashier-pass"))
        except HTTPException as error:
            assert error.status_code == 401
        else:
            raise AssertionError("Inactive users should not log in")

        print("Authentication smoke test passed.")


if __name__ == "__main__":
    main()

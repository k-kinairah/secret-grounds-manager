import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException, Response

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "auth.sqlite3")

        from server.auth import (
            auth_session_repository,
            authenticate_login,
            get_current_user,
            require_owner,
            require_role,
            set_auth_cookie,
        )
        from server.database import get_connection, initialize_database
        from server.main import (
            create_rate,
            open_shift,
            record_payment,
            session_repository,
            update_user_status,
        )
        from server.passwords import verify_password
        from server.schemas import (
            LoginRequest,
            PaymentCreateRequest,
            RateProfileCreate,
            ShiftOpenRequest,
            SessionRecord,
            UserCreateRequest,
            UserStatusUpdate,
            UserUpdateRequest,
        )
        from server.time_utils import current_utc_time
        from server.user_repository import UserRepository

        initialize_database()
        user_repository = UserRepository()

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
                username="admin",
                display_name="Admin",
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

        try:
            update_user_status(
                owner.user_id,
                UserStatusUpdate(is_active=False),
                stored_owner,
            )
        except HTTPException as error:
            assert error.status_code == 400
        else:
            raise AssertionError("Last active owner should not be disabled")

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
            PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00")),
            stored_owner,
        )
        assert billing.payment_status == "paid"
        with get_connection() as connection:
            payment_row = connection.execute(
                "SELECT recorded_by_user_id FROM payments WHERE session_id = ?",
                ("auth-session",),
            ).fetchone()
        assert payment_row["recorded_by_user_id"] == owner.user_id

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

        updated_cashier = update_user_status(
            cashier.user_id,
            UserStatusUpdate(is_active=False),
            stored_owner,
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

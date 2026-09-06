"""The Sales report exposes, per operator, when they last signed out within
the report window (SalesOperatorBreakdown.signed_out_at) -- or None if they
have not signed out. An explicit Sign Out (/auth/logout?end_operator_session
=true) records it; a plain Change Operator hand-off does not.
"""

import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from fastapi import Response

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def pay_as(main_module, session_id: str, pc_name: str, amount: Decimal, actor):
    from server.schemas import PaymentCreateRequest, SessionRecord
    from server.time_utils import current_utc_time

    now = current_utc_time()
    main_module.session_repository.create_session(
        SessionRecord(
            session_id=session_id,
            pc_name=pc_name,
            status="active",
            started_at=now,
            scheduled_end_at=now,
            original_seconds=3600,
            total_seconds=3600,
            created_at=now,
            base_amount=amount,
            total_amount=amount,
            session_mode="prepaid",
        )
    )
    main_module.record_payment(
        session_id,
        PaymentCreateRequest(payment_method="cash", amount=amount, cash_received=amount),
        actor,
    )


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "signout.sqlite3")

        from server.database import initialize_database

        initialize_database()

        import server.main as main_module
        from server.auth import auth_session_repository
        from server.schemas import UserCreateRequest
        from server.user_repository import UserRepository

        users = UserRepository()
        gone = users.get_user(
            users.create_user(
                UserCreateRequest(username="op.gone", display_name="Gone Home", password="op-gone-pass", role="cashier")
            ).user_id
        )
        onduty = users.get_user(
            users.create_user(
                UserCreateRequest(username="op.here", display_name="Still Here", password="op-here-pass", role="cashier")
            ).user_id
        )

        pay_as(main_module, "s-gone", "PC-01", Decimal("40.00"), gone)
        pay_as(main_module, "s-here", "PC-02", Decimal("25.00"), onduty)

        report = main_module.build_sales_report("today", None, None)
        by_id = {op.operator_id: op for op in report.operators}
        assert by_id[gone.user_id].signed_out_at is None, "no sign-out recorded yet"
        assert by_id[onduty.user_id].signed_out_at is None

        # op.gone signs out explicitly.
        token, _ = auth_session_repository.create_session(gone.user_id, 3600)
        main_module.logout(Response(), sg_session=token, end_operator_session=True)

        report = main_module.build_sales_report("today", None, None)
        by_id = {op.operator_id: op for op in report.operators}
        assert by_id[gone.user_id].signed_out_at is not None, "explicit Sign Out must be reflected"
        assert by_id[onduty.user_id].signed_out_at is None, "operator still on duty stays None"
        print(f"  op.gone signed_out_at = {by_id[gone.user_id].signed_out_at.isoformat()}")

        # A Change Operator hand-off (end_operator_session omitted) must NOT
        # record a sign-out.
        token2, _ = auth_session_repository.create_session(onduty.user_id, 3600)
        main_module.logout(Response(), sg_session=token2, end_operator_session=False)
        report = main_module.build_sales_report("today", None, None)
        by_id = {op.operator_id: op for op in report.operators}
        assert by_id[onduty.user_id].signed_out_at is None, "Change Operator is not a sign-out"

        print("Sales operator sign-out smoke test passed.")


if __name__ == "__main__":
    main()

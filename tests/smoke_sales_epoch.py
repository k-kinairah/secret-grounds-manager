"""Soft "reset sales figures" epoch.

Setting the sales epoch (POST /finances/sales-epoch) makes every sales /
operator-collections / net-income figure count only payments at or after
that moment -- used to zero the books after importing carried-over member
balances from another system. It deletes nothing and DELETE undoes it.
"""

import os
import sys
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def create_env(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()
    import server.main as main_module

    from server.schemas import UserCreateRequest

    owner_pub = main_module.user_repository.create_user(
        UserCreateRequest(username="se-owner", display_name="SE Owner", password="owner-pass", role="owner")
    )
    cashier_pub = main_module.user_repository.create_user(
        UserCreateRequest(username="se-cashier", display_name="SE Cashier", password="cashier-pass", role="cashier")
    )
    owner = main_module.user_repository.get_user(owner_pub.user_id)
    cashier = main_module.user_repository.get_user(cashier_pub.user_id)
    return main_module, owner, cashier


def pay(main_module, session_id: str, pc_name: str, amount: Decimal, actor):
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


def backdate_session_payments(session_id: str, when) -> None:
    from server.database import get_connection

    with get_connection() as connection:
        connection.execute(
            "UPDATE payments SET created_at = ? WHERE session_id = ?",
            (when.isoformat(), session_id),
        )


def test_epoch_excludes_pre_epoch_payments_and_delete_restores_them() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        main_module, owner, _ = create_env(Path(temp) / "epoch.sqlite3")
        from server.finances_repository import clear_sales_epoch, set_sales_epoch
        from server.time_utils import current_utc_time

        now = current_utc_time()
        pay(main_module, "s-old", "PC-01", Decimal("10.00"), owner)  # migration-era top-up
        pay(main_module, "s-new", "PC-02", Decimal("20.00"), owner)  # real trade after reset
        backdate_session_payments("s-old", now - timedelta(days=5))

        wide_start = (now - timedelta(days=10)).date()
        wide_end = date.today() + timedelta(days=1)

        def custom_total() -> Decimal:
            return main_module.build_sales_report("custom", wide_start, wide_end).summary.gross_sales

        assert custom_total() == Decimal("30.00")

        # Reset the books to a moment between the two payments.
        set_sales_epoch(now - timedelta(days=2), owner.user_id)
        assert main_module.get_sales_epoch_endpoint(owner).sales_epoch_at is not None
        assert custom_total() == Decimal("20.00"), "pre-epoch payment must drop out of the sales report"

        # Net income for this month follows the same clamp.
        month = now.strftime("%Y-%m")
        assert main_module.get_finances_summary(month, owner).income == Decimal("20.00")

        # Nothing was deleted.
        from server.database import get_connection

        with get_connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 2

        # Undo.
        clear_sales_epoch(owner.user_id)
        assert main_module.get_sales_epoch_endpoint(owner).sales_epoch_at is None
        assert custom_total() == Decimal("30.00"), "clearing the epoch restores full history"


def test_set_endpoint_stamps_now_and_delete_clears() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        main_module, owner, _ = create_env(Path(temp) / "epoch-endpoint.sqlite3")
        from server.time_utils import current_utc_time

        before = current_utc_time()
        response = main_module.set_sales_epoch_endpoint(owner)
        assert response.sales_epoch_at is not None
        assert response.sales_epoch_at >= before - timedelta(seconds=5)
        assert main_module.clear_sales_epoch_endpoint(owner).sales_epoch_at is None


def test_sales_epoch_endpoints_are_admin_and_owner_only() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        main_module, owner, cashier = create_env(Path(temp) / "epoch-authz.sqlite3")
        from fastapi import HTTPException

        from server.auth import require_role

        # All three /finances/sales-epoch endpoints gate on require_role("admin").
        guard = require_role("admin")
        assert guard(owner).user_id == owner.user_id
        try:
            guard(cashier)
        except HTTPException as error:
            assert error.status_code == 403
        else:
            raise AssertionError("a cashier must not pass require_role('admin')")

        # admin passes too (require_role("admin") = admin OR owner).
        from server.schemas import UserCreateRequest

        admin_pub = main_module.user_repository.create_user(
            UserCreateRequest(username="se-admin", display_name="SE Admin", password="admin-pass", role="admin")
        )
        admin = main_module.user_repository.get_user(admin_pub.user_id)
        assert guard(admin).user_id == admin.user_id


def main() -> None:
    for test in (
        test_epoch_excludes_pre_epoch_payments_and_delete_restores_them,
        test_set_endpoint_stamps_now_and_delete_clears,
        test_sales_epoch_endpoints_are_admin_and_owner_only,
    ):
        test()
        print(f"{test.__name__} passed.")
    print("sales epoch smoke tests passed.")


if __name__ == "__main__":
    main()

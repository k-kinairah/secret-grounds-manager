"""Net Income / bills: recurring fixed + variable bills, per-month amounts,
one-off expenses, monthly resolution, and the admin/owner gate.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def this_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def other_month(delta_months: int) -> str:
    now = datetime.now(timezone.utc)
    year = now.year + (now.month - 1 + delta_months) // 12
    month = (now.month - 1 + delta_months) % 12 + 1
    return f"{year:04d}-{month:02d}"


def pay(main_module, session_id, amount, actor):
    from server.schemas import PaymentCreateRequest, SessionRecord
    from server.time_utils import current_utc_time

    now = current_utc_time()
    main_module.session_repository.create_session(
        SessionRecord(
            session_id=session_id, pc_name="PC-01", status="active",
            started_at=now, scheduled_end_at=now, original_seconds=3600,
            total_seconds=3600, created_at=now, base_amount=Decimal(amount),
            total_amount=Decimal(amount), session_mode="prepaid",
        )
    )
    main_module.record_payment(
        session_id,
        PaymentCreateRequest(payment_method="cash", amount=Decimal(amount), cash_received=Decimal(amount)),
        actor,
    )


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp) / "finances.sqlite3")

        from server.database import initialize_database

        initialize_database()

        import server.main as m
        from server.auth import require_role
        from server.schemas import (
            OneOffExpenseRequest,
            SetBillAmountRequest,
            ShopBillCreateRequest,
            UserCreateRequest,
        )
        from server.user_repository import UserRepository

        users = UserRepository()
        admin = users.get_by_username("admin")
        owner = users.get_user(
            users.create_user(UserCreateRequest(username="fin.owner", display_name="Owner", password="owner-pass-1", role="owner")).user_id
        )
        cashier = users.get_user(
            users.create_user(UserCreateRequest(username="fin.cashier", display_name="Cash", password="cash-pass-1", role="cashier")).user_id
        )

        repo = m.finances_repository
        month = this_month()

        # --- create bills ---
        rent = repo.create_bill(ShopBillCreateRequest(name="Rent", kind="fixed", fixed_amount=Decimal("12000")), owner.user_id)
        meralco = repo.create_bill(ShopBillCreateRequest(name="Meralco", kind="variable"), owner.user_id)
        assert rent.kind == "fixed" and rent.fixed_amount == Decimal("12000.00")
        assert meralco.kind == "variable" and meralco.fixed_amount is None

        try:
            ShopBillCreateRequest(name="Bad", kind="fixed", fixed_amount=None)
        except Exception:
            pass
        else:
            raise AssertionError("fixed bill without an amount must be rejected")

        # --- resolve: fixed applied, variable not entered ---
        s = repo.resolve_month(month, Decimal("50000.00"))
        assert s.total_bills == Decimal("12000.00")
        assert s.net_income == Decimal("38000.00")
        rent_line = next(l for l in s.lines if l.label == "Rent")
        meralco_line = next(l for l in s.lines if l.label == "Meralco")
        assert rent_line.applied and rent_line.amount == Decimal("12000.00")
        assert not meralco_line.applied and meralco_line.amount is None

        # --- enter Meralco for the month ---
        repo.set_bill_amount(meralco.bill_id, month, Decimal("6540.00"), owner.user_id)
        s = repo.resolve_month(month, Decimal("50000.00"))
        assert s.total_bills == Decimal("18540.00")
        meralco_line = next(l for l in s.lines if l.label == "Meralco")
        assert meralco_line.applied and meralco_line.amount == Decimal("6540.00")

        # re-setting overwrites, not duplicates
        repo.set_bill_amount(meralco.bill_id, month, Decimal("7000.00"), owner.user_id)
        s = repo.resolve_month(month, Decimal("50000.00"))
        assert s.total_bills == Decimal("19000.00")

        # --- one-off expense ---
        repo.add_one_off(OneOffExpenseRequest(month=month, label="Aircon repair", amount=Decimal("2500")), owner.user_id)
        s = repo.resolve_month(month, Decimal("50000.00"))
        assert s.total_bills == Decimal("21500.00")
        one_off = next(l for l in s.lines if l.source == "one_off")
        assert one_off.label == "Aircon repair" and one_off.amount == Decimal("2500.00")

        repo.void_one_off(one_off.amount_id)
        s = repo.resolve_month(month, Decimal("50000.00"))
        assert s.total_bills == Decimal("19000.00")
        assert all(l.source != "one_off" for l in s.lines)

        # --- clear a variable amount ---
        repo.clear_bill_amount(meralco.bill_id, month)
        s = repo.resolve_month(month, Decimal("50000.00"))
        assert s.total_bills == Decimal("12000.00")

        # --- a bill does not apply to a month before it was created ---
        past = repo.resolve_month(other_month(-2), Decimal("0"))
        assert all(l.label != "Rent" for l in past.lines), "Rent must not apply to a past month"

        # --- archive: still counts for the month it was archived in, gone after ---
        repo.archive_bill(rent.bill_id)
        this_month_after = repo.resolve_month(month, Decimal("0"))
        assert any(l.label == "Rent" for l in this_month_after.lines), "archived-this-month bill still counts this month"
        future = repo.resolve_month(other_month(2), Decimal("0"))
        assert all(l.label != "Rent" for l in future.lines)
        assert any(b.name == "Rent" and b.archived_at is not None for b in repo.list_bills())

        # --- endpoint: month range + income + gate ---
        pay(m, "s-fin-1", "800.00", admin)
        summary = m.get_finances_summary(month=month, _=admin)
        assert summary.income == Decimal("800.00")
        # Rent (12000) still applies this month; Meralco was cleared.
        assert summary.total_bills == Decimal("12000.00")
        assert summary.net_income == Decimal("-11200.00")

        try:
            m.get_finances_summary(month="2026-13", _=admin)
        except HTTPException as error:
            assert error.status_code == 400
        else:
            raise AssertionError("invalid month must 400")

        gate = require_role("admin")
        assert gate(admin) is admin
        assert gate(owner) is owner
        try:
            gate(cashier)
        except HTTPException as error:
            assert error.status_code == 403
        else:
            raise AssertionError("cashier must not reach finances endpoints")

        print("Finances (Net Income / bills) smoke test passed.")


if __name__ == "__main__":
    main()

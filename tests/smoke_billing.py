import asyncio
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "billing.sqlite3")

        from server.database import initialize_database
        from server.main import (
            add_time_to_session,
            create_rate,
            get_session_billing,
            pc_registry,
            record_payment,
            register_pc,
            session_service,
            start_session,
            void_payment,
        )
        from server.schemas import (
            AddTimeRequest,
            PCRegistration,
            PaymentCreateRequest,
            RateProfileCreate,
            StartSessionRequest,
            VoidPaymentRequest,
        )

        initialize_database()
        pc_registry.clear()
        register_pc(PCRegistration(pc_name="PC-BILL", ip_address="127.0.0.1"))
        session_service.require_available_client = lambda _: pc_registry["PC-BILL"]

        async def acknowledge_command(*_: object) -> list[object]:
            from server.schemas import CommandAcknowledgement

            return [
                CommandAcknowledgement(command_id="test", status="received"),
                CommandAcknowledgement(command_id="test", status="completed"),
            ]

        session_service.send_required_command = acknowledge_command

        # Enter Amount (money-to-time via the metered rate) is retired;
        # billing behavior is now exercised through fixed package rates
        # carrying the same dollar amounts the test already relies on.
        starting_rate = create_rate(
            RateProfileCreate(
                name="Billing Test Package",
                duration_seconds=6000,
                price=Decimal("20.00"),
                rate_type="package",
                sort_order=1,
            )
        )
        addon_rate = create_rate(
            RateProfileCreate(
                name="Billing Test Add-On",
                duration_seconds=1500,
                price=Decimal("5.00"),
                rate_type="package",
                sort_order=2,
            )
        )

        started = asyncio.run(
            start_session(
                StartSessionRequest(pc_name="PC-BILL", rate_id=starting_rate.rate_id)
            )
        )
        session_id = started.session.session_id
        billing = get_session_billing(session_id)
        assert billing.total_amount == Decimal("20.00")
        assert billing.total_paid == Decimal("0.00")
        assert billing.balance_due == Decimal("20.00")
        assert billing.payment_status == "unpaid"

        partial = record_payment(
            session_id,
            PaymentCreateRequest(
                payment_method="cash",
                amount=Decimal("10.00"),
                cash_received=Decimal("10.00"),
            ),
        )
        assert partial.total_paid == Decimal("10.00")
        assert partial.balance_due == Decimal("10.00")
        assert partial.payment_status == "partially_paid"

        # Cash received is optional -- a cashier can record a cash payment
        # without logging the exact bill handed over. change_given simply
        # stays unset when it isn't given.
        no_cash_received = record_payment(
            session_id,
            PaymentCreateRequest(payment_method="cash", amount=Decimal("1.00")),
        )
        assert no_cash_received.payments[-1].cash_received is None
        assert no_cash_received.payments[-1].change_given is None
        assert no_cash_received.total_paid == Decimal("11.00")
        assert no_cash_received.balance_due == Decimal("9.00")

        try:
            record_payment(
                session_id,
                PaymentCreateRequest(
                    payment_method="cash",
                    amount=Decimal("5.00"),
                    cash_received=Decimal("4.00"),
                ),
            )
        except HTTPException as error:
            assert error.status_code == 400
        else:
            raise AssertionError("Insufficient cash should be rejected")

        # Reference number is optional too, same rationale as cash received.
        no_reference = record_payment(
            session_id,
            PaymentCreateRequest(payment_method="gcash", amount=Decimal("1.00")),
        )
        assert no_reference.payments[-1].reference_number is None
        assert no_reference.total_paid == Decimal("12.00")
        assert no_reference.balance_due == Decimal("8.00")

        paid = record_payment(
            session_id,
            PaymentCreateRequest(
                payment_method="gcash",
                amount=Decimal("8.00"),
                reference_number="GC-123",
            ),
        )
        assert paid.total_paid == Decimal("20.00")
        assert paid.balance_due == Decimal("0.00")
        assert paid.payment_status == "paid"

        try:
            record_payment(
                session_id,
                PaymentCreateRequest(
                    payment_method="gcash",
                    amount=Decimal("1.00"),
                    reference_number="GC-OVER",
                ),
            )
        except HTTPException as error:
            assert error.status_code == 400
        else:
            raise AssertionError("Payment amount above balance should be rejected")

        updated = asyncio.run(
            add_time_to_session(session_id, AddTimeRequest(rate_id=addon_rate.rate_id))
        )
        assert updated.session.additional_amount == Decimal("5.00")
        billing_after_add = get_session_billing(session_id)
        assert billing_after_add.total_amount == Decimal("25.00")
        assert billing_after_add.total_paid == Decimal("20.00")
        assert billing_after_add.balance_due == Decimal("5.00")

        final = record_payment(
            session_id,
            PaymentCreateRequest(
                payment_method="cash",
                amount=Decimal("5.00"),
                cash_received=Decimal("50.00"),
            ),
        )
        cash_payment = final.payments[-1]
        assert cash_payment.change_given == Decimal("45.00")
        assert final.total_paid == Decimal("25.00")
        assert final.payment_status == "paid"

        restored = void_payment(
            cash_payment.payment_id,
            VoidPaymentRequest(reason="Cash count correction"),
        )
        assert restored.total_paid == Decimal("20.00")
        assert restored.balance_due == Decimal("5.00")

        try:
            void_payment(
                cash_payment.payment_id,
                VoidPaymentRequest(reason="Duplicate void"),
            )
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Duplicate void should be rejected")

        exact_cash = record_payment(
            session_id,
            PaymentCreateRequest(
                payment_method="cash",
                amount=Decimal("5.00"),
                cash_received=Decimal("5.00"),
            ),
        )
        assert exact_cash.payments[-1].change_given == Decimal("0.00")
        assert exact_cash.payment_status == "paid"

        print("Billing smoke test passed.")


if __name__ == "__main__":
    main()

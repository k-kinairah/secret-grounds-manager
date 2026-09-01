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
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "sales.sqlite3")

        from server.database import initialize_database
        from server.main import (
            build_sales_report,
            create_member,
            create_rate,
            export_sales_report,
            get_sales_report,
            get_dashboard_today_sales,
            list_membership_products,
            open_shift,
            pc_registry,
            purchase_member_time,
            record_payment,
            register_pc,
            require_admin_operator,
            session_repository,
            session_service,
            start_session,
            void_payment,
        )
        from server.schemas import (
            CommandAcknowledgement,
            MemberCreateRequest,
            MemberPurchaseRequest,
            PCRegistration,
            PaymentCreateRequest,
            RateProfileCreate,
            ShiftOpenRequest,
            StartSessionRequest,
            UserCreateRequest,
            VoidPaymentRequest,
        )
        from server.user_repository import UserRepository

        initialize_database()
        user_repository = UserRepository()
        admin = user_repository.get_by_username("admin")
        assert admin is not None
        cashier_public = user_repository.create_user(
            UserCreateRequest(
                username="sales_cashier",
                display_name="Sales Cashier",
                password="sales-pass",
                role="cashier",
            )
        )
        cashier = user_repository.get_user(cashier_public.user_id)
        assert cashier is not None
        open_shift(ShiftOpenRequest(opening_cash=Decimal("1000.00")), cashier)

        assert require_admin_operator(admin) is admin
        try:
            require_admin_operator(cashier)
        except HTTPException as error:
            assert error.status_code == 403
        else:
            raise AssertionError("Normal operator should not access Sales reporting")

        pc_registry.clear()
        register_pc(PCRegistration(pc_name="PC-SALES", ip_address="127.0.0.1"))
        register_pc(PCRegistration(pc_name="PC-DUE", ip_address="127.0.0.2"))
        session_service.require_available_client = lambda pc_name: pc_registry[pc_name]

        async def acknowledge_command(*_: object) -> list[object]:
            return [
                CommandAcknowledgement(command_id="test", status="received"),
                CommandAcknowledgement(command_id="test", status="completed"),
            ]

        session_service.send_required_command = acknowledge_command

        # Enter Amount is retired; sales-report exercising now uses fixed
        # package rates carrying the same dollar amounts as before.
        sales_rate = create_rate(
            RateProfileCreate(
                name="Sales Report Test Package",
                duration_seconds=6000,
                price=Decimal("100.00"),
                rate_type="package",
                sort_order=1,
            )
        )
        due_rate = create_rate(
            RateProfileCreate(
                name="Sales Report Due Package",
                duration_seconds=4800,
                price=Decimal("80.00"),
                rate_type="package",
                sort_order=2,
            )
        )

        started = asyncio.run(
            start_session(StartSessionRequest(pc_name="PC-SALES", rate_id=sales_rate.rate_id))
        )
        session_id = started.session.session_id
        cash_payment = record_payment(
            session_id,
            PaymentCreateRequest(
                payment_method="cash",
                amount=Decimal("50.00"),
                cash_received=Decimal("100.00"),
            ),
            cashier,
        ).payments[-1]
        record_payment(
            session_id,
            PaymentCreateRequest(
                payment_method="gcash",
                amount=Decimal("25.00"),
                reference_number="GC-SALES",
            ),
            cashier,
        )
        void_candidate = record_payment(
            session_id,
            PaymentCreateRequest(
                payment_method="maya",
                amount=Decimal("10.00"),
                reference_number="MY-VOID",
            ),
            cashier,
        ).payments[-1]
        void_payment(
            void_candidate.payment_id,
            VoidPaymentRequest(reason="Duplicate retry test"),
            cashier,
        )

        unsettled = asyncio.run(
            start_session(StartSessionRequest(pc_name="PC-DUE", rate_id=due_rate.rate_id), cashier)
        )
        session_repository.update_session(
            unsettled.session.model_copy(
                update={
                    "status": "settlement_pending",
                    "settlement_pending": True,
                    "balance_due": Decimal("80.00"),
                    "payment_status": "unpaid",
                }
            )
        )

        # Zero-balance account (admin comp) -- keeps the sales totals below
        # driven only by the explicit top-up purchases in this test.
        member = create_member(
            MemberCreateRequest(
                username="sales.member",
                display_name="Sales Member",
                pin="1234",
                confirm_pin="1234",
            ),
            admin,
        )
        products = list_membership_products()
        product = next(item for item in products if item.product_id == "member-promo")
        purchase_member_time(
            member.member_id,
            MemberPurchaseRequest(
                product_id=product.product_id,
                payment_method="maya",
                reference_number="MAYA-123",
            ),
            cashier,
        )

        report = get_sales_report("today", None, None, admin)
        expected_total = Decimal("75.00") + product.price
        assert report.summary.total_sales == expected_total
        assert report.summary.net_sales == expected_total
        assert report.summary.refund_total == void_candidate.amount
        assert report.summary.gross_sales == expected_total + void_candidate.amount
        assert report.summary.cash_received_total == Decimal("100.00")
        assert report.summary.change_given_total == Decimal("50.00")
        assert report.summary.non_cash_collected == Decimal("25.00") + product.price
        assert report.summary.cash_total == Decimal("50.00")
        assert report.summary.gcash_total == Decimal("25.00")
        assert report.summary.maya_total == product.price
        assert report.summary.payment_count == 3
        assert report.summary.outstanding_amount == Decimal("80.00")
        assert report.summary.session_revenue == Decimal("75.00")
        assert report.summary.member_purchase_revenue == product.price
        assert all(payment.payment_id != void_candidate.payment_id for payment in report.recent_payments)
        assert any(payment.payment_id == cash_payment.payment_id for payment in report.recent_payments)
        assert report.operators[0].operator_name == "Sales Cashier"
        assert report.operators[0].total_amount == expected_total
        collection = next(
            operator for operator in report.operator_collections
            if operator.operator_name == "Sales Cashier"
        )
        assert collection.transaction_count == 4
        assert collection.gross_sales == expected_total + void_candidate.amount
        assert collection.refunds_voids == void_candidate.amount
        assert collection.net_sales == expected_total
        assert collection.cash_received == Decimal("100.00")
        assert collection.change_given == Decimal("50.00")
        assert collection.expected_cash == Decimal("50.00")
        assert collection.non_cash_collected == Decimal("25.00") + product.price
        assert collection.outstanding == Decimal("80.00")
        void_transaction = next(
            transaction for transaction in collection.transactions
            if transaction.payment_id == void_candidate.payment_id
        )
        assert void_transaction.recorded_by_operator == "Sales Cashier"
        assert void_transaction.voided_by_operator == "Sales Cashier"
        assert report.pcs[0].pc_name == "PC-SALES"
        assert report.pcs[0].total_amount == Decimal("75.00")
        assert all(pc.total_amount != expected_total for pc in report.pcs)
        assert report.grouping == "hour"
        assert sum(point.payment_count for point in report.timeseries) == 3
        assert report.outstanding_settlements[0].amount_due == Decimal("80.00")
        assert get_dashboard_today_sales(cashier) == expected_total

        all_report = build_sales_report("custom", report.start_at.date(), report.end_at.date())
        assert all_report.summary.total_sales == expected_total
        csv_response = export_sales_report("today", None, None, admin)
        body = csv_response.body.decode()
        assert "payment timestamp,operator,type,PC,customer/member,payment method,amount,status,related reference" in body
        assert "Sales Cashier" in body
        assert "member_purchase" in body

        print("Sales smoke test passed.")


if __name__ == "__main__":
    main()

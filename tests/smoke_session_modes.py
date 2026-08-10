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
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "modes.sqlite3")

        from server.database import initialize_database
        from server.main import (
            add_prepaid_time_to_session,
            create_rate,
            end_session,
            get_session_billing,
            pc_registry,
            record_payment,
            register_pc,
            session_service,
            start_prepaid_session,
        )
        from server.schemas import (
            CommandAcknowledgement,
            PCRegistration,
            PaymentCreateRequest,
            PrepaidAddTimeRequest,
            PrepaidStartSessionRequest,
            RateProfileCreate,
            RateProfileUpdate,
            SessionRecord,
        )
        from server.billing_service import calculate_open_time_estimate
        from server.rate_repository import RateRepository
        from server.time_utils import current_utc_time

        initialize_database()

        async def acknowledge_command(*_: object) -> list[object]:
            return [
                CommandAcknowledgement(command_id="test", status="received"),
                CommandAcknowledgement(command_id="test", status="completed"),
            ]

        pc_registry.clear()
        register_pc(PCRegistration(pc_name="PC-PREPAID", ip_address="127.0.0.1"))
        session_service.require_available_client = lambda _: pc_registry["PC-PREPAID"]
        session_service.send_required_command = acknowledge_command

        # Enter Amount (money-to-time via the metered rate) is retired for
        # both start and add-time; prepaid billing is now exercised through
        # fixed package rates carrying the same dollar amounts as before.
        prepaid_start_rate = create_rate(
            RateProfileCreate(
                name="Prepaid Mode Test Package",
                duration_seconds=6000,
                price=Decimal("20.00"),
                rate_type="package",
                sort_order=1,
            )
        )
        prepaid_addon_rate = create_rate(
            RateProfileCreate(
                name="Prepaid Mode Test Add-On",
                duration_seconds=1500,
                price=Decimal("5.00"),
                rate_type="package",
                sort_order=2,
            )
        )
        fail_rate = create_rate(
            RateProfileCreate(
                name="Command Failure Test Package",
                duration_seconds=1500,
                price=Decimal("5.00"),
                rate_type="package",
                sort_order=3,
            )
        )

        try:
            asyncio.run(
                start_prepaid_session(
                    PrepaidStartSessionRequest(
                        pc_name="PC-PREPAID",
                        rate_id=prepaid_start_rate.rate_id,
                        payment=PaymentCreateRequest(
                            payment_method="cash",
                            amount=Decimal("20.00"),
                            cash_received=Decimal("10.00"),
                        ),
                    )
                )
            )
        except HTTPException as error:
            assert error.status_code == 400
        else:
            raise AssertionError("Failed payment must not start a prepaid session")

        prepaid = asyncio.run(
            start_prepaid_session(
                PrepaidStartSessionRequest(
                    pc_name="PC-PREPAID",
                    rate_id=prepaid_start_rate.rate_id,
                    payment=PaymentCreateRequest(
                        payment_method="cash",
                        amount=Decimal("20.00"),
                        cash_received=Decimal("50.00"),
                    ),
                )
            )
        )
        assert prepaid.session.session_mode == "prepaid"
        prepaid_billing = get_session_billing(prepaid.session.session_id)
        assert prepaid_billing.total_paid == Decimal("20.00")
        assert prepaid_billing.payments[-1].change_given == Decimal("30.00")

        added = asyncio.run(
            add_prepaid_time_to_session(
                prepaid.session.session_id,
                PrepaidAddTimeRequest(
                    rate_id=prepaid_addon_rate.rate_id,
                    payment=PaymentCreateRequest(
                        payment_method="cash",
                        amount=Decimal("5.00"),
                        cash_received=Decimal("5.00"),
                    ),
                ),
            )
        )
        assert added.session.additional_amount == Decimal("5.00")
        assert get_session_billing(prepaid.session.session_id).payment_status == "paid"

        # --- historical Open Time session lifecycle -----------------------
        # Open Time can no longer be started via the API. This constructs a
        # session in exactly the shape a real one already active before the
        # retirement would have -- proving finalize/settle/billing still
        # work correctly for it.
        pc_registry.clear()
        register_pc(PCRegistration(pc_name="PC-OPEN", ip_address="127.0.0.1"))
        session_service.require_available_client = lambda _: pc_registry["PC-OPEN"]
        session_service.send_required_command = acknowledge_command

        metered_rate = RateRepository().get_active_metered_rate()
        assert metered_rate is not None
        now = current_utc_time()
        historical_open_session = SessionRecord(
            session_id="historical-open-time-modes-test",
            pc_name="PC-OPEN",
            status="active",
            started_at=now,
            scheduled_end_at=now,
            original_seconds=0,
            added_seconds=0,
            total_seconds=0,
            amount_paid=None,
            ended_reason=None,
            created_at=now,
            base_amount=Decimal("0.00"),
            additional_amount=Decimal("0.00"),
            total_amount=Decimal("0.00"),
            session_mode="open_time",
            settlement_pending=True,
            rate_id=metered_rate.rate_id,
            rate_name_snapshot=f"{metered_rate.name}: {metered_rate.price} per {metered_rate.duration_seconds}s",
            metered_rate_price_snapshot=metered_rate.price,
            metered_rate_duration_seconds_snapshot=metered_rate.duration_seconds,
        )
        session_service.repository.create_session(historical_open_session)
        open_started = session_service.repository.get_session(historical_open_session.session_id)
        assert open_started is not None
        assert open_started.session_mode == "open_time"
        assert open_started.original_seconds == 0
        assert open_started.metered_rate_price_snapshot == Decimal("1.00")
        assert open_started.metered_rate_duration_seconds_snapshot == 300

        started_at = current_utc_time()
        assert calculate_open_time_estimate(
            started_at,
            started_at,
            Decimal("1.00"),
            300,
        ) == (0, 0, Decimal("0.00"))
        assert calculate_open_time_estimate(
            started_at,
            started_at + __import__("datetime").timedelta(seconds=1),
            Decimal("1.00"),
            300,
        ) == (1, 300, Decimal("1.00"))
        assert calculate_open_time_estimate(
            started_at,
            started_at + __import__("datetime").timedelta(seconds=300),
            Decimal("1.00"),
            300,
        ) == (300, 300, Decimal("1.00"))
        assert calculate_open_time_estimate(
            started_at,
            started_at + __import__("datetime").timedelta(seconds=301),
            Decimal("1.00"),
            300,
        ) == (301, 600, Decimal("2.00"))
        assert calculate_open_time_estimate(
            started_at,
            started_at + __import__("datetime").timedelta(hours=2),
            Decimal("1.00"),
            300,
        ) == (7200, 7200, Decimal("24.00"))
        assert calculate_open_time_estimate(
            started_at,
            started_at + __import__("datetime").timedelta(seconds=301),
            Decimal("1.25"),
            300,
        ) == (301, 600, Decimal("2.50"))

        session = session_service.repository.get_session(open_started.session_id)
        assert session is not None
        adjusted = session.model_copy(
            update={"started_at": session.started_at - __import__("datetime").timedelta(minutes=6)}
        )
        session_service.repository.update_session(adjusted)
        ended = asyncio.run(end_session(open_started.session_id))
        assert ended.session.status == "settlement_pending"
        assert ended.session.actual_elapsed_seconds is not None
        assert ended.session.billed_seconds == 600
        assert ended.session.final_computed_amount == Decimal("2.00")
        assert ended.session.metered_rate_price_snapshot == Decimal("1.00")
        assert ended.session.settlement_pending is True
        assert session_service.repository.get_active_session_for_pc("PC-OPEN") is not None

        unpaid = get_session_billing(open_started.session_id)
        assert unpaid.payment_status == "unpaid"
        settled = record_payment(
            open_started.session_id,
            PaymentCreateRequest(
                payment_method="gcash",
                amount=Decimal("2.00"),
                reference_number="GC-OPEN",
            ),
        )
        assert settled.payment_status == "paid"
        assert settled.session.status == "completed"
        assert settled.session.settlement_pending is False
        assert session_service.repository.get_active_session_for_pc("PC-OPEN") is None

        pc_registry.clear()
        register_pc(PCRegistration(pc_name="PC-FAIL", ip_address="127.0.0.1"))
        session_service.require_available_client = lambda _: pc_registry["PC-FAIL"]

        async def fail_command(*_: object) -> list[object]:
            raise RuntimeError("simulated command failure")

        session_service.send_required_command = fail_command
        try:
            asyncio.run(
                start_prepaid_session(
                    PrepaidStartSessionRequest(
                        pc_name="PC-FAIL",
                        rate_id=fail_rate.rate_id,
                        payment=PaymentCreateRequest(
                            payment_method="cash",
                            amount=Decimal("5.00"),
                            cash_received=Decimal("5.00"),
                        ),
                    )
                )
            )
        except HTTPException as error:
            assert error.status_code == 502
        else:
            raise AssertionError("Command failure after payment should be surfaced")

        review_sessions = session_service.repository.list_history()
        failed_session = next(session for session in review_sessions if session.pc_name == "PC-FAIL")
        review_billing = get_session_billing(failed_session.session_id)
        assert review_billing.payments[-1].review_required is True

        print("Session mode smoke test passed.")


if __name__ == "__main__":
    main()

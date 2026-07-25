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
            end_session,
            get_session_billing,
            pc_registry,
            record_payment,
            register_pc,
            session_service,
            start_open_time_session,
            start_prepaid_session,
        )
        from server.schemas import (
            CommandAcknowledgement,
            OpenTimeStartRequest,
            PCRegistration,
            PaymentCreateRequest,
            PrepaidAddTimeRequest,
            PrepaidStartSessionRequest,
            RateProfileUpdate,
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

        try:
            asyncio.run(
                start_prepaid_session(
                    PrepaidStartSessionRequest(
                        pc_name="PC-PREPAID",
                        amount=Decimal("20.00"),
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
                    amount=Decimal("20.00"),
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
                    amount=Decimal("5.00"),
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

        pc_registry.clear()
        register_pc(PCRegistration(pc_name="PC-OPEN", ip_address="127.0.0.1"))
        session_service.require_available_client = lambda _: pc_registry["PC-OPEN"]
        open_started = asyncio.run(
            start_open_time_session(OpenTimeStartRequest(pc_name="PC-OPEN"))
        )
        assert open_started.session.session_mode == "open_time"
        assert open_started.session.original_seconds == 0
        assert open_started.session.metered_rate_price_snapshot == Decimal("1.00")
        assert open_started.session.metered_rate_duration_seconds_snapshot == 300

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

        session = session_service.repository.get_session(open_started.session.session_id)
        assert session is not None
        metered_rate = RateRepository().get_active_metered_rate()
        assert metered_rate is not None
        RateRepository().update_rate(
            metered_rate.rate_id,
            RateProfileUpdate(
                name="Edited Regular Rate",
                duration_seconds=60,
                price=Decimal("10.00"),
                rate_type="metered",
                sort_order=0,
            ),
        )
        adjusted = session.model_copy(
            update={"started_at": session.started_at - __import__("datetime").timedelta(minutes=6)}
        )
        session_service.repository.update_session(adjusted)
        ended = asyncio.run(end_session(open_started.session.session_id))
        assert ended.session.status == "settlement_pending"
        assert ended.session.actual_elapsed_seconds is not None
        assert ended.session.billed_seconds == 600
        assert ended.session.final_computed_amount == Decimal("2.00")
        assert ended.session.metered_rate_price_snapshot == Decimal("1.00")
        assert ended.session.settlement_pending is True
        assert session_service.repository.get_active_session_for_pc("PC-OPEN") is not None

        unpaid = get_session_billing(open_started.session.session_id)
        assert unpaid.payment_status == "unpaid"
        settled = record_payment(
            open_started.session.session_id,
            PaymentCreateRequest(payment_method="gcash", amount=Decimal("2.00")),
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
                        amount=Decimal("5.00"),
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

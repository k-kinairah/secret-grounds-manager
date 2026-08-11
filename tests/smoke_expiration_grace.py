import asyncio
import os
import sys
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class FakeConnectionManager:
    def __init__(self) -> None:
        self.commands: list[object] = []

    def is_connected(self, _: str) -> bool:
        return True

    async def send_command(self, _: str, command: object) -> list[object]:
        self.commands.append(command)
        from server.schemas import CommandAcknowledgement

        command_id = getattr(command, "command_id")
        return [
            CommandAcknowledgement(command_id=command_id, status="received", message="ok"),
            CommandAcknowledgement(command_id=command_id, status="completed", message="ok"),
        ]


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "grace.sqlite3")
        os.environ["SECRET_GROUNDS_TEST_MODE"] = "true"
        os.environ["SECRET_GROUNDS_AUTOMATIC_SHUTDOWN_ON_EXPIRATION"] = "false"
        os.environ["SECRET_GROUNDS_EXPIRATION_GRACE_SECONDS"] = "120"

        from server.billing_service import BillingService
        from server.database import initialize_database
        from server.payment_repository import PaymentRepository
        from server.rate_repository import RateRepository
        from server.schemas import PCRecord, SessionRecord
        from server.session_repository import SessionRepository
        from server.session_service import SessionService
        from server.time_utils import current_utc_time

        initialize_database()
        repository = SessionRepository()
        rate_repository = RateRepository()
        billing_service = BillingService(repository, PaymentRepository())
        connection_manager = FakeConnectionManager()
        pc_registry = {
            "PC-GRACE": PCRecord(
                pc_name="PC-GRACE",
                ip_address="127.0.0.1",
                status="online",
                last_seen=current_utc_time(),
            )
        }
        service = SessionService(
            repository=repository,
            rate_repository=rate_repository,
            connection_manager=connection_manager,  # type: ignore[arg-type]
            pc_registry=pc_registry,
            command_timeout_seconds=1,
            billing_service=billing_service,
        )

        now = current_utc_time()
        session = SessionRecord(
            session_id="grace-session",
            pc_name="PC-GRACE",
            status="active",
            started_at=now - timedelta(minutes=30),
            scheduled_end_at=now,
            original_seconds=1800,
            total_seconds=1800,
            created_at=now,
            base_amount=Decimal("20.00"),
            total_amount=Decimal("20.00"),
        )
        repository.create_session(session)

        grace = service.begin_expiration_grace(session.session_id, now)
        assert grace is not None
        assert grace.status == "grace_period"
        assert grace.grace_started_at == now
        assert grace.shutdown_at is not None
        assert int((grace.shutdown_at - now).total_seconds()) == 120

        one_hour = next(
            rate for rate in rate_repository.list_rates() if rate.name == "Standard Guest - P15 (1 Hour)"
        )
        rescue = asyncio.run(
            service.add_time(
                session.session_id,
                rate_id=one_hour.rate_id,
                actor_user_id="operator-1",
            )
        )
        assert rescue.session.status == "active"
        assert rescue.session.session_id == session.session_id
        assert rescue.session.grace_started_at is None
        assert rescue.session.shutdown_at is None
        assert rescue.session.scheduled_end_at > current_utc_time() + timedelta(minutes=59)
        assert connection_manager.commands[-1].payload["cancel_expiration"] is True  # type: ignore[attr-defined]

        second_grace = service.begin_expiration_grace(session.session_id, current_utc_time())
        assert second_grace is not None
        finalized = service.finalize_expiration_shutdown(session.session_id)
        assert finalized is True
        completed = repository.get_session(session.session_id)
        assert completed is not None
        assert completed.status == "completed"
        assert completed.ended_reason == "timer_finished"
        assert completed.settlement_pending is False

        print("Expiration grace smoke test passed.")


if __name__ == "__main__":
    main()

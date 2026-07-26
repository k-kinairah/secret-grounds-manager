import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from server.billing_service import BillingService
from server.database import initialize_database
from server.payment_repository import PaymentRepository
from server.prepared_session_repository import PreparedSessionRepository
from server.rate_repository import RateRepository
from server.schemas import (
    CommandAcknowledgement,
    PCRecord,
    PreparedSessionStartRequest,
)
from server.session_repository import SessionRepository
from server.session_service import SessionService


class DummyConnectionManager:
    def is_connected(self, _: str) -> bool:
        return True


def create_service(database_path: Path) -> tuple[SessionService, SessionRepository, PreparedSessionRepository]:
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    initialize_database()
    session_repository = SessionRepository()
    prepared_repository = PreparedSessionRepository()
    service = SessionService(
        repository=session_repository,
        rate_repository=RateRepository(),
        connection_manager=DummyConnectionManager(),  # type: ignore[arg-type]
        pc_registry={
            "PC-02": PCRecord(
                pc_name="PC-02",
                ip_address="127.0.0.1",
                status="online",
                last_seen=datetime.now(timezone.utc),
                mac_address="AA:BB:CC:DD:EE:FF",
            )
        },
        command_timeout_seconds=1,
        billing_service=BillingService(session_repository, PaymentRepository()),
        prepared_session_repository=prepared_repository,
    )

    async def complete_command(*_: object) -> list[CommandAcknowledgement]:
        return [
            CommandAcknowledgement(command_id="received", status="received", message="Command received"),
            CommandAcknowledgement(command_id="completed", status="completed", message="Session initialized"),
        ]

    service.send_required_command = complete_command  # type: ignore[method-assign]
    return service, session_repository, prepared_repository


def test_prepare_fixed_time_activates_on_reconnect(temp_directory: str) -> None:
    service, session_repository, prepared_repository = create_service(
        Path(temp_directory) / "prepared.sqlite3"
    )
    prepared_response = service.prepare_offline_session(
        PreparedSessionStartRequest(
            pc_name="PC-02",
            session_mode="prepaid",
            duration_seconds=3600,
        ),
        "cashier-user",
    )
    prepared = prepared_response.prepared_session
    assert prepared.status == "prepared"
    assert session_repository.get_active_session_for_pc("PC-02") is None

    service.mark_prepared_wake_sent(prepared.prepared_session_id)
    activated = asyncio.run(service.activate_prepared_session_for_pc("PC-02"))
    assert activated is not None
    assert activated.session is not None
    assert activated.prepared_session.status == "active"
    assert activated.session.started_at >= prepared.prepared_at
    assert int((activated.session.scheduled_end_at - activated.session.started_at).total_seconds()) == 3600

    duplicate = asyncio.run(service.activate_prepared_session_for_pc("PC-02"))
    assert duplicate is None
    assert prepared_repository.get_active_for_pc("PC-02") is None


def test_cancel_prepared_session_creates_no_charge(temp_directory: str) -> None:
    service, session_repository, _ = create_service(Path(temp_directory) / "cancel.sqlite3")
    prepared = service.prepare_offline_session(
        PreparedSessionStartRequest(
            pc_name="PC-02",
            session_mode="open_time",
        ),
        "cashier-user",
    ).prepared_session
    cancelled = service.cancel_prepared_session(prepared.prepared_session_id, "cashier-user")
    assert cancelled.prepared_session.status == "cancelled"
    assert session_repository.get_active_session_for_pc("PC-02") is None


def test_seeded_layout_does_not_create_registered_dashboard_pcs(temp_directory: str) -> None:
    database_path = Path(temp_directory) / "layout.sqlite3"
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    initialize_database()

    from server import main as server_main

    server_main.pc_registry.clear()
    server_main.recovery_flags.clear()
    server_main.touch_pc("PC-01", "192.168.1.101")
    server_main.touch_pc("PC-02", "192.168.1.102")

    first_records = server_main.get_registered_pcs(object())
    assert [record.pc_name for record in first_records] == ["PC-01", "PC-02"]
    assert len(first_records) == 2
    assert "PC-03" not in {record.pc_name for record in first_records}

    server_main.pc_registry["PC-02"] = server_main.pc_registry["PC-02"].model_copy(
        update={"status": "offline"}
    )
    powered_off_records = server_main.get_registered_pcs(object())
    assert [record.pc_name for record in powered_off_records] == ["PC-01", "PC-02"]
    pc_02 = next(record for record in powered_off_records if record.pc_name == "PC-02")
    assert pc_02.connection_status == "offline"
    assert pc_02.power_state == "powered_off"

    server_main.touch_pc("PC-03", "192.168.1.103")
    after_install_records = server_main.get_registered_pcs(object())
    assert [record.pc_name for record in after_install_records] == ["PC-01", "PC-02", "PC-03"]
    assert [record.layout_order for record in after_install_records] == [10, 20, 30]


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_prepare_fixed_time_activates_on_reconnect(temp_directory)
        test_cancel_prepared_session_creates_no_charge(temp_directory)
        test_seeded_layout_does_not_create_registered_dashboard_pcs(temp_directory)
    print("Prepared startup smoke test passed.")


if __name__ == "__main__":
    main()

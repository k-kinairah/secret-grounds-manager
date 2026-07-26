import asyncio
import os
import sys
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class FakeWebSocket:
    async def accept(self) -> None:
        pass


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "remote.sqlite3")

        from server.database import initialize_database
        from server.main import (
            broadcast_announcement,
            connection_manager,
            list_remote_commands,
            pc_registry,
            recovery_flags,
            remote_command_repository,
            send_pc_message,
            send_power_command,
            update_pc_mac_address,
            wake_pc,
        )
        from server.connection_manager import ClientConnectionManager
        import server.main as main_module
        from server.schemas import (
            BroadcastCommandRequest,
            CommandAcknowledgement,
            MessageCommandRequest,
            PCMacAddressUpdate,
            PCRecord,
            PowerCommandRequest,
            SessionRecord,
            UserCreateRequest,
        )
        from server.session_repository import SessionRepository
        from server.time_utils import current_utc_time
        from server.user_repository import UserRepository

        initialize_database()
        tracking_manager = ClientConnectionManager()
        first_socket = FakeWebSocket()
        second_socket = FakeWebSocket()
        asyncio.run(tracking_manager.connect(" PC-TRACK ", first_socket))  # type: ignore[arg-type]
        assert tracking_manager.is_connected("PC-TRACK")
        assert tracking_manager.active_pc_names() == ["PC-TRACK"]
        asyncio.run(tracking_manager.connect("PC-TRACK", second_socket))  # type: ignore[arg-type]
        tracking_manager.disconnect("PC-TRACK", first_socket)  # type: ignore[arg-type]
        assert tracking_manager.is_connected("PC-TRACK")
        tracking_manager.disconnect("PC-TRACK", second_socket)  # type: ignore[arg-type]
        assert not tracking_manager.is_connected("PC-TRACK")

        user_repository = UserRepository()
        owner = user_repository.create_user(
            UserCreateRequest(
                username="owner",
                display_name="Owner",
                password="password123",
                role="owner",
            )
        )
        admin = user_repository.create_user(
            UserCreateRequest(
                username="admin",
                display_name="Admin",
                password="password123",
                role="admin",
            )
        )
        cashier = user_repository.create_user(
            UserCreateRequest(
                username="cashier",
                display_name="Cashier",
                password="password123",
                role="cashier",
            )
        )
        owner_user = user_repository.get_user(owner.user_id)
        admin_user = user_repository.get_user(admin.user_id)
        cashier_user = user_repository.get_user(cashier.user_id)
        assert owner_user is not None
        assert admin_user is not None
        assert cashier_user is not None

        now = current_utc_time()
        pc_registry.clear()
        recovery_flags.clear()
        pc_registry["PC-IDLE"] = PCRecord(
            pc_name="PC-IDLE",
            ip_address="127.0.0.1",
            status="online",
            last_seen=now,
        )
        pc_registry["PC-ACTIVE"] = PCRecord(
            pc_name="PC-ACTIVE",
            ip_address="127.0.0.2",
            status="online",
            last_seen=now,
        )
        pc_registry["PC-OFFLINE"] = PCRecord(
            pc_name="PC-OFFLINE",
            ip_address="127.0.0.3",
            status="offline",
            last_seen=now,
        )
        connected = {"PC-IDLE", "PC-ACTIVE"}
        sent_commands: list[tuple[str, str]] = []

        def is_connected(pc_name: str) -> bool:
            return pc_name in connected

        async def send_command(pc_name: str, command, on_acknowledgement=None):
            sent_commands.append((pc_name, command.type))
            acknowledgements = [
                CommandAcknowledgement(command_id=command.command_id, status="received", message="Command received"),
                CommandAcknowledgement(command_id=command.command_id, status="completed", message="ok"),
            ]
            for acknowledgement in acknowledgements:
                if on_acknowledgement is not None:
                    on_acknowledgement(acknowledgement)
            return acknowledgements

        connection_manager.is_connected = is_connected  # type: ignore[method-assign]
        connection_manager.send_command = send_command  # type: ignore[method-assign]

        try:
            asyncio.run(
                send_power_command(
                    "PC-IDLE",
                    "shutdown",
                    PowerCommandRequest(),
                    cashier_user,
                )
            )
        except HTTPException as error:
            assert error.status_code == 403
        else:
            raise AssertionError("Cashier power command should be denied")

        shutdown = asyncio.run(
            send_power_command(
                "PC-IDLE",
                "shutdown",
                PowerCommandRequest(),
                admin_user,
            )
        )
        assert shutdown.command.status == "completed"
        assert sent_commands[-1] == ("PC-IDLE", "shutdown")

        session_repository = SessionRepository()
        active_session = SessionRecord(
            session_id="session-active",
            pc_name="PC-ACTIVE",
            status="active",
            started_at=now,
            scheduled_end_at=now + timedelta(hours=1),
            original_seconds=3600,
            total_seconds=3600,
            amount_paid=None,
            created_at=now,
            base_amount=Decimal("0.00"),
            additional_amount=Decimal("0.00"),
            total_amount=Decimal("0.00"),
        )
        session_repository.create_session(active_session)
        try:
            asyncio.run(
                send_power_command(
                    "PC-ACTIVE",
                    "sleep",
                    PowerCommandRequest(),
                    admin_user,
                )
            )
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Active session power command should require force")

        forced = asyncio.run(
            send_power_command(
                "PC-ACTIVE",
                "sleep",
                PowerCommandRequest(force=True, confirm=True, reason="Customer requested reset"),
                owner_user,
            )
        )
        assert forced.command.forced is True
        assert forced.command.force_reason == "Customer requested reset"
        assert recovery_flags["PC-ACTIVE"]["session_id"] == "session-active"

        message = asyncio.run(
            send_pc_message(
                "PC-IDLE",
                MessageCommandRequest(
                    title="Secret Grounds",
                    message="Please return to the cashier.",
                    severity="warning",
                ),
                cashier_user,
            )
        )
        assert message.command.command_type == "show_message"

        broadcast = asyncio.run(
            broadcast_announcement(
                BroadcastCommandRequest(
                    title="Closing",
                    message="The shop is closing soon.",
                    severity="info",
                    pc_names=["PC-IDLE", "PC-OFFLINE"],
                ),
                cashier_user,
            )
        )
        assert len(broadcast.commands) == 1
        assert broadcast.offline_targets == ["PC-OFFLINE"]

        try:
            wake_pc("PC-OFFLINE", admin_user)
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Missing MAC address should reject Wake-on-LAN")

        updated = update_pc_mac_address(
            "PC-OFFLINE",
            PCMacAddressUpdate(mac_address="aa-bb-cc-dd-ee-ff"),
            admin_user,
        )
        assert updated.mac_address == "AA:BB:CC:DD:EE:FF"
        packets: list[str] = []
        main_module.send_wake_on_lan_packet = lambda mac: packets.append(mac)  # type: ignore[assignment]
        wake = wake_pc("PC-OFFLINE", admin_user)
        assert wake.command.status == "completed"
        assert wake.command.client_response == "Wake-on-LAN packet_sent"
        assert packets == ["AA:BB:CC:DD:EE:FF"]

        admin_history = list_remote_commands(admin_user)
        cashier_history = list_remote_commands(cashier_user)
        assert any(command.command_type == "wake_on_lan" for command in admin_history)
        assert all(command.requested_by_user_id == cashier.user_id for command in cashier_history)
        assert all(command.command_type in {"show_message", "show_announcement"} for command in cashier_history)
        assert remote_command_repository.get(shutdown.command.command_id).status == "completed"

        print("Remote controls smoke test passed.")


if __name__ == "__main__":
    main()

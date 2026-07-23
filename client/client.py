import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread
from typing import Any
from urllib.parse import quote

import requests

from client.config import (
    ENABLE_DEV_ESCAPE,
    ENABLE_LOCK_SCREEN,
    HEARTBEAT_INTERVAL_SECONDS,
    PC_NAME,
    REQUEST_TIMEOUT_SECONDS,
    SESSION_RECOVERY_PATH,
    SERVER_URL,
)
from client.lock_screen import LockScreenController
from client.session_recovery import SessionRecoveryStore
from client.utils import get_local_ip
from client.websocket_transport import SimpleWebSocketClient


@dataclass
class LocalSession:
    session_id: str
    deadline_monotonic: float
    scheduled_end_at: str
    stop_event: Event
    timer_thread: Thread


class SecretGroundsClient:
    """Persistent shop client that owns registration, heartbeat, and retry flow."""

    def __init__(
        self,
        server_url: str,
        pc_name: str,
        heartbeat_interval: int,
        request_timeout: int,
        lock_screen: LockScreenController | None = None,
        recovery_store: SessionRecoveryStore | None = None,
        enable_lock_screen: bool = ENABLE_LOCK_SCREEN,
        enable_dev_escape: bool = ENABLE_DEV_ESCAPE,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.pc_name = pc_name
        self.heartbeat_interval = heartbeat_interval
        self.request_timeout = request_timeout
        self.session = requests.Session()
        self.stop_event = Event()
        self.active_session: LocalSession | None = None
        self.active_session_lock = Lock()
        self.processed_command_ids: set[str] = set()
        self.websocket_lock = Lock()
        self.current_websocket: SimpleWebSocketClient | None = None
        self.lock_screen = lock_screen or LockScreenController(
            pc_name=pc_name,
            enable_gui=enable_lock_screen,
            enable_dev_escape=enable_dev_escape,
        )
        self.recovery_store = recovery_store or SessionRecoveryStore(
            SESSION_RECOVERY_PATH
        )
        self.lock_screen.start()

    def build_payload(self) -> dict[str, str]:
        # Keep payload creation centralized so future client metadata is easy to add.
        return {
            "pc_name": self.pc_name,
            "ip_address": get_local_ip(),
        }

    def post(self, path: str) -> requests.Response:
        return self.session.post(
            f"{self.server_url}{path}",
            json=self.build_payload(),
            timeout=self.request_timeout,
        )

    def register_once(self) -> bool:
        try:
            response = self.post("/register")
            response.raise_for_status()
            print("Connected to Secret Grounds Server!")
            print(response.json())
            return True

        except requests.ConnectionError:
            print("Could not connect to the server. Retrying soon...")

        except requests.Timeout:
            print("The server took too long to respond. Retrying soon...")

        except requests.RequestException as error:
            print(f"Registration failed: {error}. Retrying soon...")

        return False

    def register_until_connected(self) -> None:
        # Startup blocks here until the server accepts this PC into the registry.
        while not self.register_once():
            time.sleep(self.heartbeat_interval)

    def send_heartbeat(self) -> bool:
        try:
            response = self.post("/heartbeat")

            if response.status_code == 404:
                print("Server forgot this PC. Re-registering...")
                self.register_until_connected()
                return True

            response.raise_for_status()
            print(f"Heartbeat sent for {self.pc_name}")
            return True

        except requests.ConnectionError:
            print("Server unavailable. Retrying heartbeat soon...")

        except requests.Timeout:
            print("Heartbeat timed out. Retrying soon...")

        except requests.RequestException as error:
            print(f"Heartbeat failed: {error}. Retrying soon...")

        return False

    def get_server_session_state(self) -> dict[str, Any] | None:
        try:
            response = self.session.get(
                f"{self.server_url}/pcs/{quote(self.pc_name)}/session-state",
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as error:
            print(f"Could not fetch server session state: {error}")
            return None

    def parse_utc_datetime(self, value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def calculate_remaining_seconds(
        self,
        scheduled_end_at: str,
        server_time: str | None = None,
    ) -> int:
        now = datetime.now(timezone.utc)
        if server_time is not None:
            server_now = self.parse_utc_datetime(server_time)
            discrepancy = abs((server_now - now).total_seconds())
            if discrepancy > 30:
                print(
                    "Significant clock discrepancy detected: "
                    f"{int(discrepancy)} seconds between client and server."
                )
            now = server_now

        end_at = self.parse_utc_datetime(scheduled_end_at)
        return max(0, int((end_at - now).total_seconds()))

    def build_websocket_url(self) -> str:
        websocket_base_url = self.server_url.replace("https://", "wss://", 1)
        websocket_base_url = websocket_base_url.replace("http://", "ws://", 1)
        return f"{websocket_base_url}/ws/{quote(self.pc_name)}"

    def build_acknowledgement(
        self,
        command_id: str,
        status: str,
        message: str | None = None,
    ) -> dict[str, str | None]:
        return {
            "command_id": command_id,
            "status": status,
            "message": message,
        }

    def execute_command(
        self,
        command: dict[str, Any],
        websocket: SimpleWebSocketClient,
    ) -> dict[str, str | None]:
        command_id = str(command.get("command_id", ""))
        command_type = command.get("type")

        if command_id in self.processed_command_ids:
            return self.build_acknowledgement(
                command_id=command_id,
                status="completed",
                message="Duplicate command ignored",
            )

        self.processed_command_ids.add(command_id)

        if command_type == "ping":
            return self.build_acknowledgement(
                command_id=command_id,
                status="completed",
                message="pong",
            )

        if command_type == "start_session":
            return self.start_local_session(command, websocket)

        if command_type == "add_time":
            return self.add_time_to_local_session(command)

        if command_type == "end_session":
            return self.end_local_session(command)

        return self.build_acknowledgement(
            command_id=command_id,
            status="failed",
            message=f"Unsupported command type: {command_type}",
        )

    def initialize_local_session(
        self,
        session_id: str,
        duration_seconds: int,
        scheduled_end_at: str,
    ) -> bool:
        with self.active_session_lock:
            if self.active_session is not None:
                return False

            stop_event = Event()
            deadline_monotonic = time.monotonic() + duration_seconds
            timer_thread = Thread(
                target=self.run_session_timer,
                args=(session_id, deadline_monotonic, stop_event),
                daemon=True,
            )
            self.active_session = LocalSession(
                session_id=session_id,
                deadline_monotonic=deadline_monotonic,
                scheduled_end_at=scheduled_end_at,
                stop_event=stop_event,
                timer_thread=timer_thread,
            )
            self.lock_screen.hide_for_session()
            self.recovery_store.save(
                session_id=session_id,
                pc_name=self.pc_name,
                scheduled_end_at=scheduled_end_at,
                state="active",
            )
            print(f"Session {session_id} unlocked.")
            timer_thread.start()

        return True

    def start_local_session(
        self,
        command: dict[str, Any],
        websocket: SimpleWebSocketClient,
    ) -> dict[str, str | None]:
        command_id = str(command.get("command_id", ""))
        payload = command.get("payload")

        if not isinstance(payload, dict):
            return self.build_acknowledgement(command_id, "failed", "Invalid payload")

        session_id = payload.get("session_id")
        duration_seconds = payload.get("duration_seconds")
        scheduled_end_at = payload.get("scheduled_end_at")

        if (
            not isinstance(session_id, str)
            or not isinstance(duration_seconds, int)
            or not isinstance(scheduled_end_at, str)
        ):
            return self.build_acknowledgement(command_id, "failed", "Invalid payload")
        if duration_seconds <= 0:
            return self.build_acknowledgement(command_id, "failed", "Invalid duration")

        initialized = self.initialize_local_session(
            session_id=session_id,
            duration_seconds=duration_seconds,
            scheduled_end_at=scheduled_end_at,
        )
        if not initialized:
            return self.build_acknowledgement(
                command_id,
                "failed",
                "Client already has an active session",
            )

        print(f"Session {session_id} started for {duration_seconds} seconds.")
        return self.build_acknowledgement(
            command_id,
            "completed",
            "Session timer initialized",
        )

    def add_time_to_local_session(
        self,
        command: dict[str, Any],
    ) -> dict[str, str | None]:
        command_id = str(command.get("command_id", ""))
        payload = command.get("payload")

        if not isinstance(payload, dict):
            return self.build_acknowledgement(command_id, "failed", "Invalid payload")

        session_id = payload.get("session_id")
        seconds = payload.get("seconds")

        with self.active_session_lock:
            if (
                self.active_session is None
                or self.active_session.session_id != session_id
            ):
                return self.build_acknowledgement(
                    command_id,
                    "failed",
                    "Session ID does not match active timer",
                )
            if not isinstance(seconds, int) or seconds <= 0:
                return self.build_acknowledgement(command_id, "failed", "Invalid seconds")

            self.active_session.deadline_monotonic += seconds
            scheduled_end_at = self.parse_utc_datetime(
                self.active_session.scheduled_end_at
            ) + timedelta(seconds=seconds)
            self.active_session.scheduled_end_at = scheduled_end_at.isoformat()
            self.recovery_store.save(
                session_id=self.active_session.session_id,
                pc_name=self.pc_name,
                scheduled_end_at=self.active_session.scheduled_end_at,
                state="active",
            )

        print(f"Added {seconds} seconds to session {session_id}.")
        return self.build_acknowledgement(command_id, "completed", "Time added")

    def end_local_session(
        self,
        command: dict[str, Any],
    ) -> dict[str, str | None]:
        command_id = str(command.get("command_id", ""))
        payload = command.get("payload")

        if not isinstance(payload, dict):
            return self.build_acknowledgement(command_id, "failed", "Invalid payload")

        session_id = payload.get("session_id")

        with self.active_session_lock:
            if (
                self.active_session is None
                or self.active_session.session_id != session_id
            ):
                return self.build_acknowledgement(
                    command_id,
                    "failed",
                    "Session ID does not match active timer",
                )

            self.active_session.stop_event.set()
            self.active_session = None
            self.recovery_store.clear()
            self.lock_screen.show_locked("Session locked after manual end.")

        print(f"Session {session_id} ended manually.")
        return self.build_acknowledgement(command_id, "completed", "Session ended")

    def run_session_timer(
        self,
        session_id: str,
        deadline_monotonic: float,
        stop_event: Event,
    ) -> None:
        while not stop_event.is_set():
            with self.active_session_lock:
                active_session = self.active_session
                if active_session is None or active_session.session_id != session_id:
                    return
                deadline_monotonic = active_session.deadline_monotonic

            remaining_seconds = max(0, int(deadline_monotonic - time.monotonic()))
            print(f"Session {session_id} remaining: {remaining_seconds} seconds")

            if remaining_seconds <= 0:
                with self.active_session_lock:
                    scheduled_end_at = ""
                    if (
                        self.active_session is not None
                        and self.active_session.session_id == session_id
                    ):
                        scheduled_end_at = self.active_session.scheduled_end_at
                        self.active_session = None

                self.lock_screen.show_locked("Session locked after expiration.")
                self.recovery_store.mark_pending_completion(
                    session_id=session_id,
                    pc_name=self.pc_name,
                    scheduled_end_at=scheduled_end_at
                    or datetime.now(timezone.utc).isoformat(),
                )
                sent = self.send_client_event(
                    {
                        "event_type": "session_finished",
                        "session_id": session_id,
                        "message": "Timer finished",
                    }
                )
                if not sent:
                    print("Session completion is pending server confirmation.")
                print(f"Session {session_id} finished.")
                return

            stop_event.wait(min(5, remaining_seconds))

    def handle_command(self, websocket: SimpleWebSocketClient) -> None:
        command = websocket.receive_json()
        if command.get("event_type") == "session_completion_recorded":
            self.handle_session_completion_confirmation(command)
            return

        command_id = str(command.get("command_id", ""))

        websocket.send_json(
            self.build_acknowledgement(
                command_id=command_id,
                status="received",
                message="Command received",
            )
        )
        websocket.send_json(self.execute_command(command, websocket))

    def run_websocket_loop(self) -> None:
        websocket_url = self.build_websocket_url()

        while not self.stop_event.is_set():
            try:
                self.register_until_connected()
                print(f"Connecting command WebSocket: {websocket_url}")
                with SimpleWebSocketClient(
                    url=websocket_url,
                    timeout=self.request_timeout,
                ) as websocket:
                    print("Command WebSocket connected.")
                    self.set_current_websocket(websocket)
                    self.lock_screen.set_connection_online(True)
                    self.reconcile_session_state()
                    self.flush_pending_completion()

                    while not self.stop_event.is_set():
                        self.handle_command(websocket)

            except (ConnectionError, OSError, ValueError) as error:
                print(f"Command WebSocket disconnected: {error}")
                self.handle_websocket_offline()
                time.sleep(self.heartbeat_interval)

    def run_heartbeat_loop(self) -> None:
        # The long-running loop keeps this process alive as the PC presence agent.
        while not self.stop_event.is_set():
            time.sleep(self.heartbeat_interval)
            self.send_heartbeat()

    def run_forever(self) -> None:
        self.register_until_connected()
        self.reconcile_session_state()

        # Heartbeats and live commands are separate loops so a slow command does
        # not stop presence updates, and a server restart reconnects both paths.
        websocket_thread = Thread(target=self.run_websocket_loop, daemon=True)
        websocket_thread.start()
        self.run_heartbeat_loop()

    def stop(self) -> None:
        self.stop_event.set()
        self.lock_screen.stop()

    def set_current_websocket(self, websocket: SimpleWebSocketClient | None) -> None:
        with self.websocket_lock:
            self.current_websocket = websocket

    def send_client_event(self, event: dict[str, object]) -> bool:
        with self.websocket_lock:
            websocket = self.current_websocket

        if websocket is None:
            print(f"Could not send client event while WebSocket is offline: {event}")
            return False

        try:
            websocket.send_json(event)
            return True
        except (ConnectionError, OSError) as error:
            print(f"Could not send client event: {error}")
            return False

    def handle_session_completion_confirmation(self, message: dict[str, Any]) -> None:
        if not message.get("recorded"):
            print(f"Server rejected completion confirmation: {message}")
            return

        recovery_record = self.recovery_store.load()
        if (
            recovery_record is not None
            and recovery_record.session_id == message.get("session_id")
            and recovery_record.state == "pending_completion"
        ):
            self.recovery_store.clear()
            print(
                "Pending session completion confirmed by server: "
                f"{recovery_record.session_id}"
            )

    def flush_pending_completion(self) -> None:
        recovery_record = self.recovery_store.load()
        if recovery_record is None or recovery_record.state != "pending_completion":
            return

        print(f"Resending pending session completion: {recovery_record.session_id}")
        self.send_client_event(
            {
                "event_type": "session_finished",
                "session_id": recovery_record.session_id,
                "message": "Timer finished",
            }
        )

    def reconcile_session_state(self) -> None:
        recovery_record = self.recovery_store.load()
        server_state = self.get_server_session_state()
        if server_state is None:
            return

        server_session = server_state.get("session")
        has_server_active_session = bool(server_state.get("has_active_session"))

        with self.active_session_lock:
            active_session_id = (
                self.active_session.session_id
                if self.active_session is not None
                else None
            )

        if (
            active_session_id is not None
            and has_server_active_session
            and isinstance(server_session, dict)
            and server_session.get("session_id") == active_session_id
        ):
            print(f"Reconciliation kept running local session: {active_session_id}")
            return

        if recovery_record is None and not has_server_active_session:
            print("Reconciliation: no local or server active session; staying locked.")
            self.lock_screen.show_locked()
            return

        if recovery_record is None and has_server_active_session:
            print("Reconciliation unresolved: server-only active session found.")
            self.lock_screen.show_locked()
            return

        if recovery_record is None:
            return

        if recovery_record.pc_name != self.pc_name:
            print("Reconciliation mismatch: recovery record belongs to another PC.")
            self.lock_screen.show_locked()
            return

        if recovery_record.state == "pending_completion":
            if has_server_active_session:
                self.flush_pending_completion()
            else:
                print("Reconciliation: pending completion already resolved on server.")
                self.recovery_store.clear()
            self.lock_screen.show_locked()
            return

        if not has_server_active_session:
            print("Reconciliation: clearing stale local recovery record.")
            self.recovery_store.clear()
            self.lock_screen.show_locked()
            return

        if not isinstance(server_session, dict):
            self.lock_screen.show_locked()
            return

        if server_session.get("session_id") != recovery_record.session_id:
            print(
                "Reconciliation mismatch: local session "
                f"{recovery_record.session_id} does not match server session "
                f"{server_session.get('session_id')}."
            )
            self.lock_screen.show_locked()
            return

        remaining_seconds = self.calculate_remaining_seconds(
            recovery_record.scheduled_end_at,
            str(server_state.get("server_time")),
        )
        if remaining_seconds > 0:
            print(
                "Reconciliation: restoring local session "
                f"{recovery_record.session_id} with {remaining_seconds} seconds."
            )
            self.initialize_local_session(
                session_id=recovery_record.session_id,
                duration_seconds=remaining_seconds,
                scheduled_end_at=recovery_record.scheduled_end_at,
            )
            return

        print("Reconciliation: recovered session has expired; staying locked.")
        self.lock_screen.show_locked()
        self.recovery_store.mark_pending_completion(
            session_id=recovery_record.session_id,
            pc_name=self.pc_name,
            scheduled_end_at=recovery_record.scheduled_end_at,
        )
        self.flush_pending_completion()

    def handle_websocket_offline(self) -> None:
        self.set_current_websocket(None)
        self.lock_screen.set_connection_online(False)

        with self.active_session_lock:
            has_active_session = self.active_session is not None

        if has_active_session:
            print("WebSocket offline during active session; keeping PC unlocked.")
        else:
            print("WebSocket offline while idle; remaining locked.")
            self.lock_screen.show_locked()


def main() -> None:
    client = SecretGroundsClient(
        server_url=SERVER_URL,
        pc_name=PC_NAME,
        heartbeat_interval=HEARTBEAT_INTERVAL_SECONDS,
        request_timeout=REQUEST_TIMEOUT_SECONDS,
        enable_lock_screen=ENABLE_LOCK_SCREEN,
        enable_dev_escape=ENABLE_DEV_ESCAPE,
    )

    try:
        client.run_forever()
    except KeyboardInterrupt:
        client.stop()
        print("Secret Grounds client stopped.")


def register_with_server() -> None:
    client = SecretGroundsClient(
        server_url=SERVER_URL,
        pc_name=PC_NAME,
        heartbeat_interval=HEARTBEAT_INTERVAL_SECONDS,
        request_timeout=REQUEST_TIMEOUT_SECONDS,
        enable_lock_screen=ENABLE_LOCK_SCREEN,
        enable_dev_escape=ENABLE_DEV_ESCAPE,
    )
    client.register_until_connected()


if __name__ == "__main__":
    main()

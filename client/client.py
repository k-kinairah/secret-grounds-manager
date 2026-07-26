import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread
from typing import Any, Callable
from urllib.parse import quote

import requests

from client.config import (
    ClientConfig,
    ClientConfigError,
    ENABLE_DEV_ESCAPE,
    ENABLE_LOCK_SCREEN,
    HEARTBEAT_INTERVAL_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    SESSION_RECOVERY_PATH,
    get_client_config_path,
    load_client_config,
)
from client.lock_screen import LockScreenController
from client.remote_controls import RemoteControlHandler
from client.session_recovery import SessionRecoveryStore
from client.setup_wizard import show_setup_wizard
from client.utils import get_local_ip, get_local_mac_address
from client.websocket_transport import SimpleWebSocketClient


@dataclass
class LocalSession:
    session_id: str
    deadline_monotonic: float
    scheduled_end_at: str
    stop_event: Event
    timer_thread: Thread | None
    session_mode: str = "prepaid"


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
        open_setup_wizard: Callable[[], None] | None = None,
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
        self.remote_controls = RemoteControlHandler(pc_name)
        self.websocket_lock = Lock()
        self.current_websocket: SimpleWebSocketClient | None = None
        self.lock_screen = lock_screen or LockScreenController(
            pc_name=pc_name,
            enable_gui=enable_lock_screen,
            enable_dev_escape=enable_dev_escape,
            open_setup_wizard=open_setup_wizard,
        )
        self.recovery_store = recovery_store or SessionRecoveryStore(
            SESSION_RECOVERY_PATH
        )
        self.lock_screen.start()

    def build_payload(self, include_mac: bool = False) -> dict[str, str | None]:
        # Keep payload creation centralized so future client metadata is easy to add.
        payload: dict[str, str | None] = {
            "pc_name": self.pc_name,
            "ip_address": get_local_ip(),
        }
        if include_mac:
            payload["mac_address"] = get_local_mac_address()
        return payload

    def post(self, path: str) -> requests.Response:
        return self.session.post(
            f"{self.server_url}{path}",
            json=self.build_payload(),
            timeout=self.request_timeout,
        )

    def register_once(self) -> bool:
        try:
            response = self.session.post(
                f"{self.server_url}/register",
                json=self.build_payload(include_mac=True),
                timeout=self.request_timeout,
            )
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

        if command_type in {"shutdown", "restart", "sleep"}:
            return self.handle_remote_power_command(command)

        if command_type in {"show_message", "show_announcement"}:
            return self.handle_remote_message_command(command)

        return self.build_acknowledgement(
            command_id=command_id,
            status="failed",
            message=f"Unsupported command type: {command_type}",
        )

    def handle_remote_power_command(self, command: dict[str, Any]) -> dict[str, str | None]:
        command_id = str(command.get("command_id", ""))
        command_type = str(command.get("type", ""))
        payload = command.get("payload")
        if not isinstance(payload, dict):
            return self.build_acknowledgement(command_id, "failed", "invalid_payload")
        try:
            message = self.remote_controls.handle_power_command(command_type, payload)
            return self.build_acknowledgement(command_id, "completed", message)
        except Exception as error:
            print(f"Remote {command_type} rejected: {error}")
            return self.build_acknowledgement(command_id, "failed", str(error))

    def handle_remote_message_command(self, command: dict[str, Any]) -> dict[str, str | None]:
        command_id = str(command.get("command_id", ""))
        command_type = str(command.get("type", ""))
        payload = command.get("payload")
        if not isinstance(payload, dict):
            return self.build_acknowledgement(command_id, "failed", "invalid_payload")
        try:
            message = self.remote_controls.show_message(
                payload,
                announcement=command_type == "show_announcement",
            )
            return self.build_acknowledgement(command_id, "completed", message)
        except Exception as error:
            print(f"Remote message rejected: {error}")
            return self.build_acknowledgement(command_id, "failed", str(error))

    def initialize_local_session(
        self,
        session_id: str,
        duration_seconds: int | None,
        scheduled_end_at: str,
        session_mode: str = "prepaid",
    ) -> bool:
        old_stop_event: Event | None = None
        with self.active_session_lock:
            local_session_id = (
                self.active_session.session_id
                if self.active_session is not None
                else None
            )
            print(
                "Initializing local session: "
                f"server_session_id={session_id}, local_session_id={local_session_id}, "
                f"session_mode={session_mode}"
            )
            if self.active_session is not None:
                if self.active_session.session_id == session_id:
                    self.recovery_store.save(
                        session_id=session_id,
                        pc_name=self.pc_name,
                        scheduled_end_at=scheduled_end_at
                        or datetime.now(timezone.utc).isoformat(),
                        state="active",
                        session_mode=session_mode,
                    )
                    try:
                        self.lock_screen.hide_for_session()
                    except Exception as error:
                        print(
                            "Lock screen hide failed for already-active "
                            f"session_id={session_id}: {error}"
                        )
                        return False
                    print(
                        "Local session already active; refreshed persisted state "
                        f"and hid lock screen for session_id={session_id}."
                    )
                    print(f"Local session {session_id} is already active.")
                    return True
                old_stop_event = self.active_session.stop_event
                print(
                    "Replacing stale local session "
                    f"{self.active_session.session_id} with server session {session_id}."
                )

            stop_event = Event()
            deadline_monotonic = time.monotonic() + (duration_seconds or 0)
            timer_thread = None
            if session_mode != "open_time":
                if duration_seconds is None or duration_seconds <= 0:
                    return False
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
                session_mode=session_mode,
            )
            self.recovery_store.save(
                session_id=session_id,
                pc_name=self.pc_name,
                scheduled_end_at=scheduled_end_at
                or datetime.now(timezone.utc).isoformat(),
                state="active",
                session_mode=session_mode,
            )
            print(f"Local session persisted: session_id={session_id}, state=active.")
            if old_stop_event is not None:
                old_stop_event.set()
            try:
                self.lock_screen.hide_for_session()
                print(f"Lock screen hidden for session_id={session_id}.")
            except Exception as error:
                self.active_session.stop_event.set()
                self.active_session = None
                self.recovery_store.clear()
                print(f"Lock screen hide failed for session_id={session_id}: {error}")
                return False
            print(f"Session {session_id} unlocked.")
            if timer_thread is not None:
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
        session_mode = payload.get("session_mode", "prepaid")
        duration_seconds = payload.get("duration_seconds")
        scheduled_end_at = payload.get("scheduled_end_at")
        print(
            "start_session received: "
            f"server_session_id={session_id}, session_mode={session_mode}, "
            f"duration_seconds={duration_seconds}, scheduled_end_at={scheduled_end_at}"
        )

        if session_mode == "open_time":
            if not isinstance(session_id, str):
                return self.build_acknowledgement(command_id, "failed", "Invalid payload")
            initialized = self.initialize_local_session(
                session_id=session_id,
                duration_seconds=None,
                scheduled_end_at=str(payload.get("started_at") or ""),
                session_mode="open_time",
            )
            if not initialized:
                return self.build_acknowledgement(
                    command_id,
                    "failed",
                    "Client already has an active session",
                )
            print(f"Open-time session {session_id} started.")
            return self.build_acknowledgement(
                command_id,
                "completed",
                "Open-time session initialized",
            )

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
            session_mode=session_mode if isinstance(session_mode, str) else "prepaid",
        )
        if not initialized:
            return self.build_acknowledgement(
                command_id,
                "failed",
                "Client already has an active session",
            )

        print(f"{session_mode} session {session_id} started for up to {duration_seconds} seconds.")
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
            if self.active_session.session_mode == "open_time":
                return self.build_acknowledgement(
                    command_id,
                    "failed",
                    "Open-time sessions do not support add time",
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
            local_session_id = (
                self.active_session.session_id
                if self.active_session is not None
                else None
            )
        print(
            "end_session received: "
            f"server_session_id={session_id}, local_session_id={local_session_id}"
        )

        should_reconcile = False
        with self.active_session_lock:
            if (
                self.active_session is None
                or self.active_session.session_id != session_id
            ):
                should_reconcile = True

        if should_reconcile and isinstance(session_id, str):
            print(
                "end_session local cache miss; reconciling before rejection: "
                f"server_session_id={session_id}, local_session_id={local_session_id}"
            )
            self.reconcile_session_state()

        with self.active_session_lock:
            if (
                self.active_session is None
                or self.active_session.session_id != session_id
            ):
                if self.active_session is None:
                    print(
                        "Rejecting end_session command: "
                        f"no local session for target {session_id} after reconciliation."
                    )
                    return self.build_acknowledgement(
                        command_id,
                        "failed",
                        "no_matching_local_session",
                    )

                print(
                    "Rejecting end_session command: local session "
                    f"{self.active_session.session_id} does not match target "
                    f"{session_id} after reconciliation."
                )
                return self.build_acknowledgement(
                    command_id,
                    "failed",
                    "session_id_mismatch",
                )

            self.active_session.stop_event.set()
            self.active_session = None
            self.recovery_store.clear()
            self.lock_screen.show_locked("Session locked after manual end.")
            print(f"Lock screen shown after end_session for session_id={session_id}.")

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
                    session_mode="prepaid",
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

    def clear_local_session_state(self, reason: str) -> None:
        with self.active_session_lock:
            if self.active_session is not None:
                print(
                    "Clearing local active session: "
                    f"local_session_id={self.active_session.session_id}, reason={reason}"
                )
                self.active_session.stop_event.set()
                self.active_session = None
        self.recovery_store.clear()

    def adopt_server_session(
        self,
        server_state: dict[str, Any],
        server_session: dict[str, Any],
    ) -> bool:
        session_id = server_session.get("session_id")
        session_mode = server_session.get("session_mode", "prepaid")
        scheduled_end_at = server_session.get("scheduled_end_at")
        server_time = server_state.get("server_time")

        if not isinstance(session_id, str):
            print("Reconciliation failed: server session is missing session_id.")
            return False

        session_mode = session_mode if isinstance(session_mode, str) else "prepaid"
        print(
            "Reconciliation adopting server session: "
            f"server_session_id={session_id}, session_mode={session_mode}"
        )

        if session_mode == "open_time":
            return self.initialize_local_session(
                session_id=session_id,
                duration_seconds=None,
                scheduled_end_at=str(scheduled_end_at or server_time or ""),
                session_mode="open_time",
            )

        if not isinstance(scheduled_end_at, str):
            print(
                "Reconciliation failed: countdown server session is missing "
                f"scheduled_end_at for session_id={session_id}."
            )
            return False

        remaining_seconds = self.calculate_remaining_seconds(
            scheduled_end_at,
            str(server_time) if server_time is not None else None,
        )
        if remaining_seconds <= 0:
            print(
                "Reconciliation: authoritative server session has expired locally; "
                f"session_id={session_id}."
            )
            self.lock_screen.show_locked()
            print(f"Lock screen shown for expired session_id={session_id}.")
            self.recovery_store.mark_pending_completion(
                session_id=session_id,
                pc_name=self.pc_name,
                scheduled_end_at=scheduled_end_at,
                session_mode=session_mode,
            )
            self.flush_pending_completion()
            return False

        return self.initialize_local_session(
            session_id=session_id,
            duration_seconds=remaining_seconds,
            scheduled_end_at=scheduled_end_at,
            session_mode=session_mode,
        )

    def reconcile_session_state(self) -> None:
        recovery_record = self.recovery_store.load()
        server_state = self.get_server_session_state()
        if server_state is None:
            print("Reconciliation skipped: server state unavailable.")
            return

        server_session = server_state.get("session")
        has_server_active_session = bool(server_state.get("has_active_session"))

        with self.active_session_lock:
            active_session_id = (
                self.active_session.session_id
                if self.active_session is not None
                else None
            )
        recovery_session_id = (
            recovery_record.session_id if recovery_record is not None else None
        )
        server_session_id = (
            server_session.get("session_id")
            if isinstance(server_session, dict)
            else None
        )
        print(
            "Reconciliation result input: "
            f"server_has_active={has_server_active_session}, "
            f"server_session_id={server_session_id}, "
            f"local_session_id={active_session_id}, "
            f"recovery_session_id={recovery_session_id}"
        )

        if (
            active_session_id is not None
            and has_server_active_session
            and isinstance(server_session, dict)
            and server_session.get("session_id") == active_session_id
        ):
            print(f"Reconciliation kept running local session: {active_session_id}")
            return

        if not has_server_active_session:
            print("Reconciliation: server has no active session; clearing local state.")
            self.clear_local_session_state("server_has_no_active_session")
            self.lock_screen.show_locked()
            print("Lock screen shown after reconciliation found no server session.")
            return

        if not isinstance(server_session, dict):
            print("Reconciliation: server reported active session without details.")
            self.lock_screen.show_locked()
            return

        if recovery_record is not None and recovery_record.pc_name != self.pc_name:
            print("Reconciliation mismatch: recovery record belongs to another PC.")
            self.recovery_store.clear()

        if (
            recovery_record is not None
            and recovery_record.session_id != server_session.get("session_id")
        ):
            print(
                "Reconciliation replacing stale recovery session "
                f"{recovery_record.session_id} does not match server session "
                f"{server_session.get('session_id')}."
            )

        if self.adopt_server_session(server_state, server_session):
            print(
                "Reconciliation adopted active server session: "
                f"session_id={server_session.get('session_id')}."
            )
            return

        print("Reconciliation failed to adopt server session; staying locked.")
        self.lock_screen.show_locked()

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


def open_setup_wizard_async() -> None:
    def run_wizard() -> None:
        try:
            saved_config = show_setup_wizard(
                get_client_config_path(),
                title="Secret Grounds Client Setup",
                allow_cancel=True,
            )
            if saved_config is not None:
                print(
                    "Secret Grounds configuration saved. "
                    "Restart the client to use the new server settings."
                )
        except ClientConfigError as error:
            print(f"Could not open setup wizard: {error}")

    Thread(target=run_wizard, daemon=True).start()


def load_runtime_config() -> ClientConfig:
    config_path = get_client_config_path()
    if not config_path.exists():
        print(f"First run detected. Opening setup wizard for {config_path}.")
        try:
            runtime_config = show_setup_wizard(
                config_path,
                title="Secret Grounds First-Run Setup",
                allow_cancel=False,
            )
        except ClientConfigError as error:
            print("Secret Grounds client configuration needs attention.")
            print(f"Config file: {config_path}")
            print(f"Problem: {error}")
            raise SystemExit(1) from error
        if runtime_config is None:
            raise SystemExit(1)
        return runtime_config

    try:
        return load_client_config(config_path)
    except ClientConfigError as error:
        print("Secret Grounds client configuration needs attention.")
        print(f"Config file: {config_path}")
        print(f"Problem: {error}")
        raise SystemExit(1) from error


def main() -> None:
    runtime_config = load_runtime_config()

    print(
        "Secret Grounds client attempting to connect to "
        f"{runtime_config.server_url} as {runtime_config.pc_name}."
    )
    client = SecretGroundsClient(
        server_url=runtime_config.server_url,
        pc_name=runtime_config.pc_name,
        heartbeat_interval=HEARTBEAT_INTERVAL_SECONDS,
        request_timeout=REQUEST_TIMEOUT_SECONDS,
        enable_lock_screen=ENABLE_LOCK_SCREEN,
        enable_dev_escape=ENABLE_DEV_ESCAPE,
        open_setup_wizard=open_setup_wizard_async,
    )

    try:
        client.run_forever()
    except KeyboardInterrupt:
        client.stop()
        print("Secret Grounds client stopped.")


def register_with_server() -> None:
    runtime_config = load_runtime_config()

    print(
        "Secret Grounds client attempting to connect to "
        f"{runtime_config.server_url} as {runtime_config.pc_name}."
    )
    client = SecretGroundsClient(
        server_url=runtime_config.server_url,
        pc_name=runtime_config.pc_name,
        heartbeat_interval=HEARTBEAT_INTERVAL_SECONDS,
        request_timeout=REQUEST_TIMEOUT_SECONDS,
        enable_lock_screen=ENABLE_LOCK_SCREEN,
        enable_dev_escape=ENABLE_DEV_ESCAPE,
    )
    client.register_until_connected()


if __name__ == "__main__":
    main()

import asyncio

from fastapi import WebSocket

from server.schemas import ClientCommand, CommandAcknowledgement


class ClientConnectionManager:
    """In-memory WebSocket registry for live commands.

    The manager owns active socket references and command delivery. HTTP routes
    can ask it whether a PC is connected without knowing WebSocket details.
    """

    def __init__(self) -> None:
        self.active_connections: dict[str, WebSocket] = {}
        self.pending_acknowledgements: dict[str, asyncio.Queue[CommandAcknowledgement]] = {}
        self._send_locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, pc_name: str) -> asyncio.Lock:
        # One lock per connection, not one for the whole manager -- a
        # command send for PC A must never block a keepalive send for PC B.
        lock = self._send_locks.get(pc_name)
        if lock is None:
            lock = asyncio.Lock()
            self._send_locks[pc_name] = lock
        return lock

    def normalize_pc_name(self, pc_name: str) -> str:
        return pc_name.strip()

    def active_pc_names(self) -> list[str]:
        return sorted(self.active_connections)

    def log_active_connections(self, event: str, pc_name: str) -> None:
        active_pc_names = self.active_pc_names()
        print(
            "Command WebSocket "
            f"{event}: pc_name={pc_name}, "
            f"active_connection_count={len(active_pc_names)}, "
            f"active_pc_names={active_pc_names}"
        )

    async def connect(self, pc_name: str, websocket: WebSocket) -> None:
        normalized_pc_name = self.normalize_pc_name(pc_name)
        await websocket.accept()
        had_existing = normalized_pc_name in self.active_connections
        self.active_connections[normalized_pc_name] = websocket
        self.log_active_connections(
            "reconnected" if had_existing else "connected",
            normalized_pc_name,
        )

    def disconnect(self, pc_name: str, websocket: WebSocket) -> None:
        normalized_pc_name = self.normalize_pc_name(pc_name)
        if self.active_connections.get(normalized_pc_name) is websocket:
            self.active_connections.pop(normalized_pc_name, None)
            self._send_locks.pop(normalized_pc_name, None)
            self.log_active_connections("disconnected", normalized_pc_name)
            return

        self.log_active_connections("stale_disconnect_ignored", normalized_pc_name)

    def is_connected(self, pc_name: str) -> bool:
        return self.normalize_pc_name(pc_name) in self.active_connections

    async def send_command(
        self,
        pc_name: str,
        command: ClientCommand,
        on_acknowledgement=None,
    ) -> list[CommandAcknowledgement]:
        acknowledgement_queue: asyncio.Queue[CommandAcknowledgement] = asyncio.Queue()
        self.pending_acknowledgements[command.command_id] = acknowledgement_queue

        normalized_pc_name = self.normalize_pc_name(pc_name)
        websocket = self.active_connections[normalized_pc_name]
        print(
            "Command WebSocket send: "
            f"pc_name={normalized_pc_name}, command_id={command.command_id}, "
            f"type={command.type}"
        )
        async with self._lock_for(normalized_pc_name):
            await websocket.send_json(command.model_dump(mode="json"))

        acknowledgements: list[CommandAcknowledgement] = []
        try:
            while len(acknowledgements) < 2:
                acknowledgement = await acknowledgement_queue.get()
                print(
                    "Command WebSocket acknowledgement received: "
                    f"pc_name={normalized_pc_name}, command_id={acknowledgement.command_id}, "
                    f"status={acknowledgement.status}, message={acknowledgement.message}"
                )
                acknowledgements.append(acknowledgement)
                if on_acknowledgement is not None:
                    on_acknowledgement(acknowledgement)

                if acknowledgement.status in {"completed", "failed"}:
                    break

            return acknowledgements
        finally:
            self.pending_acknowledgements.pop(command.command_id, None)

    async def send_keepalive(self, pc_name: str) -> bool:
        """A tiny app-level frame so this connection can never sit idle long
        enough for a NAT/router/firewall to silently kill it. Confirmed
        live: an otherwise-healthy LAN link between a customer PC and the
        cashier server still saw the command WebSocket drop with a
        "connection aborted" error after stretches with no real command
        traffic -- ordinary idle-connection timeout behavior, not an app
        bug, and not something this app's separate HTTP heartbeat (a
        different connection entirely) does anything to prevent.

        Deliberately independent of whatever WebSocket-protocol-level ping
        uvicorn's own default configuration may or may not be sending --
        this doesn't rely on that being active. Returns False (never
        raises) if the connection is already gone, so the periodic caller
        can just stop rather than crash its own loop."""
        normalized_pc_name = self.normalize_pc_name(pc_name)
        websocket = self.active_connections.get(normalized_pc_name)
        if websocket is None:
            return False
        try:
            async with self._lock_for(normalized_pc_name):
                await websocket.send_json({"event_type": "keepalive"})
            return True
        except Exception as error:
            print(f"Command WebSocket keepalive send failed: pc_name={normalized_pc_name}, error={error}")
            return False

    async def record_acknowledgement(self, raw_acknowledgement: dict[str, object]) -> None:
        acknowledgement = CommandAcknowledgement.model_validate(raw_acknowledgement)
        acknowledgement_queue = self.pending_acknowledgements.get(
            acknowledgement.command_id
        )

        if acknowledgement_queue is not None:
            await acknowledgement_queue.put(acknowledgement)
        else:
            # Nothing is awaiting this command_id anymore (already timed out,
            # already completed, or it belongs to a stale/duplicate message).
            # Log it instead of silently dropping it so a mismatch is visible.
            print(
                "Command WebSocket acknowledgement had no pending waiter: "
                f"command_id={acknowledgement.command_id}, status={acknowledgement.status}, "
                f"message={acknowledgement.message}"
            )

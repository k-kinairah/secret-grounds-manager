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

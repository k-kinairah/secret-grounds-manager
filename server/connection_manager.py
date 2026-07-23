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

    async def connect(self, pc_name: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections[pc_name] = websocket

    def disconnect(self, pc_name: str, websocket: WebSocket) -> None:
        if self.active_connections.get(pc_name) is websocket:
            self.active_connections.pop(pc_name, None)

    def is_connected(self, pc_name: str) -> bool:
        return pc_name in self.active_connections

    async def send_command(
        self,
        pc_name: str,
        command: ClientCommand,
    ) -> list[CommandAcknowledgement]:
        acknowledgement_queue: asyncio.Queue[CommandAcknowledgement] = asyncio.Queue()
        self.pending_acknowledgements[command.command_id] = acknowledgement_queue

        websocket = self.active_connections[pc_name]
        await websocket.send_json(command.model_dump(mode="json"))

        acknowledgements: list[CommandAcknowledgement] = []
        try:
            while len(acknowledgements) < 2:
                acknowledgement = await acknowledgement_queue.get()
                acknowledgements.append(acknowledgement)

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

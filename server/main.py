import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from server.connection_manager import ClientConnectionManager
from server.database import initialize_database
from server.schemas import (
    AddTimeRequest,
    ClientEvent,
    ClientCommand,
    PCHeartbeat,
    PCDashboardRecord,
    PCRecord,
    PCRegistration,
    PCRegistrationResponse,
    PCSessionStateResponse,
    RateProfile,
    RateProfileCreate,
    RateProfileUpdate,
    RateStatusUpdate,
    SessionCommandResponse,
    SessionRecord,
    SessionStateSummary,
    StartSessionRequest,
    TestCommandRequest,
    TestCommandResponse,
)
from server.session_repository import SessionRepository
from server.session_service import SessionService
from server.rate_repository import RateRepository
from server.time_utils import current_utc_time


pc_registry: dict[str, PCRecord] = {}
connection_manager = ClientConnectionManager()
session_repository = SessionRepository()
rate_repository = RateRepository()
session_service = SessionService(
    repository=session_repository,
    rate_repository=rate_repository,
    connection_manager=connection_manager,
    pc_registry=pc_registry,
    command_timeout_seconds=5,
)
OFFLINE_AFTER_SECONDS = 15
MONITOR_INTERVAL_SECONDS = 5
COMMAND_ACK_TIMEOUT_SECONDS = 5
DEFAULT_CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"


def get_allowed_cors_origins() -> list[str]:
    raw_origins = os.getenv("SECRET_GROUNDS_CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    return [
        origin.strip()
        for origin in raw_origins.split(",")
        if origin.strip()
    ]


def clean_pc_payload(pc_name: str, ip_address: str) -> tuple[str, str]:
    cleaned_pc_name = pc_name.strip()
    cleaned_ip_address = ip_address.strip()

    if not cleaned_pc_name or not cleaned_ip_address:
        raise HTTPException(
            status_code=400,
            detail="pc_name and ip_address must not be blank",
        )

    return cleaned_pc_name, cleaned_ip_address


def touch_pc(pc_name: str, ip_address: str) -> PCRecord:
    # Registry writes live in one helper so registration and heartbeat stay in sync.
    registered_pc = PCRecord(
        pc_name=pc_name,
        ip_address=ip_address,
        status="online",
        last_seen=current_utc_time(),
    )
    pc_registry[pc_name] = registered_pc
    return registered_pc


def mark_stale_pcs_offline() -> None:
    now = current_utc_time()

    for pc_name, pc in list(pc_registry.items()):
        if pc.status == "offline":
            continue

        if (now - pc.last_seen).total_seconds() > OFFLINE_AFTER_SECONDS:
            pc_registry[pc_name] = PCRecord(
                pc_name=pc.pc_name,
                ip_address=pc.ip_address,
                status="offline",
                last_seen=pc.last_seen,
            )


async def monitor_pc_statuses() -> None:
    # The background monitor owns automatic offline transitions for in-memory PCs.
    while True:
        mark_stale_pcs_offline()
        await asyncio.sleep(MONITOR_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    monitor_task = asyncio.create_task(monitor_pc_statuses())

    try:
        yield
    finally:
        monitor_task.cancel()


app = FastAPI(
    title="Secret Grounds Management System",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/")
def home() -> dict[str, str]:
    return {
        "message": "Welcome to Secret Grounds Management System!"
    }


@app.post("/register", response_model=PCRegistrationResponse)
def register_pc(pc: PCRegistration) -> PCRegistrationResponse:
    pc_name, ip_address = clean_pc_payload(pc.pc_name, pc.ip_address)
    registered_pc = touch_pc(pc_name, ip_address)

    return PCRegistrationResponse(
        message=f"{pc_name} registered successfully",
        pc_name=registered_pc.pc_name,
        ip_address=registered_pc.ip_address,
        status=registered_pc.status,
        last_seen=registered_pc.last_seen,
    )


@app.post("/heartbeat", response_model=PCRecord)
def heartbeat(pc: PCHeartbeat) -> PCRecord:
    pc_name, ip_address = clean_pc_payload(pc.pc_name, pc.ip_address)

    if pc_name not in pc_registry:
        raise HTTPException(
            status_code=404,
            detail=f"{pc_name} is not registered",
        )

    return touch_pc(pc_name, ip_address)


@app.websocket("/ws/{pc_name}")
async def client_websocket(websocket: WebSocket, pc_name: str) -> None:
    cleaned_pc_name = pc_name.strip()

    if not cleaned_pc_name or cleaned_pc_name not in pc_registry:
        await websocket.close(code=1008)
        return

    await connection_manager.connect(cleaned_pc_name, websocket)

    try:
        # The WebSocket endpoint owns all incoming acknowledgement reads, which
        # lets FastAPI detect disconnects and keeps HTTP command routes simple.
        while True:
            message = await websocket.receive_json()
            if "command_id" in message:
                await connection_manager.record_acknowledgement(message)
            elif message.get("event_type") == "session_finished":
                client_event = ClientEvent.model_validate(message)
                recorded = session_service.complete_from_client(
                    client_event.session_id,
                    client_event.message,
                )
                await websocket.send_json(
                    {
                        "event_type": "session_completion_recorded",
                        "session_id": client_event.session_id,
                        "recorded": recorded,
                        "server_time": current_utc_time().isoformat(),
                    }
                )
    except WebSocketDisconnect:
        pass
    finally:
        connection_manager.disconnect(cleaned_pc_name, websocket)


@app.get("/pcs", response_model=list[PCDashboardRecord])
def get_registered_pcs() -> list[PCDashboardRecord]:
    mark_stale_pcs_offline()
    now = current_utc_time()
    dashboard_records: list[PCDashboardRecord] = []

    for _, pc in sorted(pc_registry.items()):
        active_session = session_repository.get_active_session_for_pc(pc.pc_name)
        remaining_seconds: int | None = None
        connection_status = "online" if pc.status == "online" else "offline"
        session_status = "active" if active_session is not None else "idle"

        if connection_status == "offline" and active_session is not None:
            session_status = "unknown"

        if active_session is not None:
            remaining_seconds = max(
                0,
                int((active_session.scheduled_end_at - now).total_seconds()),
            )

        dashboard_records.append(
            PCDashboardRecord(
                pc_name=pc.pc_name,
                ip_address=pc.ip_address,
                connection_status=connection_status,
                last_seen=pc.last_seen,
                session_status=session_status,
                active_session_id=(
                    active_session.session_id if active_session is not None else None
                ),
                scheduled_end_at=(
                    active_session.scheduled_end_at
                    if active_session is not None
                    else None
                ),
                remaining_seconds=remaining_seconds,
            )
        )

    return dashboard_records


@app.get("/pcs/{pc_name}/session-state", response_model=PCSessionStateResponse)
def get_pc_session_state(pc_name: str) -> PCSessionStateResponse:
    cleaned_pc_name = pc_name.strip()
    active_session = session_repository.get_active_session_for_pc(cleaned_pc_name)

    return PCSessionStateResponse(
        pc_name=cleaned_pc_name,
        has_active_session=active_session is not None,
        session=(
            SessionStateSummary(
                session_id=active_session.session_id,
                status=active_session.status,
                scheduled_end_at=active_session.scheduled_end_at,
                total_seconds=active_session.total_seconds,
            )
            if active_session is not None
            else None
        ),
        server_time=current_utc_time(),
    )


@app.post("/commands/test-ping", response_model=TestCommandResponse)
async def send_test_ping_command(
    request: TestCommandRequest,
) -> TestCommandResponse:
    pc_name = request.pc_name.strip()
    mark_stale_pcs_offline()

    if not pc_name:
        raise HTTPException(status_code=400, detail="pc_name must not be blank")

    pc = pc_registry.get(pc_name)
    if pc is None or pc.status == "offline":
        raise HTTPException(
            status_code=409,
            detail=f"{pc_name} is offline",
        )

    if not connection_manager.is_connected(pc_name):
        raise HTTPException(
            status_code=409,
            detail=f"{pc_name} has no active WebSocket connection",
        )

    command = ClientCommand(
        command_id=str(uuid4()),
        type="ping",
        payload={},
        created_at=current_utc_time(),
    )

    try:
        acknowledgements = await session_service.send_required_command(
            pc_name,
            command,
        )
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Command delivery failed: {error}",
        ) from error

    return TestCommandResponse(
        command=command,
        acknowledgements=acknowledgements,
    )


@app.post("/sessions/start", response_model=SessionCommandResponse)
async def start_session(request: StartSessionRequest) -> SessionCommandResponse:
    mark_stale_pcs_offline()
    return await session_service.start_session(
        request.pc_name,
        request.duration_seconds,
        request.rate_id,
    )


@app.post("/sessions/{session_id}/add-time", response_model=SessionCommandResponse)
async def add_time_to_session(
    session_id: str,
    request: AddTimeRequest,
) -> SessionCommandResponse:
    mark_stale_pcs_offline()
    return await session_service.add_time(session_id, request.seconds, request.rate_id)


@app.post("/sessions/{session_id}/end", response_model=SessionCommandResponse)
async def end_session(session_id: str) -> SessionCommandResponse:
    mark_stale_pcs_offline()
    return await session_service.end_session(session_id)


@app.get("/sessions/active", response_model=list[SessionRecord])
def get_active_sessions() -> list[SessionRecord]:
    return session_repository.list_active_sessions()


@app.get("/sessions/history", response_model=list[SessionRecord])
def get_session_history() -> list[SessionRecord]:
    return session_repository.list_history()


@app.get("/sessions/{session_id}", response_model=SessionRecord)
def get_session(session_id: str) -> SessionRecord:
    session = session_repository.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    return session


@app.get("/rates", response_model=list[RateProfile])
def get_rates(include_inactive: bool = False) -> list[RateProfile]:
    return rate_repository.list_rates(include_inactive=include_inactive)


@app.post("/rates", response_model=RateProfile)
def create_rate(request: RateProfileCreate) -> RateProfile:
    return rate_repository.create_rate(request)


@app.put("/rates/{rate_id}", response_model=RateProfile)
def update_rate(rate_id: str, request: RateProfileUpdate) -> RateProfile:
    rate = rate_repository.update_rate(rate_id, request)
    if rate is None:
        raise HTTPException(status_code=404, detail="Rate not found")
    return rate


@app.patch("/rates/{rate_id}/status", response_model=RateProfile)
def update_rate_status(rate_id: str, request: RateStatusUpdate) -> RateProfile:
    rate = rate_repository.set_active(rate_id, request.is_active)
    if rate is None:
        raise HTTPException(status_code=404, detail="Rate not found")
    return rate

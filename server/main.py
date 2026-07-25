import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import uuid4

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from server.connection_manager import ClientConnectionManager
from server.database import initialize_database
from server.schemas import (
    AddTimeRequest,
    BillingSummary,
    CalculatedTimeResponse,
    CalculateTimeRequest,
    ClientEvent,
    ClientCommand,
    ForceEndSessionResponse,
    PCHeartbeat,
    PCDashboardRecord,
    PCRecord,
    PCRegistration,
    PCRegistrationResponse,
    PCSessionStateResponse,
    PaymentCreateRequest,
    LoginRequest,
    PasswordResetRequest,
    PrepaidAddTimeRequest,
    PrepaidStartSessionRequest,
    OpenTimeStartRequest,
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
    VoidPaymentRequest,
    UserAdminRecord,
    UserCreateRequest,
    UserPublic,
    UserStatusUpdate,
    UserUpdateRequest,
)
from server.auth import (
    AUTH_COOKIE_NAME,
    auth_session_repository,
    authenticate_login,
    clear_auth_cookie,
    get_actor_user_id,
    get_current_user,
    require_owner,
    require_role,
    set_auth_cookie,
    to_public_user,
    user_repository,
)
from server.billing_service import BillingService
from server.payment_repository import PaymentRepository
from server.rate_calculator import calculate_metered_time
from server.session_repository import SessionRepository
from server.session_service import SessionService
from server.rate_repository import RateRepository
from server.time_utils import current_utc_time


pc_registry: dict[str, PCRecord] = {}
recovery_flags: dict[str, dict[str, str]] = {}
connection_manager = ClientConnectionManager()
session_repository = SessionRepository()
payment_repository = PaymentRepository()
billing_service = BillingService(session_repository, payment_repository)
rate_repository = RateRepository()
session_service = SessionService(
    repository=session_repository,
    rate_repository=rate_repository,
    connection_manager=connection_manager,
    pc_registry=pc_registry,
    command_timeout_seconds=5,
    billing_service=billing_service,
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
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH"],
    allow_headers=["Content-Type"],
)


@app.get("/")
def home() -> dict[str, str]:
    return {
        "message": "Welcome to Secret Grounds Management System!"
    }


@app.post("/auth/login", response_model=UserPublic)
def login(request: LoginRequest, response: Response) -> UserPublic:
    user = authenticate_login(request)
    token, _ = auth_session_repository.create_session(user.user_id, int(os.getenv("SECRET_GROUNDS_AUTH_SECONDS", str(8 * 60 * 60))))
    set_auth_cookie(response, token)
    return to_public_user(user)


@app.post("/auth/logout")
def logout(
    response: Response,
    sg_session: str | None = Cookie(default=None, alias=AUTH_COOKIE_NAME),
) -> dict[str, str]:
    if sg_session:
        auth_session_repository.revoke_token(sg_session)
    clear_auth_cookie(response)
    return {"message": "Logged out"}


@app.get("/auth/me", response_model=UserPublic)
def get_me(current_user=Depends(get_current_user)) -> UserPublic:
    return to_public_user(current_user)


@app.get("/users", response_model=list[UserAdminRecord])
def list_users(_: object = Depends(require_owner)) -> list[UserAdminRecord]:
    return user_repository.list_users()


@app.post("/users", response_model=UserAdminRecord)
def create_user(
    request: UserCreateRequest,
    _: object = Depends(require_owner),
) -> UserAdminRecord:
    return user_repository.create_user(request)


@app.put("/users/{user_id}", response_model=UserAdminRecord)
def update_user(
    user_id: str,
    request: UserUpdateRequest,
    _: object = Depends(require_owner),
) -> UserAdminRecord:
    user = user_repository.update_user(user_id, request)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.patch("/users/{user_id}/status", response_model=UserAdminRecord)
def update_user_status(
    user_id: str,
    request: UserStatusUpdate,
    _: object = Depends(require_owner),
) -> UserAdminRecord:
    user = user_repository.set_active(user_id, request)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.post("/users/{user_id}/reset-password", response_model=UserAdminRecord)
def reset_user_password(
    user_id: str,
    request: PasswordResetRequest,
    _: object = Depends(require_owner),
) -> UserAdminRecord:
    user = user_repository.reset_password(user_id, request)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


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
                recovery_flags.pop(cleaned_pc_name, None)
                await websocket.send_json(
                    {
                        "event_type": "session_completion_recorded",
                        "session_id": client_event.session_id,
                        "recorded": recorded,
                        "server_time": current_utc_time().isoformat(),
                    }
                )
            elif message.get("event_type") == "reconciliation_unresolved":
                client_event = ClientEvent.model_validate(message)
                recovery_flags[cleaned_pc_name] = {
                    "session_id": client_event.session_id,
                    "reason": client_event.reason or "reconciliation_unresolved",
                }
    except WebSocketDisconnect:
        pass
    finally:
        connection_manager.disconnect(cleaned_pc_name, websocket)


@app.get("/pcs", response_model=list[PCDashboardRecord])
def get_registered_pcs(
    _: object = Depends(require_role("cashier")),
) -> list[PCDashboardRecord]:
    mark_stale_pcs_offline()
    now = current_utc_time()
    dashboard_records: list[PCDashboardRecord] = []

    for _, pc in sorted(pc_registry.items()):
        active_session = session_repository.get_active_session_for_pc(pc.pc_name)
        recovery_flag = recovery_flags.get(pc.pc_name)
        remaining_seconds: int | None = None
        connection_status = "online" if pc.status == "online" else "offline"
        session_status = (
            active_session.status
            if active_session is not None
            and active_session.status in {"active", "ending", "settlement_pending"}
            else "idle"
        )

        if (
            connection_status == "offline"
            and active_session is not None
            and active_session.status in {"active", "ending"}
        ):
            session_status = "unknown"

        if active_session is not None:
            remaining_seconds = max(
                0,
                int((active_session.scheduled_end_at - now).total_seconds()),
            )
            billing = billing_service.summarize_session(active_session)
            display_session = billing.session
        else:
            billing = None
            display_session = None

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
                started_at=(
                    active_session.started_at if active_session is not None else None
                ),
                scheduled_end_at=(
                    active_session.scheduled_end_at
                    if active_session is not None
                    else None
                ),
                remaining_seconds=remaining_seconds,
                rate_name_snapshot=(
                    display_session.rate_name_snapshot if display_session is not None else None
                ),
                base_amount=(
                    display_session.base_amount
                    if display_session is not None
                    else 0
                ),
                additional_amount=(
                    display_session.additional_amount
                    if display_session is not None
                    else 0
                ),
                total_amount=(
                    display_session.total_amount
                    if display_session is not None
                    else 0
                ),
                total_paid=(billing.total_paid if billing is not None else 0),
                balance_due=(billing.balance_due if billing is not None else 0),
                payment_status=(
                    billing.payment_status if billing is not None else "unpaid"
                ),
                session_mode=(
                    display_session.session_mode if display_session is not None else "prepaid"
                ),
                actual_elapsed_seconds=(
                    display_session.actual_elapsed_seconds
                    if display_session is not None
                    else None
                ),
                billed_seconds=(
                    display_session.billed_seconds if display_session is not None else None
                ),
                final_computed_amount=(
                    display_session.final_computed_amount
                    if display_session is not None
                    else None
                ),
                metered_rate_price_snapshot=(
                    display_session.metered_rate_price_snapshot
                    if display_session is not None
                    else None
                ),
                metered_rate_duration_seconds_snapshot=(
                    display_session.metered_rate_duration_seconds_snapshot
                    if display_session is not None
                    else None
                ),
                estimated_total_amount=(
                    display_session.estimated_total_amount
                    if display_session is not None
                    else None
                ),
                estimated_balance_due=(
                    display_session.estimated_balance_due
                    if display_session is not None
                    else None
                ),
                settlement_pending=(
                    display_session.settlement_pending
                    if display_session is not None
                    else False
                ),
                recovery_required=(
                    active_session is not None
                    and recovery_flag is not None
                    and recovery_flag.get("session_id") == active_session.session_id
                ),
                recovery_reason=(
                    recovery_flag.get("reason")
                    if active_session is not None
                    and recovery_flag is not None
                    and recovery_flag.get("session_id") == active_session.session_id
                    else None
                ),
            )
        )

    return dashboard_records


@app.get("/pcs/{pc_name}/session-state", response_model=PCSessionStateResponse)
def get_pc_session_state(pc_name: str) -> PCSessionStateResponse:
    cleaned_pc_name = pc_name.strip()
    active_session = session_repository.get_active_session_for_pc(cleaned_pc_name)
    has_client_active_session = (
        active_session is not None
        and active_session.status in {"active", "ending"}
    )

    return PCSessionStateResponse(
        pc_name=cleaned_pc_name,
        has_active_session=has_client_active_session,
        session=(
            SessionStateSummary(
                session_id=active_session.session_id,
                status=active_session.status,
                scheduled_end_at=active_session.scheduled_end_at,
                total_seconds=active_session.total_seconds,
                session_mode=active_session.session_mode,
            )
            if has_client_active_session and active_session is not None
            else None
        ),
        server_time=current_utc_time(),
    )


@app.post("/commands/test-ping", response_model=TestCommandResponse)
async def send_test_ping_command(
    request: TestCommandRequest,
    _: object = Depends(require_role("admin")),
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
async def start_session(
    request: StartSessionRequest,
    current_user: object = Depends(require_role("cashier")),
) -> SessionCommandResponse:
    mark_stale_pcs_offline()
    response = await session_service.start_session(
        request.pc_name,
        request.duration_seconds,
        request.rate_id,
        request.amount,
        get_actor_user_id(current_user),
    )
    recovery_flags.pop(response.session.pc_name, None)
    return response


@app.post("/sessions/start-prepaid", response_model=SessionCommandResponse)
async def start_prepaid_session(
    request: PrepaidStartSessionRequest,
    current_user: object = Depends(require_role("cashier")),
) -> SessionCommandResponse:
    mark_stale_pcs_offline()
    response = await session_service.start_prepaid_session(
        request.pc_name,
        request.payment,
        request.duration_seconds,
        request.rate_id,
        request.amount,
        get_actor_user_id(current_user),
    )
    recovery_flags.pop(response.session.pc_name, None)
    return response


@app.post("/sessions/start-open-time", response_model=SessionCommandResponse)
async def start_open_time_session(
    request: OpenTimeStartRequest,
    current_user: object = Depends(require_role("cashier")),
) -> SessionCommandResponse:
    mark_stale_pcs_offline()
    response = await session_service.start_open_time_session(
        request.pc_name,
        get_actor_user_id(current_user),
    )
    recovery_flags.pop(response.session.pc_name, None)
    return response


@app.post("/sessions/{session_id}/add-time", response_model=SessionCommandResponse)
async def add_time_to_session(
    session_id: str,
    request: AddTimeRequest,
    current_user: object = Depends(require_role("cashier")),
) -> SessionCommandResponse:
    mark_stale_pcs_offline()
    return await session_service.add_time(
        session_id,
        request.seconds,
        request.rate_id,
        request.amount,
        get_actor_user_id(current_user),
    )


@app.post("/sessions/{session_id}/add-time-prepaid", response_model=SessionCommandResponse)
async def add_prepaid_time_to_session(
    session_id: str,
    request: PrepaidAddTimeRequest,
    current_user: object = Depends(require_role("cashier")),
) -> SessionCommandResponse:
    mark_stale_pcs_offline()
    return await session_service.add_prepaid_time(
        session_id,
        request.payment,
        request.seconds,
        request.rate_id,
        request.amount,
        get_actor_user_id(current_user),
    )


@app.post("/sessions/{session_id}/end", response_model=SessionCommandResponse)
async def end_session(
    session_id: str,
    current_user: object = Depends(require_role("cashier")),
) -> SessionCommandResponse:
    mark_stale_pcs_offline()
    response = await session_service.end_session(
        session_id,
        get_actor_user_id(current_user),
    )
    recovery_flags.pop(response.session.pc_name, None)
    return response


@app.post("/sessions/{session_id}/force-end", response_model=ForceEndSessionResponse)
async def force_end_session(
    session_id: str,
    current_user: object = Depends(require_role("admin")),
) -> ForceEndSessionResponse:
    mark_stale_pcs_offline()
    response = await session_service.force_end_session(
        session_id,
        get_actor_user_id(current_user),
    )
    recovery_flags.pop(response.session.pc_name, None)
    return response


@app.get("/sessions/active", response_model=list[SessionRecord])
def get_active_sessions(
    _: object = Depends(require_role("cashier")),
) -> list[SessionRecord]:
    return [
        billing_service.enrich_session(session)
        for session in session_repository.list_active_sessions()
    ]


@app.get("/sessions/history", response_model=list[SessionRecord])
def get_session_history(
    _: object = Depends(require_role("cashier")),
) -> list[SessionRecord]:
    return [
        billing_service.enrich_session(session)
        for session in session_repository.list_history()
    ]


@app.get("/sessions/{session_id}", response_model=SessionRecord)
def get_session(
    session_id: str,
    _: object = Depends(require_role("cashier")),
) -> SessionRecord:
    session = session_repository.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    return billing_service.enrich_session(session)


@app.get("/sessions/{session_id}/billing", response_model=BillingSummary)
def get_session_billing(
    session_id: str,
    _: object = Depends(require_role("cashier")),
) -> BillingSummary:
    return billing_service.get_billing(session_id)


@app.post("/sessions/{session_id}/payments", response_model=BillingSummary)
def record_payment(
    session_id: str,
    request: PaymentCreateRequest,
    current_user: object = Depends(require_role("cashier")),
) -> BillingSummary:
    return billing_service.record_payment(
        session_id,
        request,
        actor_user_id=get_actor_user_id(current_user),
    )


@app.post("/payments/{payment_id}/void", response_model=BillingSummary)
def void_payment(
    payment_id: str,
    request: VoidPaymentRequest,
    current_user: object = Depends(require_role("admin")),
) -> BillingSummary:
    return billing_service.void_payment(
        payment_id,
        request.reason,
        get_actor_user_id(current_user),
    )


@app.get("/rates", response_model=list[RateProfile])
def get_rates(
    include_inactive: bool = False,
    _: object = Depends(require_role("cashier")),
) -> list[RateProfile]:
    return rate_repository.list_rates(include_inactive=include_inactive)


@app.get("/rates/metered", response_model=RateProfile)
def get_metered_rate(
    _: object = Depends(require_role("cashier")),
) -> RateProfile:
    rate = rate_repository.get_active_metered_rate()
    if rate is None:
        raise HTTPException(status_code=404, detail="Active metered rate is missing")
    return rate


@app.post("/rates/calculate-time", response_model=CalculatedTimeResponse)
def calculate_time(
    request: CalculateTimeRequest,
    _: object = Depends(require_role("cashier")),
) -> CalculatedTimeResponse:
    rate = rate_repository.get_active_metered_rate()
    if rate is None:
        raise HTTPException(status_code=409, detail="Active metered rate is missing")
    return calculate_metered_time(request.amount, rate)


@app.post("/rates", response_model=RateProfile)
def create_rate(
    request: RateProfileCreate,
    current_user: object = Depends(require_role("admin")),
) -> RateProfile:
    return rate_repository.create_rate(request, get_actor_user_id(current_user))


@app.put("/rates/{rate_id}", response_model=RateProfile)
def update_rate(
    rate_id: str,
    request: RateProfileUpdate,
    current_user: object = Depends(require_role("admin")),
) -> RateProfile:
    rate = rate_repository.update_rate(rate_id, request, get_actor_user_id(current_user))
    if rate is None:
        raise HTTPException(status_code=404, detail="Rate not found")
    return rate


@app.patch("/rates/{rate_id}/status", response_model=RateProfile)
def update_rate_status(
    rate_id: str,
    request: RateStatusUpdate,
    current_user: object = Depends(require_role("admin")),
) -> RateProfile:
    rate = rate_repository.set_active(
        rate_id,
        request.is_active,
        get_actor_user_id(current_user),
    )
    if rate is None:
        raise HTTPException(status_code=404, detail="Rate not found")
    return rate

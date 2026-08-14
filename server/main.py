import asyncio
import csv
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import socket
from datetime import date, datetime, time, timedelta, timezone
from io import StringIO
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import AsyncIterator
from uuid import uuid4

from fastapi import Body, Cookie, Depends, FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from server.connection_manager import ClientConnectionManager
from server.database import get_connection, initialize_database
from server.schemas import (
    AddTimeRequest,
    AdminSettingsResetRequest,
    AdminSettingsResponse,
    AdminSettingsUpdateRequest,
    BillingSummary,
    CashierShiftDetail,
    CashierShiftRecord,
    CalculatedTimeResponse,
    CalculateTimeRequest,
    ClientEvent,
    ClientCommand,
    ClientSafeSettings,
    ClientMemberLoginRequest,
    ClientMemberLoginResponse,
    ClientSessionLockRequest,
    ClientSessionLockResponse,
    ClientSessionUnlockRequest,
    ClientSessionUnlockResponse,
    CommandAcknowledgement,
    BroadcastCommandRequest,
    BroadcastCommandResponse,
    ForceEndSessionResponse,
    AdminOperatorLoginRequest,
    MessageCommandRequest,
    IncomeGroupRecord,
    PCMacAddressUpdate,
    PCLayoutRecord,
    PCLayoutUpdate,
    PCHeartbeat,
    PCDashboardRecord,
    PCRecord,
    PowerCommandRequest,
    PCRegistration,
    PCRegistrationResponse,
    PCSessionStateResponse,
    PaymentCreateRequest,
    LoginRequest,
    ScreenMonitorPCStatus,
    QuickOperatorLoginRequest,
    MemberAddTimeRequest,
    MemberCreateRequest,
    MemberPurchaseRefundRequest,
    MemberPurchaseRefundResponse,
    MemberPurchaseRequest,
    MemberPurchaseResponse,
    MemberPinResetRequest,
    MemberRecord,
    MembershipProductCreate,
    MembershipProductRecord,
    MembershipProductUpdate,
    MemberSessionStartRequest,
    MemberStatusUpdate,
    MemberTimeTransactionRecord,
    MemberUpdateRequest,
    MemberVipPlayablePreview,
    PasswordResetRequest,
    PreparedSessionResponse,
    PreparedSessionStartRequest,
    PreparedSessionActivationResponse,
    OnDutyOperatorRecord,
    OperatorSaleSummary,
    OperatorAccessRecord,
    OperatorActivityRecord,
    OperatorCreateRequest,
    OperatorManagementRecord,
    OperatorPasswordResetRequest,
    OperatorSelectionRequest,
    OperatorUpdateRequest,
    PasswordOperatorLoginRequest,
    PrepaidAddTimeRequest,
    PrepaidStartSessionRequest,
    OpenTimeStartRequest,
    RemoteCommandRecord,
    RemoteCommandResponse,
    RemoteCommandType,
    RateProfile,
    RateProfileCreate,
    RateProfileUpdate,
    RateStatusUpdate,
    ShiftCloseRequest,
    ShiftOpenRequest,
    SessionCommandResponse,
    SessionBalanceSourceProjection,
    SessionRecord,
    SessionStateSummary,
    SalesOutstandingSettlement,
    SalesIncomeGroupBreakdown,
    SalesPCBreakdown,
    SalesPaymentMethodBreakdown,
    SalesPeriod,
    SalesRecentPayment,
    SalesReport,
    SalesSummary,
    SalesTimeseriesPoint,
    SalesTypeBreakdown,
    SalesOperatorBreakdown,
    SalesOperatorCollection,
    SalesOperatorTransaction,
    StartSessionRequest,
    StationAssignRequest,
    StationAssignmentRecord,
    StationResolveRequest,
    StationResolveResponse,
    normalize_mac_address,
    TestCommandRequest,
    TestCommandResponse,
    WakeCommandResponse,
    VoidPaymentRequest,
    UserAdminRecord,
    UserCreateRequest,
    UserPublic,
    UserRecord,
    UserStatusUpdate,
    UserUpdateRequest,
)
from server.admin_settings_service import AdminSettingsService
from server.balance_conversion import split_vip_session
from server.auth import (
    AUTH_COOKIE_NAME,
    auth_session_repository,
    authenticate_admin_operator,
    authenticate_login,
    authenticate_password_operator,
    authenticate_quick_operator,
    clear_auth_cookie,
    get_actor_user_id,
    get_current_user,
    list_family_operator_access,
    require_owner,
    require_role,
    set_auth_cookie,
    to_public_user,
    user_repository,
)
from server.billing_service import BillingService
from server.member_repository import MemberRepository
from server.operator_repository import OperatorRepository
from server.payment_repository import PaymentRepository
from server.income_attribution import resolve_pending_payments_for_configured_pc
from server.remote_command_repository import PCMetadataRepository, RemoteCommandRepository
from server.pc_layout_repository import IncomeGroupRepository, OperatorStateRepository, PCLayoutRepository
from server.prepared_session_repository import PreparedSessionRepository
from server.session_repository import SessionRepository
from server.session_service import SessionService
from server.shift_repository import ShiftRepository
from server.rate_repository import RateRepository
from server.screen_monitor_service import ScreenMonitorService, sniff_image_content_type
from server.settings import SCREEN_MONITORING_ENABLED
from server.station_repository import StationAssignmentRepository
from server.time_utils import current_utc_time
from server.system_service import SystemService
from server.version import APP_VERSION


logger = logging.getLogger(__name__)

pc_registry: dict[str, PCRecord] = {}
recovery_flags: dict[str, dict[str, str]] = {}
connection_manager = ClientConnectionManager()
screen_monitor_service = ScreenMonitorService()
session_repository = SessionRepository()
payment_repository = PaymentRepository()
shift_repository = ShiftRepository()
admin_settings_service = AdminSettingsService()
system_service = SystemService()
SERVER_STARTED_AT = current_utc_time()


def require_admin_operator(current_user=Depends(get_current_user)):
    if current_user.username.casefold() != "admin":
        raise HTTPException(status_code=403, detail="Admin operator access required")
    return current_user
member_repository = MemberRepository(payment_repository, shift_repository)
remote_command_repository = RemoteCommandRepository()
pc_metadata_repository = PCMetadataRepository()
pc_layout_repository = PCLayoutRepository()
station_repository = StationAssignmentRepository()
income_group_repository = IncomeGroupRepository()
operator_state_repository = OperatorStateRepository()
operator_repository = OperatorRepository()
prepared_session_repository = PreparedSessionRepository()
billing_service = BillingService(session_repository, payment_repository, shift_repository, user_repository)
rate_repository = RateRepository()
session_service = SessionService(
    repository=session_repository,
    rate_repository=rate_repository,
    connection_manager=connection_manager,
    pc_registry=pc_registry,
    command_timeout_seconds=5,
    billing_service=billing_service,
    member_repository=member_repository,
    prepared_session_repository=prepared_session_repository,
    pc_layout_repository=pc_layout_repository,
    payment_repository=payment_repository,
    income_group_repository=income_group_repository,
)
OFFLINE_AFTER_SECONDS = 15
MONITOR_INTERVAL_SECONDS = 5
COMMAND_ACK_TIMEOUT_SECONDS = 5
DEVELOPMENT_DASHBOARD_ORIGIN = "http://localhost:3000"
PRODUCTION_DASHBOARD_ORIGIN = "http://127.0.0.1:3001"


def configure_file_logging() -> None:
    log_path = os.getenv("SECRET_GROUNDS_SERVER_LOG")
    if not log_path:
        return

    resolved_path = os.path.abspath(os.path.expanduser(log_path))
    os.makedirs(os.path.dirname(resolved_path), exist_ok=True)
    handler = RotatingFileHandler(
        resolved_path,
        maxBytes=int(os.getenv("SECRET_GROUNDS_LOG_MAX_BYTES", "1048576")),
        backupCount=int(os.getenv("SECRET_GROUNDS_LOG_BACKUP_COUNT", "5")),
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)
    logging.getLogger("uvicorn").addHandler(handler)
    logging.getLogger("uvicorn.error").addHandler(handler)
    logging.info("Secret Grounds %s backend logging initialized.", APP_VERSION)


def get_allowed_cors_origins() -> list[str]:
    default_origin = (
        PRODUCTION_DASHBOARD_ORIGIN
        if os.getenv("SECRET_GROUNDS_APP_MODE", "development").strip().lower() == "production"
        else DEVELOPMENT_DASHBOARD_ORIGIN
    )
    raw_origins = os.getenv("SECRET_GROUNDS_CORS_ORIGINS", default_origin)
    return [
        origin.strip()
        for origin in raw_origins.split(",")
        if origin.strip() and origin.strip() != "*"
    ]


def issue_operator_session(user: object, response: Response, selected_by_user_id: str | None = None) -> UserPublic:
    public_user = to_public_user(user)
    token, _ = auth_session_repository.create_session(
        public_user.user_id,
        int(os.getenv("SECRET_GROUNDS_AUTH_SECONDS", str(8 * 60 * 60))),
    )
    set_auth_cookie(response, token)
    operator_state_repository.set_current(
        public_user.user_id,
        selected_by_user_id or public_user.user_id,
    )
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO operator_audit_events (
                event_id,
                event_type,
                operator_user_id,
                operator_display_name,
                selected_by_user_id,
                created_at
            )
            VALUES (?, 'operator_changed', ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                public_user.user_id,
                public_user.display_name,
                selected_by_user_id or public_user.user_id,
                current_utc_time().isoformat(),
            ),
        )
    print(f"Operator changed: {public_user.display_name}")
    return public_user


def clean_pc_payload(pc_name: str, ip_address: str) -> tuple[str, str]:
    cleaned_pc_name = pc_name.strip()
    cleaned_ip_address = ip_address.strip()

    if not cleaned_pc_name or not cleaned_ip_address:
        raise HTTPException(
            status_code=400,
            detail="pc_name and ip_address must not be blank",
        )

    return cleaned_pc_name, cleaned_ip_address


def upsert_client_display_name(pc_name: str, display_name: str | None, mac_address: str | None = None) -> None:
    cleaned_display_name = " ".join((display_name or "").split())
    if not cleaned_display_name:
        return

    existing_layout = pc_layout_repository.get_layout(pc_name)
    pc_layout_repository.upsert_layout(
        pc_name,
        PCLayoutUpdate(
            display_name=cleaned_display_name,
            area_label=existing_layout.area_label if existing_layout is not None else "CLIENT",
            layout_order=existing_layout.layout_order if existing_layout is not None else 10_000,
            is_vip=existing_layout.is_vip if existing_layout is not None else False,
            is_enabled=existing_layout.is_enabled if existing_layout is not None else True,
            mac_address=mac_address or (existing_layout.mac_address if existing_layout is not None else None),
            # Register/heartbeat only ever touch client/runtime info (display
            # name, MAC). The owner-controlled revenue-owner assignment must
            # never be reset by a routine client check-in -- carry it forward
            # unchanged, the same way every other layout field already is.
            income_group_id=existing_layout.income_group_id if existing_layout is not None else None,
        ),
    )


def reject_duplicate_station_id(pc_name: str, mac_address: str | None) -> None:
    existing_pc = pc_registry.get(pc_name)
    if existing_pc is None or existing_pc.status != "online":
        return
    existing_mac = pc_metadata_repository.get_mac_address(pc_name)
    if existing_mac and mac_address and existing_mac.casefold() != mac_address.casefold():
        raise HTTPException(
            status_code=409,
            detail=(
                f"Station ID {pc_name} is already online from another client. "
                "Choose a unique station ID for this installation."
            ),
        )


def touch_pc(
    pc_name: str,
    ip_address: str,
    display_name: str | None = None,
    mac_address: str | None = None,
) -> PCRecord:
    # Registry writes live in one helper so registration and heartbeat stay in sync.
    upsert_client_display_name(pc_name, display_name, mac_address)
    registered_pc = PCRecord(
        pc_name=pc_name,
        ip_address=ip_address,
        status="online",
        last_seen=current_utc_time(),
        mac_address=pc_metadata_repository.get_mac_address(pc_name),
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
                mac_address=pc.mac_address,
            )
            screen_monitor_service.clear_frame(pc_name)


async def monitor_pc_statuses() -> None:
    # The background monitor owns automatic offline transitions for in-memory PCs.
    while True:
        mark_stale_pcs_offline()
        await asyncio.sleep(MONITOR_INTERVAL_SECONDS)


async def run_scheduled_backups() -> None:
    while True:
        now = datetime.now().astimezone()
        next_run = now.replace(hour=2, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep(max(60, int((next_run - now).total_seconds())))
        try:
            system_service.create_backup()
        except Exception as error:
            print(f"Automatic backup failed: {error}")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_file_logging()
    logging.info("Secret Grounds %s backend startup at %s.", APP_VERSION, SERVER_STARTED_AT.isoformat())
    initialize_database()
    monitor_task = asyncio.create_task(monitor_pc_statuses())
    backup_task = asyncio.create_task(run_scheduled_backups())

    try:
        yield
    finally:
        logging.info("Secret Grounds backend shutdown requested.")
        monitor_task.cancel()
        backup_task.cancel()


app = FastAPI(
    title="Secret Grounds Management System",
    version=APP_VERSION,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.get("/")
def home() -> dict[str, str]:
    return {
        "message": "Welcome to Secret Grounds Management System!"
    }


@app.get("/healthz")
def readiness_health() -> dict[str, str]:
    return {
        "status": "ok",
        "version": app.version,
        "started_at": SERVER_STARTED_AT.isoformat(),
    }


@app.post("/auth/login", response_model=UserPublic)
def login(request: LoginRequest, response: Response) -> UserPublic:
    user = authenticate_login(request)
    return issue_operator_session(user, response)


@app.get("/auth/operators", response_model=list[OperatorAccessRecord])
def list_login_operators() -> list[OperatorAccessRecord]:
    return list_family_operator_access()


@app.post("/auth/operator/quick", response_model=UserPublic)
def quick_operator_login(
    request: QuickOperatorLoginRequest,
    response: Response,
) -> UserPublic:
    user = authenticate_quick_operator(request.operator_id)
    return issue_operator_session(user, response)


@app.post("/auth/operator/admin", response_model=UserPublic)
def admin_operator_login(
    request: AdminOperatorLoginRequest,
    response: Response,
) -> UserPublic:
    user = authenticate_admin_operator(request.password)
    return issue_operator_session(user, response)


@app.post("/auth/operator/password", response_model=UserPublic)
def password_operator_login(
    request: PasswordOperatorLoginRequest,
    response: Response,
) -> UserPublic:
    user = authenticate_password_operator(request.operator_id, request.password)
    return issue_operator_session(user, response)


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


@app.get("/me", response_model=UserPublic)
def get_me_alias(current_user=Depends(get_current_user)) -> UserPublic:
    return to_public_user(current_user)


@app.get("/settings", response_model=AdminSettingsResponse)
def get_admin_settings(
    _: object = Depends(require_admin_operator),
) -> AdminSettingsResponse:
    return admin_settings_service.get_response()


@app.put("/settings", response_model=AdminSettingsResponse)
def update_admin_settings(
    request: AdminSettingsUpdateRequest,
    current_user=Depends(require_admin_operator),
) -> AdminSettingsResponse:
    return admin_settings_service.update_settings(request, current_user.user_id)


@app.post("/settings/reset", response_model=AdminSettingsResponse)
def reset_admin_settings(
    request: AdminSettingsResetRequest,
    current_user=Depends(require_admin_operator),
) -> AdminSettingsResponse:
    return admin_settings_service.reset_settings(request, current_user.user_id)


@app.get("/system/health")
def get_system_health(_: object = Depends(require_admin_operator)) -> dict[str, object]:
    mark_stale_pcs_offline()
    return system_service.get_health(
        app_version=app.version,
        server_started_at=SERVER_STARTED_AT,
        pc_registry=pc_registry,
        active_websockets=connection_manager.active_pc_names(),
    )


@app.get("/system/database")
def get_system_database(_: object = Depends(require_admin_operator)) -> dict[str, object]:
    return system_service.get_database_health()


@app.get("/system/backups")
def get_system_backups(_: object = Depends(require_admin_operator)) -> list[dict[str, object]]:
    return system_service.list_backups()


@app.post("/system/backup")
def create_system_backup(_: object = Depends(require_admin_operator)) -> dict[str, object]:
    return system_service.create_backup()


@app.post("/system/restore")
def restore_system_backup(
    payload: dict[str, object] = Body(...),
    _: object = Depends(require_admin_operator),
) -> dict[str, object]:
    filename = str(payload.get("filename") or "")
    confirm = bool(payload.get("confirm"))
    return system_service.restore_backup(filename, confirm)


@app.delete("/system/backups/{filename}")
def delete_system_backup(
    filename: str,
    confirm: bool = Query(False),
    _: object = Depends(require_admin_operator),
) -> dict[str, str]:
    return system_service.delete_backup(filename, confirm)


@app.get("/system/backups/{filename}/download")
def download_system_backup(
    filename: str,
    _: object = Depends(require_admin_operator),
) -> FileResponse:
    backup_path = system_service.resolve_backup_path(filename)
    return FileResponse(
        path=backup_path,
        filename=backup_path.name,
        media_type="application/vnd.sqlite3",
    )


@app.get("/system/storage")
def get_system_storage(_: object = Depends(require_admin_operator)) -> dict[str, object]:
    return system_service.get_storage()


@app.get("/system/info")
def get_system_info(_: object = Depends(require_admin_operator)) -> dict[str, object]:
    return system_service.get_info(
        app_version=app.version,
        server_started_at=SERVER_STARTED_AT,
    )


@app.get("/system/logs")
def get_system_logs(_: object = Depends(require_admin_operator)) -> list[dict[str, str]]:
    return system_service.get_logs()


@app.get("/system/clients")
def get_system_clients(_: object = Depends(require_admin_operator)) -> list[dict[str, object]]:
    mark_stale_pcs_offline()
    layouts = {layout.pc_name: layout for layout in pc_layout_repository.list_layouts(include_disabled=True)}
    clients: list[dict[str, object]] = []
    for pc_name, layout in sorted(layouts.items(), key=lambda item: (item[1].layout_order, item[0])):
        pc = pc_registry.get(pc_name)
        active_session = session_repository.get_active_session_for_pc(pc_name)
        connected = pc is not None and pc.status == "online"
        clients.append(
            {
                "pc_name": pc_name,
                "display_name": layout.display_name,
                "registered": pc is not None,
                "connected": connected,
                "last_heartbeat": pc.last_seen.isoformat() if pc is not None else None,
                "version": None,
                "current_state": active_session.status if active_session is not None else ("connected" if connected else "disconnected"),
            }
        )
    return clients


@app.get("/client-settings", response_model=ClientSafeSettings)
def get_client_settings() -> ClientSafeSettings:
    return admin_settings_service.get_client_settings()


@app.post("/shifts/open", response_model=CashierShiftDetail)
def open_shift(
    request: ShiftOpenRequest,
    current_user=Depends(require_role("cashier")),
) -> CashierShiftDetail:
    return shift_repository.open_shift(request, current_user)


@app.get("/shifts/current", response_model=CashierShiftDetail | None)
def get_current_shift(
    current_user=Depends(require_role("cashier")),
) -> CashierShiftDetail | None:
    shift = shift_repository.get_open_shift_for_user(current_user.user_id)
    if shift is None:
        return None
    return shift_repository.get_shift(shift.shift_id, current_user)


@app.get("/shifts", response_model=list[CashierShiftRecord])
def list_shifts(
    current_user=Depends(require_role("cashier")),
) -> list[CashierShiftRecord]:
    return shift_repository.list_shifts(current_user)


@app.get("/shifts/{shift_id}", response_model=CashierShiftDetail)
def get_shift(
    shift_id: str,
    current_user=Depends(require_role("cashier")),
) -> CashierShiftDetail:
    detail = shift_repository.get_shift(shift_id, current_user)
    if detail is None:
        raise HTTPException(status_code=404, detail="Shift not found")
    return detail


@app.post("/shifts/{shift_id}/close", response_model=CashierShiftDetail)
def close_shift(
    shift_id: str,
    request: ShiftCloseRequest,
    response: Response,
    current_user=Depends(require_role("cashier")),
) -> CashierShiftDetail:
    # Validate/finalize first -- if this raises, nothing below runs, so a
    # failed close leaves the operator's auth/on-duty state untouched and
    # they can simply retry. Once this line succeeds the shift is
    # permanently closed; everything after is "operator is done working"
    # cleanup that must never be able to put the shift itself back into a
    # half-closed state.
    closed = shift_repository.close_shift(shift_id, request, current_user)
    shift_owner_id = closed.shift.user_id

    try:
        operator_repository.revoke_all_sessions(shift_owner_id)
    except Exception:
        logger.exception(
            "End Shift: failed to revoke auth sessions for operator %s after closing shift %s",
            shift_owner_id,
            shift_id,
        )

    try:
        operator_state_repository.clear_if_current(shift_owner_id)
    except Exception:
        logger.exception(
            "End Shift: failed to clear on-duty operator state for operator %s after closing shift %s",
            shift_owner_id,
            shift_id,
        )

    if current_user.user_id == shift_owner_id:
        # Only clear the cookie belonging to the browser that made this
        # request. An owner/admin force-closing another cashier's shift
        # must never clear their own session -- the shift owner's other
        # sessions are already invalidated server-side above and will be
        # rejected on their next request regardless.
        clear_auth_cookie(response)

    return closed


@app.get("/members", response_model=list[MemberRecord])
def list_members(
    q: str | None = None,
    _: object = Depends(require_role("cashier")),
) -> list[MemberRecord]:
    return member_repository.list_members(q)


@app.get("/members/{member_id}", response_model=MemberRecord)
def get_member(
    member_id: str,
    _: object = Depends(require_role("cashier")),
) -> MemberRecord:
    member = member_repository.get_member(member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


@app.get("/members/{member_id}/vip-preview", response_model=MemberVipPlayablePreview)
def get_member_vip_preview(
    member_id: str,
    pc_name: str,
    duration_seconds: int | None = None,
    _: object = Depends(require_role("cashier")),
) -> MemberVipPlayablePreview:
    """Dashboard-only preview of playable real time for this member on this
    PC. Server remains authoritative at actual session start/add-time --
    this never reserves or mutates anything."""
    member = member_repository.get_member(member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")

    is_vip_station = session_service.is_vip_pc(pc_name)
    if not is_vip_station:
        playable_seconds = (
            member.time_balance_seconds
            if duration_seconds is None
            else min(duration_seconds, member.time_balance_seconds)
        )
        return MemberVipPlayablePreview(
            is_vip_station=False,
            playable_seconds=max(0, playable_seconds),
            vip_seconds=0,
            standard_seconds=0,
            standard_real_seconds_equivalent=0,
        )

    effective, vip_part, standard_part = split_vip_session(
        member.vip_balance_seconds, member.time_balance_seconds, duration_seconds
    )
    return MemberVipPlayablePreview(
        is_vip_station=True,
        playable_seconds=effective,
        vip_seconds=vip_part,
        standard_seconds=standard_part,
        standard_real_seconds_equivalent=standard_part // 2,
    )


@app.get("/sessions/{session_id}/balance-source", response_model=SessionBalanceSourceProjection)
def get_session_balance_source(
    session_id: str,
    _: object = Depends(require_role("cashier")),
) -> SessionBalanceSourceProjection:
    """Dashboard-only read of which balance pool an active member session is
    currently drawing from. Pure projection from persisted reservation +
    session state and current time -- polling this never mutates anything."""
    return session_service.get_balance_source_projection(session_id)


@app.post("/members", response_model=MemberRecord)
def create_member(
    request: MemberCreateRequest,
    current_user=Depends(require_role("cashier")),
) -> MemberRecord:
    return member_repository.create_member(
        request,
        current_user if isinstance(current_user, UserRecord) else None,
    )


@app.put("/members/{member_id}", response_model=MemberRecord)
def update_member(
    member_id: str,
    request: MemberUpdateRequest,
    current_user=Depends(require_role("admin")),
) -> MemberRecord:
    member = member_repository.update_member(
        member_id,
        request,
        current_user if isinstance(current_user, UserRecord) else None,
    )
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


@app.patch("/members/{member_id}/status", response_model=MemberRecord)
def update_member_status(
    member_id: str,
    request: MemberStatusUpdate,
    _: object = Depends(require_role("admin")),
) -> MemberRecord:
    member = member_repository.set_member_status(member_id, request)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


@app.post("/members/{member_id}/pin", response_model=MemberRecord)
def reset_member_pin(
    member_id: str,
    request: MemberPinResetRequest,
    current_user=Depends(require_role("admin")),
) -> MemberRecord:
    member = member_repository.set_member_pin(
        member_id,
        request.pin,
        current_user if isinstance(current_user, UserRecord) else None,
    )
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


@app.get("/members/{member_id}/ledger", response_model=list[MemberTimeTransactionRecord])
def get_member_ledger(
    member_id: str,
    _: object = Depends(require_role("cashier")),
) -> list[MemberTimeTransactionRecord]:
    if member_repository.get_member(member_id) is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return member_repository.list_ledger(member_id)


@app.post("/members/{member_id}/purchase", response_model=MemberPurchaseResponse)
def purchase_member_time(
    member_id: str,
    request: MemberPurchaseRequest,
    current_user=Depends(require_role("cashier")),
) -> MemberPurchaseResponse:
    return member_repository.purchase(member_id, request, current_user)


@app.post("/member-purchases/{payment_id}/refund", response_model=MemberPurchaseRefundResponse)
def refund_member_purchase(
    payment_id: str,
    request: MemberPurchaseRefundRequest,
    current_user=Depends(require_role("admin")),
) -> MemberPurchaseRefundResponse:
    return member_repository.refund_purchase(payment_id, request, current_user)


@app.get("/membership-products", response_model=list[MembershipProductRecord])
def list_membership_products(
    include_inactive: bool = False,
    _: object = Depends(require_role("cashier")),
) -> list[MembershipProductRecord]:
    return member_repository.list_products(include_inactive)


@app.post("/membership-products", response_model=MembershipProductRecord)
def create_membership_product(
    request: MembershipProductCreate,
    _: object = Depends(require_role("admin")),
) -> MembershipProductRecord:
    return member_repository.create_product(request)


@app.put("/membership-products/{product_id}", response_model=MembershipProductRecord)
def update_membership_product(
    product_id: str,
    request: MembershipProductUpdate,
    _: object = Depends(require_role("admin")),
) -> MembershipProductRecord:
    product = member_repository.update_product(product_id, request)
    if product is None:
        raise HTTPException(status_code=404, detail="Membership product not found")
    return product


@app.patch("/membership-products/{product_id}/status", response_model=MembershipProductRecord)
def update_membership_product_status(
    product_id: str,
    request: RateStatusUpdate,
    _: object = Depends(require_role("admin")),
) -> MembershipProductRecord:
    product = member_repository.set_product_active(product_id, request.is_active)
    if product is None:
        raise HTTPException(status_code=404, detail="Membership product not found")
    return product


@app.get("/users", response_model=list[UserAdminRecord])
def list_users(_: object = Depends(require_admin_operator)) -> list[UserAdminRecord]:
    return user_repository.list_users()


@app.post("/users", response_model=UserAdminRecord)
def create_user(
    request: UserCreateRequest,
    _: object = Depends(require_admin_operator),
) -> UserAdminRecord:
    return user_repository.create_user(request)


@app.put("/users/{user_id}", response_model=UserAdminRecord)
def update_user(
    user_id: str,
    request: UserUpdateRequest,
    _: object = Depends(require_admin_operator),
) -> UserAdminRecord:
    user = user_repository.update_user(user_id, request)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.patch("/users/{user_id}/status", response_model=UserAdminRecord)
def update_user_status(
    user_id: str,
    request: UserStatusUpdate,
    _: object = Depends(require_admin_operator),
) -> UserAdminRecord:
    user = user_repository.set_active(user_id, request)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.post("/users/{user_id}/reset-password", response_model=UserAdminRecord)
def reset_user_password(
    user_id: str,
    request: PasswordResetRequest,
    _: object = Depends(require_admin_operator),
) -> UserAdminRecord:
    user = user_repository.reset_password(user_id, request)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def operator_name_for_user(user_id: str | None, users_by_id: dict[str, str]) -> str:
    if user_id is None:
        return "System"
    return users_by_id.get(user_id, "Unknown operator")


IMPORTANT_SESSION_EVENTS: dict[str, tuple[str, str]] = {
    "operator_changed": ("Operator changed", "operator"),
    "session_started": ("Started session", "session"),
    "prepaid_start_paid": ("Collected prepaid start payment", "payment"),
    "time_added": ("Added time", "session"),
    "time_added_during_grace": ("Added time during grace period", "session"),
    "prepaid_add_time_paid": ("Collected add-time payment", "payment"),
    "automatic_shutdown_cancelled": ("Cancelled automatic shutdown", "session"),
    "session_ended_manually": ("Ended session", "session"),
    "session_completed": ("Session completed", "session"),
    "client_connected": ("Client connected", "session"),
    "open_time_started": ("Started Open Time", "session"),
    "open_time_ended": ("Ended Open Time", "session"),
    "open_time_settled": ("Settled Open Time", "payment"),
    "payment_action_review_required": ("Payment needs review", "payment"),
    "recovery_force_end": ("Force-ended recovered session", "recovery"),
    "wake_signal_sent": ("Wake requested", "remote"),
    "automatic_shutdown_initiated": ("Automatic shutdown initiated", "remote"),
    "automatic_shutdown_failed": ("Automatic shutdown failed", "remote"),
    "shutdown_command_acknowledged": ("Shutdown acknowledged", "remote"),
    "shutdown_command_invoked": ("Shutdown invoked", "remote"),
}


@app.get("/admin/operators", response_model=list[OperatorManagementRecord])
def list_admin_operators(
    _: object = Depends(require_admin_operator),
) -> list[OperatorManagementRecord]:
    return operator_repository.list_operators()


@app.post("/admin/operators", response_model=OperatorManagementRecord)
def create_admin_operator(
    request: OperatorCreateRequest,
    current_user=Depends(require_admin_operator),
) -> OperatorManagementRecord:
    return operator_repository.create_operator(request, current_user)


@app.put("/admin/operators/{operator_id}", response_model=OperatorManagementRecord)
def update_admin_operator(
    operator_id: str,
    request: OperatorUpdateRequest,
    current_user=Depends(require_admin_operator),
) -> OperatorManagementRecord:
    return operator_repository.update_operator(operator_id, request, current_user)


@app.post("/admin/operators/{operator_id}/enable", response_model=OperatorManagementRecord)
def enable_admin_operator(
    operator_id: str,
    current_user=Depends(require_admin_operator),
) -> OperatorManagementRecord:
    return operator_repository.set_enabled(operator_id, True, current_user)


@app.post("/admin/operators/{operator_id}/disable", response_model=OperatorManagementRecord)
def disable_admin_operator(
    operator_id: str,
    current_user=Depends(require_admin_operator),
) -> OperatorManagementRecord:
    return operator_repository.set_enabled(operator_id, False, current_user)


@app.post("/admin/operators/{operator_id}/reset-password", response_model=OperatorManagementRecord)
def reset_admin_operator_password(
    operator_id: str,
    request: OperatorPasswordResetRequest,
    current_user=Depends(require_admin_operator),
) -> OperatorManagementRecord:
    return operator_repository.reset_password(operator_id, request, current_user)


@app.post("/admin/operators/{operator_id}/sign-out", response_model=OperatorManagementRecord)
def sign_out_admin_operator(
    operator_id: str,
    current_user=Depends(require_admin_operator),
) -> OperatorManagementRecord:
    return operator_repository.sign_out_operator(operator_id, current_user)


@app.delete("/admin/operators/{operator_id}", response_model=OperatorManagementRecord)
def archive_admin_operator(
    operator_id: str,
    current_user=Depends(require_admin_operator),
) -> OperatorManagementRecord:
    return operator_repository.archive_operator(operator_id, current_user)


@app.get("/operator-activity", response_model=list[OperatorActivityRecord])
def list_operator_activity(
    date_scope: str = Query("today", pattern="^(today|all)$"),
    operator_id: str | None = None,
    category: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    _: object = Depends(require_admin_operator),
) -> list[OperatorActivityRecord]:
    return build_operator_activity(date_scope, operator_id, category, limit)


@app.get("/activity", response_model=list[OperatorActivityRecord])
def list_live_activity(
    limit: int = Query(60, ge=1, le=120),
    _: object = Depends(require_role("cashier")),
) -> list[OperatorActivityRecord]:
    return build_operator_activity("today", None, None, limit)


def build_operator_activity(
    date_scope: str,
    operator_id: str | None,
    category: str | None,
    limit: int,
) -> list[OperatorActivityRecord]:
    since = current_utc_time().date().isoformat() if date_scope == "today" else None
    users = user_repository.list_users()
    users_by_id = {user.user_id: user.display_name for user in users}
    activity: list[OperatorActivityRecord] = []

    with get_connection() as connection:
        event_rows = connection.execute(
            """
            SELECT
                e.event_type,
                e.created_at,
                e.amount,
                e.payment_method,
                e.performed_by_user_id,
                s.pc_name,
                s.member_name_snapshot,
                s.member_code_snapshot
            FROM session_events e
            LEFT JOIN sessions s ON s.session_id = e.session_id
            ORDER BY e.created_at DESC
            LIMIT 1000
            """
        ).fetchall()
        payment_rows = connection.execute(
            """
            SELECT
                p.created_at,
                p.amount,
                p.payment_method,
                p.status,
                p.recorded_by_user_id,
                s.pc_name,
                s.member_name_snapshot,
                s.member_code_snapshot
            FROM payments p
            LEFT JOIN sessions s ON s.session_id = p.session_id
            ORDER BY p.created_at DESC
            LIMIT 1000
            """
        ).fetchall()
        command_rows = connection.execute(
            """
            SELECT requested_at, command_type, status, requested_by_user_id, pc_name
            FROM remote_commands
            ORDER BY requested_at DESC
            LIMIT 1000
            """
        ).fetchall()
        settings_rows = connection.execute(
            """
            SELECT
                event_type,
                category,
                changed_fields_json,
                performed_by_user_id,
                applied_immediately,
                created_at
            FROM admin_settings_audit_events
            ORDER BY created_at DESC
            LIMIT 1000
            """
        ).fetchall()
        audit_rows = connection.execute(
            """
            SELECT
                event_type,
                operator_user_id,
                operator_display_name,
                selected_by_user_id,
                created_at,
                metadata_json
            FROM operator_audit_events
            ORDER BY created_at DESC
            LIMIT 1000
            """
        ).fetchall()

    for row in event_rows:
        details = IMPORTANT_SESSION_EVENTS.get(row["event_type"])
        if details is None:
            continue
        action, event_category = details
        timestamp = row["created_at"]
        actor_id = row["performed_by_user_id"]
        if since is not None and timestamp < since:
            continue
        if operator_id is not None and actor_id != operator_id:
            continue
        if category is not None and category != event_category:
            continue
        activity.append(
            OperatorActivityRecord(
                timestamp=timestamp,
                operator_id=actor_id,
                operator_name=operator_name_for_user(actor_id, users_by_id),
                action=action,
                category=event_category,
                pc_name=row["pc_name"],
                customer=row["member_name_snapshot"] or row["member_code_snapshot"],
                amount=Decimal(row["amount"]) if row["amount"] is not None else None,
                payment_method=row["payment_method"],
                result="Recorded",
            )
        )

    for row in payment_rows:
        timestamp = row["created_at"]
        actor_id = row["recorded_by_user_id"]
        if since is not None and timestamp < since:
            continue
        if operator_id is not None and actor_id != operator_id:
            continue
        if category is not None and category != "payment":
            continue
        activity.append(
            OperatorActivityRecord(
                timestamp=timestamp,
                operator_id=actor_id,
                operator_name=operator_name_for_user(actor_id, users_by_id),
                action="Collected payment",
                category="payment",
                pc_name=row["pc_name"],
                customer=row["member_name_snapshot"] or row["member_code_snapshot"],
                amount=Decimal(row["amount"]),
                payment_method=row["payment_method"],
                result=str(row["status"]).replace("_", " ").title(),
            )
        )

    for row in command_rows:
        command_type = str(row["command_type"])
        if command_type not in {
            "wake_on_lan",
            "shutdown",
            "restart",
            "sleep",
            "show_message",
            "show_announcement",
        }:
            continue
        timestamp = row["requested_at"]
        actor_id = row["requested_by_user_id"]
        if since is not None and timestamp < since:
            continue
        if operator_id is not None and actor_id != operator_id:
            continue
        if category is not None and category != "remote":
            continue
        activity.append(
            OperatorActivityRecord(
                timestamp=timestamp,
                operator_id=actor_id,
                operator_name=operator_name_for_user(actor_id, users_by_id),
                action=f"Sent {command_type.replace('_', ' ')} command",
                category="remote",
                pc_name=row["pc_name"],
                result=str(row["status"]).replace("_", " ").title(),
            )
        )

    for row in settings_rows:
        timestamp = row["created_at"]
        actor_id = row["performed_by_user_id"]
        if since is not None and timestamp < since:
            continue
        if operator_id is not None and actor_id != operator_id:
            continue
        if category is not None and category != "settings":
            continue
        try:
            changed_fields = ", ".join(json.loads(row["changed_fields_json"]))
        except Exception:
            changed_fields = str(row["category"])
        activity.append(
            OperatorActivityRecord(
                timestamp=timestamp,
                operator_id=actor_id,
                operator_name=operator_name_for_user(actor_id, users_by_id),
                action=f"Updated settings: {changed_fields}",
                category="settings",
                result="Applied" if row["applied_immediately"] else "Restart required",
            )
        )

    for row in audit_rows:
        event_type = str(row["event_type"])
        if event_type == "operator_changed":
            action = "Operator changed"
            event_category = "operator"
            result = "Active"
        elif event_type == "backup_created":
            action = "Backup created"
            event_category = "system"
            result = "Verified"
        elif event_type == "restore_completed":
            action = "Restore completed"
            event_category = "system"
            result = "Completed"
        elif event_type == "backup_deleted":
            action = "Backup deleted"
            event_category = "system"
            result = "Deleted"
        elif event_type == "member_created":
            action = "Member created"
            event_category = "operator"
            result = "Created"
        elif event_type == "member_username_changed":
            action = "Member username changed"
            event_category = "operator"
            result = "Updated"
        elif event_type == "member_pin_reset":
            action = "Member PIN reset"
            event_category = "operator"
            result = "Updated"
        elif event_type == "member_purchase_refunded":
            action = "Member purchase refunded"
            event_category = "payment"
            result = "Refunded"
        else:
            continue
        timestamp = row["created_at"]
        actor_id = row["selected_by_user_id"] or row["operator_user_id"]
        if since is not None and timestamp < since:
            continue
        if operator_id is not None and actor_id != operator_id:
            continue
        if category is not None and category != event_category:
            continue
        metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
        customer = metadata.get("username") or metadata.get("member_code") or row["operator_display_name"]
        activity.append(
            OperatorActivityRecord(
                timestamp=timestamp,
                operator_id=actor_id,
                operator_name=operator_name_for_user(actor_id, users_by_id),
                action=action,
                category=event_category,
                customer=str(customer),
                result=result,
            )
        )

    return sorted(activity, key=lambda item: item.timestamp, reverse=True)[:limit]


def parse_money(value: object) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"))


def storage_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def operator_name_for_sales(user_id: str | None, users_by_id: dict[str, str]) -> str:
    return operator_name_for_user(user_id, users_by_id)


def resolve_sales_range(
    period: SalesPeriod,
    start_date: date | None,
    end_date: date | None,
) -> tuple[datetime, datetime, str]:
    today = current_utc_time().date()
    if period == "today":
        start = today
        end = today + timedelta(days=1)
    elif period == "yesterday":
        start = today - timedelta(days=1)
        end = today
    elif period == "this_week":
        start = today - timedelta(days=today.weekday())
        end = today + timedelta(days=1)
    elif period == "this_month":
        start = today.replace(day=1)
        end = today + timedelta(days=1)
    else:
        if start_date is None or end_date is None:
            raise HTTPException(status_code=400, detail="Custom range requires start_date and end_date")
        if end_date < start_date:
            raise HTTPException(status_code=400, detail="End date cannot be before start date")
        start = start_date
        end = end_date + timedelta(days=1)

    start_at = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_at = datetime.combine(end, time.min, tzinfo=timezone.utc)
    grouping = "hour" if (end_at - start_at) <= timedelta(days=2) else "day"
    return start_at, end_at, grouping


def sales_type_for_payment(row: object) -> tuple[str, str]:
    if row["member_transaction_type"] in {"membership_purchase", "promo_topup"}:
        return "member_purchase", "Member Balance Purchase"
    if row["session_id"] is not None:
        return "pc_session", "PC Session"
    return "other", "Other"


def build_sales_report(
    period: SalesPeriod,
    start_date: date | None,
    end_date: date | None,
    recent_limit: int = 80,
) -> SalesReport:
    start_at, end_at, grouping = resolve_sales_range(period, start_date, end_date)
    users = user_repository.list_users()
    users_by_id = {user.user_id: user.display_name for user in users}
    start_text = start_at.isoformat()
    end_text = end_at.isoformat()

    with get_connection() as connection:
        payment_rows = connection.execute(
            """
            SELECT
                p.payment_id,
                p.session_id,
                p.amount,
                p.payment_method,
                p.cash_received,
                p.change_given,
                p.reference_number,
                p.status,
                p.action_type,
                p.recorded_by_user_id,
                p.voided_by_user_id,
                p.voided_at,
                p.void_reason,
                p.created_at,
                p.income_group_id_snapshot,
                p.income_group_name_snapshot,
                p.attribution_status,
                s.pc_name,
                s.member_name_snapshot,
                s.member_code_snapshot,
                m.full_name AS member_full_name,
                m.member_code AS member_code,
                mt.transaction_type AS member_transaction_type,
                mt.balance_type AS member_balance_type
            FROM payments p
            LEFT JOIN sessions s ON s.session_id = p.session_id
            LEFT JOIN member_time_transactions mt ON mt.related_payment_id = p.payment_id
                AND mt.transaction_type IN ('membership_purchase', 'promo_topup')
            LEFT JOIN members m ON m.member_id = mt.member_id
            WHERE p.created_at >= ?
              AND p.created_at < ?
              AND p.status = 'completed'
            ORDER BY p.created_at DESC
            """,
            (start_text, end_text),
        ).fetchall()
        refund_rows = connection.execute(
            """
            SELECT
                p.payment_id,
                p.session_id,
                p.amount,
                p.payment_method,
                p.cash_received,
                p.change_given,
                p.reference_number,
                p.status,
                p.action_type,
                p.recorded_by_user_id,
                p.voided_by_user_id,
                p.voided_at,
                p.void_reason,
                p.created_at,
                s.pc_name,
                s.member_name_snapshot,
                s.member_code_snapshot,
                m.full_name AS member_full_name,
                m.member_code AS member_code,
                mt.transaction_type AS member_transaction_type,
                mt.balance_type AS member_balance_type
            FROM payments p
            LEFT JOIN sessions s ON s.session_id = p.session_id
            LEFT JOIN member_time_transactions mt ON mt.related_payment_id = p.payment_id
                AND mt.transaction_type IN ('membership_purchase', 'promo_topup')
            LEFT JOIN members m ON m.member_id = mt.member_id
            WHERE p.created_at >= ?
              AND p.created_at < ?
              AND p.status = 'voided'
            ORDER BY p.created_at DESC
            """,
            (start_text, end_text),
        ).fetchall()
        all_payment_rows = [*payment_rows, *refund_rows]
        outstanding_rows = connection.execute(
            """
            SELECT
                s.session_id,
                s.pc_name,
                s.member_name_snapshot,
                s.member_code_snapshot,
                s.ended_at,
                s.total_amount,
                s.created_by_user_id,
                COALESCE(SUM(CASE WHEN p.status = 'completed' THEN CAST(p.amount AS REAL) ELSE 0 END), 0) AS total_paid,
                s.status
            FROM sessions s
            LEFT JOIN payments p ON p.session_id = s.session_id
            WHERE s.settlement_pending = 1
              AND s.status = 'settlement_pending'
            GROUP BY s.session_id
            HAVING (CAST(s.total_amount AS REAL) - total_paid) > 0
            ORDER BY COALESCE(s.ended_at, s.created_at) DESC
            """
        ).fetchall()

    method_totals = {
        "cash": {"amount": Decimal("0.00"), "count": 0},
        "gcash": {"amount": Decimal("0.00"), "count": 0},
        "maya": {"amount": Decimal("0.00"), "count": 0},
        "other": {"amount": Decimal("0.00"), "count": 0},
    }
    type_totals = {
        "pc_session": {"label": "PC Sessions", "amount": Decimal("0.00"), "count": 0},
        "member_purchase": {"label": "Member Balance Purchases", "amount": Decimal("0.00"), "count": 0},
        "other": {"label": "Other", "amount": Decimal("0.00"), "count": 0},
    }
    operator_totals: dict[str, dict[str, object]] = {}
    pc_totals: dict[str, dict[str, object]] = {}
    timeseries_totals: dict[str, dict[str, object]] = {}
    recent_payments: list[SalesRecentPayment] = []

    # Income-group reporting (Stage 6): the four currently-configured
    # groups always appear, even at ₱0.00, so the shop always sees the
    # full set. A custom/legacy group only appears if a payment snapshot
    # actually references it -- never hardcoded, never pre-seeded.
    income_group_totals: dict[str, dict[str, object]] = {
        group.income_group_id: {
            "income_group_id": group.income_group_id,
            "income_group_name": group.name,
            "amount": Decimal("0.00"),
            "count": 0,
        }
        for group in income_group_repository.list_income_groups()
    }
    pending_attribution_amount = Decimal("0.00")
    pending_attribution_count = 0

    for row in payment_rows:
        amount = parse_money(row["amount"])
        method = row["payment_method"]
        sales_type, _ = sales_type_for_payment(row)
        created_at = storage_datetime(row["created_at"])
        operator_id = row["recorded_by_user_id"]
        operator_key = operator_id or "__system__"
        operator_entry = operator_totals.setdefault(
            operator_key,
            {
                "operator_id": operator_id,
                "operator_name": operator_name_for_sales(operator_id, users_by_id),
                "total": Decimal("0.00"),
                "count": 0,
                "cash": Decimal("0.00"),
                "gcash": Decimal("0.00"),
                "maya": Decimal("0.00"),
                "other": Decimal("0.00"),
            },
        )
        operator_entry["total"] = operator_entry["total"] + amount
        operator_entry["count"] = int(operator_entry["count"]) + 1
        operator_entry[method] = operator_entry[method] + amount

        method_totals[method]["amount"] += amount
        method_totals[method]["count"] += 1
        type_totals[sales_type]["amount"] += amount
        type_totals[sales_type]["count"] += 1

        if row["attribution_status"] == "pending":
            pending_attribution_amount += amount
            pending_attribution_count += 1
        else:
            group_id = row["income_group_id_snapshot"]
            if group_id is None:
                # Historical payment recorded before the income-attribution
                # migration existed (attribution_status='assigned' with no
                # group snapshot, per Stage 1). Never forced into one of
                # the four current groups -- kept in its own explicit
                # bucket so income-group totals still reconcile exactly.
                group_id = "__legacy__"
                group_name = "Legacy / Unattributed"
            else:
                group_name = row["income_group_name_snapshot"] or group_id
            group_entry = income_group_totals.setdefault(
                group_id,
                {
                    "income_group_id": None if group_id == "__legacy__" else group_id,
                    "income_group_name": group_name,
                    "amount": Decimal("0.00"),
                    "count": 0,
                },
            )
            group_entry["amount"] = group_entry["amount"] + amount
            group_entry["count"] = int(group_entry["count"]) + 1

        if row["pc_name"] is not None and sales_type == "pc_session":
            pc_entry = pc_totals.setdefault(
                row["pc_name"],
                {
                    "pc_name": row["pc_name"],
                    "total": Decimal("0.00"),
                    "count": 0,
                    "sessions": set(),
                },
            )
            pc_entry["total"] = pc_entry["total"] + amount
            pc_entry["count"] = int(pc_entry["count"]) + 1
            pc_entry["sessions"].add(row["session_id"])

        bucket_start = created_at.replace(minute=0, second=0, microsecond=0) if grouping == "hour" else created_at.replace(hour=0, minute=0, second=0, microsecond=0)
        bucket_label = bucket_start.strftime("%I %p").lstrip("0") if grouping == "hour" else bucket_start.strftime("%b %d")
        bucket_key = bucket_start.isoformat()
        bucket = timeseries_totals.setdefault(
            bucket_key,
            {"label": bucket_label, "start_at": bucket_start, "amount": Decimal("0.00"), "count": 0},
        )
        bucket["amount"] = bucket["amount"] + amount
        bucket["count"] = int(bucket["count"]) + 1

        if len(recent_payments) < recent_limit:
            customer = (
                row["member_full_name"]
                or row["member_name_snapshot"]
                or row["member_code"]
                or row["member_code_snapshot"]
            )
            recent_payments.append(
                SalesRecentPayment(
                    payment_id=row["payment_id"],
                    timestamp=created_at,
                    operator_name=operator_name_for_sales(operator_id, users_by_id),
                    customer=customer,
                    pc_name=row["pc_name"],
                    sales_type=sales_type,
                    payment_method=method,
                    amount=amount,
                    status=row["status"],
                    related_reference=row["session_id"] or row["payment_id"],
                    balance_type=row["member_balance_type"],
                )
            )

    outstanding = [
        SalesOutstandingSettlement(
            session_id=row["session_id"],
            pc_name=row["pc_name"],
            customer=row["member_name_snapshot"] or row["member_code_snapshot"],
            session_end_time=storage_datetime(row["ended_at"]) if row["ended_at"] is not None else None,
            amount_due=parse_money(row["total_amount"]) - parse_money(row["total_paid"]),
            status=row["status"],
            payment_status="unpaid" if parse_money(row["total_paid"]) == Decimal("0.00") else "partially_paid",
        )
        for row in outstanding_rows
    ]
    operator_collections: dict[str, dict[str, object]] = {}
    for row in all_payment_rows:
        amount = parse_money(row["amount"])
        method = row["payment_method"]
        status = row["status"]
        sales_type, _ = sales_type_for_payment(row)
        operator_id = row["recorded_by_user_id"]
        operator_key = operator_id or "__system__"
        entry = operator_collections.setdefault(
            operator_key,
            {
                "operator_id": operator_id,
                "operator_name": operator_name_for_sales(operator_id, users_by_id),
                "transaction_count": 0,
                "gross_sales": Decimal("0.00"),
                "refunds_voids": Decimal("0.00"),
                "net_sales": Decimal("0.00"),
                "cash_received": Decimal("0.00"),
                "change_given": Decimal("0.00"),
                "cash_refunds": Decimal("0.00"),
                "non_cash_collected": Decimal("0.00"),
                "outstanding": Decimal("0.00"),
                "transactions": [],
            },
        )
        entry["transaction_count"] = int(entry["transaction_count"]) + 1
        entry["gross_sales"] = entry["gross_sales"] + amount
        if status == "voided":
            entry["refunds_voids"] = entry["refunds_voids"] + amount
            if method == "cash":
                entry["cash_refunds"] = entry["cash_refunds"] + amount
        else:
            entry["net_sales"] = entry["net_sales"] + amount
            if method == "cash":
                entry["cash_received"] = entry["cash_received"] + parse_money(row["cash_received"])
                entry["change_given"] = entry["change_given"] + parse_money(row["change_given"])
            else:
                entry["non_cash_collected"] = entry["non_cash_collected"] + amount

        customer = (
            row["member_full_name"]
            or row["member_name_snapshot"]
            or row["member_code"]
            or row["member_code_snapshot"]
        )
        entry["transactions"].append(
            SalesOperatorTransaction(
                payment_id=row["payment_id"],
                timestamp=storage_datetime(row["created_at"]),
                customer=customer,
                pc_name=row["pc_name"],
                sales_type=sales_type,
                payment_method=method,
                amount_due=amount,
                cash_received=parse_money(row["cash_received"]) if row["cash_received"] is not None else None,
                change_given=parse_money(row["change_given"]) if row["change_given"] is not None else None,
                reference_number=row["reference_number"],
                status=status,
                recorded_by_operator=operator_name_for_sales(operator_id, users_by_id),
                voided_by_operator=operator_name_for_sales(row["voided_by_user_id"], users_by_id)
                if row["voided_by_user_id"]
                else None,
                voided_at=storage_datetime(row["voided_at"]) if row["voided_at"] is not None else None,
                void_reason=row["void_reason"],
                related_reference=row["session_id"] or row["payment_id"],
                balance_type=row["member_balance_type"],
            )
        )

    for row in outstanding_rows:
        operator_id = row["created_by_user_id"]
        operator_key = operator_id or "__system__"
        entry = operator_collections.setdefault(
            operator_key,
            {
                "operator_id": operator_id,
                "operator_name": operator_name_for_sales(operator_id, users_by_id),
                "transaction_count": 0,
                "gross_sales": Decimal("0.00"),
                "refunds_voids": Decimal("0.00"),
                "net_sales": Decimal("0.00"),
                "cash_received": Decimal("0.00"),
                "change_given": Decimal("0.00"),
                "cash_refunds": Decimal("0.00"),
                "non_cash_collected": Decimal("0.00"),
                "outstanding": Decimal("0.00"),
                "transactions": [],
            },
        )
        entry["outstanding"] = entry["outstanding"] + (parse_money(row["total_amount"]) - parse_money(row["total_paid"]))

    operator_collection_rows = [
        SalesOperatorCollection(
            operator_id=values["operator_id"],
            operator_name=values["operator_name"],
            transaction_count=values["transaction_count"],
            gross_sales=values["gross_sales"],
            refunds_voids=values["refunds_voids"],
            net_sales=values["net_sales"],
            cash_received=values["cash_received"],
            change_given=values["change_given"],
            expected_cash=values["cash_received"] - values["change_given"] - values["cash_refunds"],
            non_cash_collected=values["non_cash_collected"],
            outstanding=values["outstanding"],
            transactions=sorted(values["transactions"], key=lambda item: item.timestamp, reverse=True),
        )
        for values in sorted(
            operator_collections.values(),
            key=lambda value: (value["net_sales"], value["gross_sales"]),
            reverse=True,
        )
    ]
    outstanding_amount = sum((item.amount_due for item in outstanding), Decimal("0.00"))
    total_sales = sum((parse_money(row["amount"]) for row in payment_rows), Decimal("0.00"))
    refund_total = sum((parse_money(row["amount"]) for row in refund_rows), Decimal("0.00"))
    gross_sales = total_sales + refund_total
    cash_received_total = sum(
        (
            parse_money(row["cash_received"])
            for row in payment_rows
            if row["payment_method"] == "cash"
        ),
        Decimal("0.00"),
    )
    change_given_total = sum(
        (
            parse_money(row["change_given"])
            for row in payment_rows
            if row["payment_method"] == "cash"
        ),
        Decimal("0.00"),
    )
    non_cash_collected = sum(
        (
            parse_money(row["amount"])
            for row in payment_rows
            if row["payment_method"] != "cash"
        ),
        Decimal("0.00"),
    )
    payment_count = len(payment_rows)
    average = (total_sales / payment_count).quantize(Decimal("0.01")) if payment_count else Decimal("0.00")

    return SalesReport(
        period=period,
        start_at=start_at,
        end_at=end_at,
        grouping=grouping,
        summary=SalesSummary(
            total_sales=total_sales,
            gross_sales=gross_sales,
            refund_total=refund_total,
            net_sales=total_sales,
            cash_received_total=cash_received_total,
            change_given_total=change_given_total,
            non_cash_collected=non_cash_collected,
            cash_total=method_totals["cash"]["amount"],
            gcash_total=method_totals["gcash"]["amount"],
            maya_total=method_totals["maya"]["amount"],
            other_total=method_totals["other"]["amount"],
            payment_count=payment_count,
            outstanding_amount=outstanding_amount,
            session_revenue=type_totals["pc_session"]["amount"],
            member_purchase_revenue=type_totals["member_purchase"]["amount"],
            other_revenue=type_totals["other"]["amount"],
            average_transaction_value=average,
        ),
        payment_methods=[
            SalesPaymentMethodBreakdown(
                payment_method=method,
                amount=values["amount"],
                payment_count=values["count"],
            )
            for method, values in method_totals.items()
        ],
        sales_types=[
            SalesTypeBreakdown(
                sales_type=sales_type,
                label=values["label"],
                amount=values["amount"],
                payment_count=values["count"],
            )
            for sales_type, values in type_totals.items()
        ],
        operators=[
            SalesOperatorBreakdown(
                operator_id=values["operator_id"],
                operator_name=values["operator_name"],
                total_amount=values["total"],
                payment_count=values["count"],
                cash_total=values["cash"],
                gcash_total=values["gcash"],
                maya_total=values["maya"],
                other_total=values["other"],
            )
            for values in sorted(operator_totals.values(), key=lambda value: value["total"], reverse=True)
        ],
        operator_collections=operator_collection_rows,
        pcs=[
            SalesPCBreakdown(
                pc_name=values["pc_name"],
                total_amount=values["total"],
                payment_count=values["count"],
                paid_session_count=len(values["sessions"]),
            )
            for values in sorted(pc_totals.values(), key=lambda value: value["total"], reverse=True)
        ],
        timeseries=[
            SalesTimeseriesPoint(
                label=values["label"],
                start_at=values["start_at"],
                amount=values["amount"],
                payment_count=values["count"],
            )
            for _, values in sorted(timeseries_totals.items())
        ],
        recent_payments=recent_payments,
        outstanding_settlements=outstanding,
        income_groups=[
            SalesIncomeGroupBreakdown(
                income_group_id=values["income_group_id"],
                income_group_name=values["income_group_name"],
                amount=values["amount"],
                payment_count=values["count"],
            )
            for values in sorted(
                income_group_totals.values(),
                key=lambda value: value["income_group_name"],
            )
        ],
        pending_attribution_amount=pending_attribution_amount,
        pending_attribution_count=pending_attribution_count,
    )


@app.get("/sales/report", response_model=SalesReport)
def get_sales_report(
    period: SalesPeriod = Query("today"),
    start_date: date | None = None,
    end_date: date | None = None,
    _: object = Depends(require_admin_operator),
) -> SalesReport:
    return build_sales_report(period, start_date, end_date)


@app.get("/sales/export.csv")
def export_sales_report(
    period: SalesPeriod = Query("today"),
    start_date: date | None = None,
    end_date: date | None = None,
    _: object = Depends(require_admin_operator),
) -> Response:
    report = build_sales_report(period, start_date, end_date, recent_limit=10000)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "payment timestamp",
        "operator",
        "type",
        "PC",
        "customer/member",
        "payment method",
        "amount",
        "status",
        "related reference",
    ])
    for payment in report.recent_payments:
        writer.writerow([
            payment.timestamp.isoformat(),
            payment.operator_name,
            payment.sales_type,
            payment.pc_name or "",
            payment.customer or "",
            payment.payment_method,
            str(payment.amount),
            payment.status,
            payment.related_reference or "",
        ])
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="secret-grounds-sales.csv"'},
    )


@app.get("/dashboard/today-sales", response_model=Decimal)
def get_dashboard_today_sales(_: object = Depends(require_role("cashier"))) -> Decimal:
    start_at, end_at, _ = resolve_sales_range("today", None, None)
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN status = 'completed' THEN CAST(amount AS REAL) ELSE 0 END), 0) AS total
            FROM payments
            WHERE created_at >= ?
              AND created_at < ?
            """,
            (start_at.isoformat(), end_at.isoformat()),
        ).fetchone()
    return parse_money(row["total"] if row is not None else "0.00")


@app.get("/dashboard/operator-sale", response_model=OperatorSaleSummary)
def get_dashboard_operator_sale(_: object = Depends(require_role("cashier"))) -> OperatorSaleSummary:
    """Read-only projection: how much has the CURRENT on-duty operator
    collected during their CURRENT open shift. Resolves the on-duty
    operator through the exact same lookup payment attribution uses
    (get_on_duty_operator) -- never the calling browser's own cookie
    user -- so this can never disagree with what payments are actually
    being credited to. Reuses ShiftRepository.calculate_totals() as-is;
    no second sales calculation is introduced here."""
    on_duty = get_on_duty_operator()
    if on_duty.operator is None:
        return OperatorSaleSummary(has_open_shift=False)

    shift = shift_repository.get_open_shift_for_user(on_duty.operator.user_id)
    if shift is None:
        return OperatorSaleSummary(
            has_open_shift=False,
            operator_user_id=on_duty.operator.user_id,
            operator_name=on_duty.operator.display_name,
        )

    totals = shift_repository.calculate_totals(shift)
    # gross_session_sales is already the SUM of only status='completed'
    # payments -- calculate_totals() computes voided_payments as a
    # disjoint bucket (status='voided'), never a subset of
    # gross_session_sales. A voided payment is therefore already excluded
    # the moment it's voided; subtracting voided_payments again here
    # would double-count it. gross_session_sales is already the correct
    # net-of-void figure, exposed as-is.
    return OperatorSaleSummary(
        has_open_shift=True,
        operator_user_id=on_duty.operator.user_id,
        operator_name=on_duty.operator.display_name,
        shift_id=shift.shift_id,
        operator_sale=totals.gross_session_sales,
        cash_payments=totals.cash_payments,
        gcash_payments=totals.gcash_payments,
        maya_payments=totals.maya_payments,
        other_payments=totals.other_payments,
        voided_payments=totals.voided_payments,
        payment_count=totals.payment_count,
    )


@app.post("/register", response_model=PCRegistrationResponse)
def register_pc(pc: PCRegistration) -> PCRegistrationResponse:
    pc_name, ip_address = clean_pc_payload(pc.pc_name, pc.ip_address)
    reject_duplicate_station_id(pc_name, pc.mac_address)
    if pc.mac_address:
        pc_metadata_repository.set_mac_address(pc_name, pc.mac_address)
    registered_pc = touch_pc(pc_name, ip_address, pc.display_name, pc.mac_address)

    return PCRegistrationResponse(
        message=f"{pc_name} registered successfully",
        pc_name=registered_pc.pc_name,
        ip_address=registered_pc.ip_address,
        status=registered_pc.status,
        last_seen=registered_pc.last_seen,
        mac_address=registered_pc.mac_address,
    )


@app.get("/client-registration/check")
def check_client_registration(
    pc_name: str = Query(..., min_length=1),
    mac_address: str | None = None,
) -> dict[str, object]:
    cleaned_pc_name = pc_name.strip()
    if not cleaned_pc_name:
        raise HTTPException(status_code=400, detail="Station ID must not be blank")
    try:
        reject_duplicate_station_id(cleaned_pc_name, mac_address)
    except HTTPException as error:
        return {
            "available": False,
            "pc_name": cleaned_pc_name,
            "detail": error.detail,
        }
    return {
        "available": True,
        "pc_name": cleaned_pc_name,
        "detail": "Station ID is available",
    }


@app.post("/stations/resolve", response_model=StationResolveResponse)
def resolve_station(request: StationResolveRequest) -> StationResolveResponse:
    """Public, pre-registration identity lookup for a booting client that
    does not yet have a pc_name. Deliberately minimal and read-mostly:
    records this MAC's sighting (mac_address/ip_address only -- see
    StationResolveRequest's extra="forbid", which makes it impossible for
    a caller to smuggle a pc_name in) and reports back only whether the
    server's station_assignments table already has an active binding for
    it. Never creates, changes, or removes an assignment; never touches
    pc_layouts, income groups, or Standard/VIP configuration.
    """
    record = station_repository.record_sighting(request.mac_address, request.ip_address)

    if record.status == "assigned" and record.pc_name is not None:
        layout = pc_layout_repository.get_layout(record.pc_name)
        return StationResolveResponse(
            status="assigned",
            pc_name=record.pc_name,
            display_name=layout.display_name if layout is not None else None,
        )

    # 'unclaimed' and 'revoked' both mean "no active assignment right now"
    # from this endpoint's perspective -- a revoked MAC must never regain
    # its old station automatically, and the response must not hint at
    # revocation history to an unauthenticated caller.
    return StationResolveResponse(status="unclaimed", pc_name=None)


@app.post("/heartbeat", response_model=PCRecord)
def heartbeat(pc: PCHeartbeat) -> PCRecord:
    pc_name, ip_address = clean_pc_payload(pc.pc_name, pc.ip_address)

    if pc_name not in pc_registry:
        raise HTTPException(
            status_code=404,
            detail=f"{pc_name} is not registered",
        )

    return touch_pc(pc_name, ip_address, pc.display_name)


@app.websocket("/ws/{pc_name}")
async def client_websocket(websocket: WebSocket, pc_name: str) -> None:
    cleaned_pc_name = connection_manager.normalize_pc_name(pc_name)

    if not cleaned_pc_name or cleaned_pc_name not in pc_registry:
        print(
            "Command WebSocket rejected: "
            f"pc_name={cleaned_pc_name!r}, registered_pc_names={sorted(pc_registry)}"
        )
        await websocket.close(code=1008)
        return

    await connection_manager.connect(cleaned_pc_name, websocket)
    async def activate_after_receive_loop_starts() -> None:
        activation_response = await session_service.activate_prepared_session_for_pc(
            cleaned_pc_name
        )
        if activation_response is not None:
            print(
                "Prepared startup activation processed: "
                f"pc_name={cleaned_pc_name}, "
                f"prepared_session_id={activation_response.prepared_session.prepared_session_id}, "
                f"status={activation_response.prepared_session.status}, "
                f"session_id={activation_response.prepared_session.activated_session_id}"
            )

    activation_task = asyncio.create_task(activate_after_receive_loop_starts())

    try:
        # The WebSocket endpoint owns all incoming acknowledgement reads, which
        # lets FastAPI detect disconnects and keeps HTTP command routes simple.
        while True:
            message = await websocket.receive_json()
            if "command_id" in message:
                await connection_manager.record_acknowledgement(message)
            elif message.get("event_type") in {
                "session_finished",
                "expiration_grace_started",
                "expiration_final_warning",
                "expiration_shutdown_started",
                "expiration_shutdown_invoked",
                "expiration_shutdown_failed",
            }:
                client_event = ClientEvent.model_validate(message)
                if client_event.event_type == "expiration_grace_started":
                    grace_session = session_service.begin_expiration_grace(
                        client_event.session_id
                    )
                    recorded = grace_session is not None
                    response_event_type = "expiration_grace_recorded"
                    extra_payload = {
                        "shutdown_at": grace_session.shutdown_at.isoformat()
                        if grace_session is not None and grace_session.shutdown_at is not None
                        else None
                    }
                elif client_event.event_type == "expiration_final_warning":
                    session_service.create_event(
                        client_event.session_id,
                        "expiration_final_warning_reached",
                        message=client_event.message or "Final 30-second warning reached",
                    )
                    recorded = True
                    response_event_type = "expiration_final_warning_recorded"
                    extra_payload = {}
                elif client_event.event_type == "expiration_shutdown_invoked":
                    session_service.create_event(
                        client_event.session_id,
                        "shutdown_command_invoked",
                        message=client_event.message or "Client invoked allowlisted shutdown handler",
                    )
                    recorded = True
                    response_event_type = "expiration_shutdown_invocation_recorded"
                    extra_payload = {}
                elif client_event.event_type == "expiration_shutdown_failed":
                    session_service.create_event(
                        client_event.session_id,
                        "automatic_shutdown_failed",
                        message=client_event.message or client_event.reason,
                    )
                    recorded = True
                    response_event_type = "expiration_shutdown_failure_recorded"
                    extra_payload = {}
                else:
                    recorded = session_service.complete_from_client(
                        client_event.session_id,
                        client_event.message,
                    )
                    recovery_flags.pop(cleaned_pc_name, None)
                    response_event_type = "session_completion_recorded"
                    extra_payload = {}
                await websocket.send_json(
                    {
                        "event_type": response_event_type,
                        "session_id": client_event.session_id,
                        "recorded": recorded,
                        "server_time": current_utc_time().isoformat(),
                        **extra_payload,
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
        activation_task.cancel()
        connection_manager.disconnect(cleaned_pc_name, websocket)


@app.websocket("/ws/screens/{pc_name}")
async def screen_upload_websocket(websocket: WebSocket, pc_name: str) -> None:
    """Separate, dedicated channel for PC screen monitoring frames.

    Deliberately independent of /ws/{pc_name} (commands/acknowledgements)
    -- a stalled or slow screen upload must never be able to delay or
    interfere with command delivery, and vice versa. Authorization here
    is the same station-identity check the command socket already uses:
    only a pc_name that has actually registered may push frames.
    """
    cleaned_pc_name = connection_manager.normalize_pc_name(pc_name)

    if not SCREEN_MONITORING_ENABLED:
        await websocket.close(code=1008)
        return

    if not cleaned_pc_name or cleaned_pc_name not in pc_registry:
        print(
            "Screen WebSocket rejected: "
            f"pc_name={cleaned_pc_name!r}, registered_pc_names={sorted(pc_registry)}"
        )
        await websocket.close(code=1008)
        return

    await websocket.accept()
    print(f"Screen WebSocket connected: pc_name={cleaned_pc_name}")
    pending_sequence: int | None = None

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            text_payload = message.get("text")
            if text_payload is not None:
                # Optional per-frame metadata (currently just an
                # in-order sequence number the client wants echoed and
                # stored). Applies to the very next binary frame only.
                try:
                    parsed = json.loads(text_payload)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(parsed, dict) and parsed.get("type") == "frame_meta":
                    sequence_value = parsed.get("sequence")
                    pending_sequence = sequence_value if isinstance(sequence_value, int) else None
                continue

            data = message.get("bytes")
            if not data:
                continue

            sequence = pending_sequence
            pending_sequence = None

            content_type = sniff_image_content_type(data)
            if content_type is None:
                await websocket.send_json(
                    {"type": "frame_rejected", "reason": "Unsupported or unrecognized image format"}
                )
                continue

            try:
                frame = screen_monitor_service.store_frame(cleaned_pc_name, content_type, data, sequence)
            except HTTPException as error:
                await websocket.send_json({"type": "frame_rejected", "reason": error.detail})
                continue

            await websocket.send_json(
                {
                    "type": "frame_accepted",
                    "sequence": frame.sequence,
                    "byte_size": frame.byte_size,
                    "received_at": frame.received_at.isoformat(),
                }
            )
    except WebSocketDisconnect:
        pass
    finally:
        screen_monitor_service.clear_frame(cleaned_pc_name)
        print(f"Screen WebSocket disconnected: pc_name={cleaned_pc_name}")


@app.get("/monitor/pcs", response_model=list[ScreenMonitorPCStatus])
def list_monitored_pcs(
    _: object = Depends(require_role("cashier")),
) -> list[ScreenMonitorPCStatus]:
    mark_stale_pcs_offline()
    statuses: list[ScreenMonitorPCStatus] = []

    for pc_name in sorted(pc_registry):
        pc = pc_registry[pc_name]
        frame, status = screen_monitor_service.frame_status(pc_name, is_online=pc.status == "online")
        statuses.append(
            ScreenMonitorPCStatus(
                pc_name=pc_name,
                has_frame=frame is not None,
                frame_received_at=frame.received_at if frame is not None else None,
                frame_age_seconds=(
                    (current_utc_time() - frame.received_at).total_seconds() if frame is not None else None
                ),
                frame_status=status,
            )
        )

    return statuses


@app.get("/monitor/pcs/{pc_name}/frame")
def get_monitored_pc_frame(
    pc_name: str,
    _: object = Depends(require_role("cashier")),
) -> Response:
    frame = screen_monitor_service.get_frame(pc_name.strip())
    if frame is None:
        raise HTTPException(status_code=404, detail=f"No screen frame available for {pc_name}")

    return Response(
        content=frame.data,
        media_type=frame.content_type,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/pcs", response_model=list[PCDashboardRecord])
def get_registered_pcs(
    _: object = Depends(require_role("cashier")),
) -> list[PCDashboardRecord]:
    mark_stale_pcs_offline()
    prepared_session_repository.expire_old_preparations()
    now = current_utc_time()
    dashboard_records: list[PCDashboardRecord] = []
    layouts = {layout.pc_name: layout for layout in pc_layout_repository.list_layouts()}
    prepared_by_pc = {
        prepared.pc_name: prepared
        for prepared in prepared_session_repository.list_active()
    }
    # Operational dashboard data is registered-client only. Layout rows are
    # configuration slots and must not become cashier-visible PCs until a real
    # Secret Grounds client has registered at least once.
    pc_names = sorted(
        pc_registry,
        key=lambda name: (
            layouts[name].layout_order if name in layouts else 10_000,
            name,
        ),
    )

    for pc_name in pc_names:
        layout = layouts.get(pc_name)
        pc = pc_registry[pc_name]
        active_session = session_repository.get_active_session_for_pc(pc.pc_name)
        prepared = prepared_by_pc.get(pc.pc_name)
        recovery_flag = recovery_flags.get(pc.pc_name)
        remaining_seconds: int | None = None
        connection_status = "online" if pc.status == "online" else "offline"
        session_status = (
            active_session.status
            if active_session is not None
            and active_session.status
            in {"active", "grace_period", "shutting_down", "ending", "settlement_pending"}
            else "idle"
        )

        if (
            connection_status == "offline"
            and active_session is not None
            and active_session.status in {"active", "grace_period", "ending"}
        ):
            session_status = "unknown"
        power_state = "online" if connection_status == "online" else "powered_off"
        if active_session is not None and active_session.status == "shutting_down":
            power_state = "shutting_down" if connection_status == "online" else "powered_off"
        if prepared is not None:
            power_state = "starting" if prepared.status == "starting" else "waking"
            session_status = "idle"
        if recovery_flag is not None:
            power_state = "recovery_required"
        elif (
            connection_status == "offline"
            and active_session is not None
            and active_session.status in {"active", "grace_period", "ending"}
        ):
            power_state = "connection_problem"

        if active_session is not None:
            remaining_target = (
                active_session.shutdown_at
                if active_session.status in {"grace_period", "shutting_down"}
                and active_session.shutdown_at is not None
                else active_session.scheduled_end_at
            )
            remaining_seconds = max(
                0,
                int((remaining_target - now).total_seconds()),
            )
            billing = billing_service.summarize_session(active_session)
            display_session = billing.session
        else:
            billing = None
            display_session = None

        dashboard_records.append(
            PCDashboardRecord(
                pc_name=pc.pc_name,
                display_name=layout.display_name if layout is not None else pc.pc_name,
                area_label=layout.area_label if layout is not None else "UNASSIGNED",
                layout_order=layout.layout_order if layout is not None else 10_000,
                is_vip=layout.is_vip if layout is not None else False,
                is_enabled=layout.is_enabled if layout is not None else True,
                ip_address=pc.ip_address,
                connection_status=connection_status,
                has_command_connection=connection_manager.is_connected(pc.pc_name),
                power_state=power_state,
                last_seen=pc.last_seen,
                session_status=session_status,
                startup_status=prepared.status if prepared is not None else None,
                prepared_session_id=(
                    prepared.prepared_session_id if prepared is not None else None
                ),
                startup_elapsed_seconds=(
                    max(0, int((now - prepared.prepared_at).total_seconds()))
                    if prepared is not None
                    else None
                ),
                startup_expires_at=prepared.expires_at if prepared is not None else None,
                startup_failure_reason=(
                    prepared.failure_reason if prepared is not None else None
                ),
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
                grace_started_at=(
                    active_session.grace_started_at
                    if active_session is not None
                    else None
                ),
                shutdown_at=(
                    active_session.shutdown_at if active_session is not None else None
                ),
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
                member_id=(
                    display_session.member_id if display_session is not None else None
                ),
                member_code_snapshot=(
                    display_session.member_code_snapshot if display_session is not None else None
                ),
                member_name_snapshot=(
                    display_session.member_name_snapshot if display_session is not None else None
                ),
                member_balance_before_seconds=(
                    display_session.member_balance_before_seconds
                    if display_session is not None
                    else None
                ),
                member_balance_after_seconds=(
                    remaining_seconds
                    if display_session is not None
                    and display_session.session_mode == "member_balance"
                    and display_session.status in {"active", "grace_period", "ending"}
                    and remaining_seconds is not None
                    else display_session.member_balance_after_seconds
                    if display_session is not None
                    else None
                ),
                mac_address=pc.mac_address,
                customer_locked=(
                    active_session.customer_locked if active_session is not None else False
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
        and active_session.status in {"active", "grace_period", "shutting_down", "ending"}
    )

    return PCSessionStateResponse(
        pc_name=cleaned_pc_name,
        has_active_session=has_client_active_session,
        session=(
            SessionStateSummary(
                session_id=active_session.session_id,
                status=active_session.status,
                started_at=active_session.started_at,
                scheduled_end_at=active_session.scheduled_end_at,
                grace_started_at=active_session.grace_started_at,
                shutdown_at=active_session.shutdown_at,
                total_seconds=active_session.total_seconds,
                session_mode=active_session.session_mode,
                member_code_snapshot=active_session.member_code_snapshot,
                member_name_snapshot=active_session.member_name_snapshot,
                customer_locked=active_session.customer_locked,
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


POWER_COMMAND_TYPES: set[str] = {"shutdown", "restart", "sleep"}
EXPECTED_DISCONNECT_COMMANDS: set[str] = {"shutdown", "restart"}
REMOTE_MESSAGE_COMMAND_TYPES: set[str] = {"show_message", "show_announcement"}


def require_remote_command_permission(command_type: str, current_user: object) -> None:
    role = getattr(current_user, "role", None)
    if command_type == "wake_on_lan":
        if role not in {"owner", "admin", "cashier"}:
            raise HTTPException(status_code=403, detail="You cannot wake PCs")
        return
    if command_type in POWER_COMMAND_TYPES:
        if role not in {"owner", "admin"}:
            raise HTTPException(status_code=403, detail="Only owner/admin can use PC power controls")
        return
    if command_type in REMOTE_MESSAGE_COMMAND_TYPES:
        if role not in {"owner", "admin", "cashier"}:
            raise HTTPException(status_code=403, detail="You cannot send remote messages")
        return
    raise HTTPException(status_code=400, detail="Unsupported remote command type")


def get_registered_pc_or_404(pc_name: str) -> PCRecord:
    cleaned_pc_name = pc_name.strip()
    pc = pc_registry.get(cleaned_pc_name)
    if pc is None:
        raise HTTPException(status_code=404, detail="PC is not registered")
    return pc


def require_registered_client_identity(
    pc_name: str,
    ip_address: str,
    mac_address: str | None,
) -> PCRecord:
    cleaned_pc_name, _ = clean_pc_payload(pc_name, ip_address)
    pc = pc_registry.get(cleaned_pc_name)
    if pc is None or pc.status == "offline":
        raise HTTPException(status_code=409, detail="This computer is not currently available.")
    expected_mac = pc_metadata_repository.get_mac_address(cleaned_pc_name)
    if expected_mac and mac_address and expected_mac.casefold() != mac_address.casefold():
        raise HTTPException(status_code=403, detail="This computer is not currently available.")
    if expected_mac and not mac_address:
        raise HTTPException(status_code=403, detail="This computer is not currently available.")
    return pc


def get_known_pc_or_404(pc_name: str) -> PCRecord:
    cleaned_pc_name = pc_name.strip()
    pc = pc_registry.get(cleaned_pc_name)
    if pc is not None:
        return pc
    layout = pc_layout_repository.get_layout(cleaned_pc_name)
    if layout is None:
        raise HTTPException(status_code=404, detail="PC is not configured")
    return PCRecord(
        pc_name=layout.pc_name,
        ip_address="",
        status="offline",
        last_seen=current_utc_time(),
        mac_address=layout.mac_address,
    )


def get_on_duty_operator() -> OnDutyOperatorRecord:
    """The single authoritative on-duty lookup. Both payment attribution
    (get_on_duty_actor_user_id below) and the Operator's Sale read
    (get_dashboard_operator_sale) call this exact function, so they can
    never disagree about who is on duty -- they just differ in what they
    do when nobody is: a payment write always needs some actor (falls
    back to the browser's own cookie user), while a read that's about to
    tell the whole shop "whose sales is this" must not silently
    substitute whoever happens to be looking at the dashboard."""
    return operator_state_repository.get_current(user_repository.get_user)


def get_on_duty_actor_user_id(current_user: object) -> str | None:
    current_operator = get_on_duty_operator()
    if current_operator.operator is not None:
        return current_operator.operator.user_id
    return get_actor_user_id(current_user)


def send_wake_on_lan_packet(mac_address: str) -> None:
    mac_bytes = bytes.fromhex(mac_address.replace(":", ""))
    packet = b"\xff" * 6 + mac_bytes * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as wol_socket:
        wol_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        wol_socket.sendto(packet, ("255.255.255.255", 9))


def send_wake_on_lan_burst(mac_address: str, packets: int = 3) -> None:
    for _ in range(max(1, packets)):
        send_wake_on_lan_packet(mac_address)


async def deliver_remote_command(
    pc_name: str,
    command_id: str,
    command_type: RemoteCommandType,
    payload: dict[str, object],
) -> list[CommandAcknowledgement]:
    command = ClientCommand(
        command_id=command_id,
        type=command_type,  # type: ignore[arg-type]
        payload=payload,
        created_at=current_utc_time(),
    )
    def record_acknowledgement(acknowledgement: CommandAcknowledgement) -> None:
        if acknowledgement.status == "received":
            remote_command_repository.mark_received(command_id, acknowledgement.message)
        elif acknowledgement.status == "completed":
            remote_command_repository.mark_completed(command_id, acknowledgement.message)
        elif acknowledgement.status == "failed":
            remote_command_repository.mark_failed(
                command_id,
                "failed",
                acknowledgement.message or "Client command failed",
                acknowledgement.message,
            )

    try:
        acknowledgements = await asyncio.wait_for(
            connection_manager.send_command(pc_name, command, record_acknowledgement),
            timeout=COMMAND_ACK_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        record = remote_command_repository.get(command_id)
        if command_type in EXPECTED_DISCONNECT_COMMANDS and record is not None and record.received_at is not None:
            remote_command_repository.mark_completed(command_id, "Expected disconnect after received acknowledgement")
            return [
                CommandAcknowledgement(
                    command_id=command_id,
                    status="completed",
                    message="Expected disconnect after received acknowledgement",
                )
            ]
        remote_command_repository.mark_failed(command_id, "timed_out", "Client command timed out")
        raise HTTPException(status_code=504, detail="The client did not finish the remote command")
    except Exception as error:
        remote_command_repository.mark_failed(command_id, "failed", str(error))
        raise HTTPException(status_code=502, detail=f"Command delivery failed: {error}") from error

    return acknowledgements


async def create_and_send_remote_command(
    pc_name: str,
    command_type: RemoteCommandType,
    payload: dict[str, object],
    current_user: object,
    forced: bool = False,
    force_reason: str | None = None,
) -> RemoteCommandResponse:
    require_remote_command_permission(command_type, current_user)
    pc = get_registered_pc_or_404(pc_name)

    if pc.status == "offline":
        raise HTTPException(status_code=409, detail="This PC is offline")
    if not connection_manager.is_connected(pc.pc_name):
        raise HTTPException(status_code=409, detail="This PC has no active command connection")

    command_id = str(uuid4())
    record = remote_command_repository.create(
        command_id=command_id,
        pc_name=pc.pc_name,
        command_type=command_type,
        payload=payload,
        requested_by_user_id=get_on_duty_actor_user_id(current_user),
        forced=forced,
        force_reason=force_reason,
    )
    acknowledgements = await deliver_remote_command(pc.pc_name, command_id, command_type, payload)
    updated = remote_command_repository.get(command_id) or record
    return RemoteCommandResponse(command=updated, acknowledgements=acknowledgements)


@app.put("/pcs/{pc_name}/mac", response_model=PCRecord)
def update_pc_mac_address(
    pc_name: str,
    request: PCMacAddressUpdate,
    _: object = Depends(require_role("admin")),
) -> PCRecord:
    pc = get_registered_pc_or_404(pc_name)
    mac_address = pc_metadata_repository.set_mac_address(pc.pc_name, request.mac_address)
    updated = pc.model_copy(update={"mac_address": mac_address})
    pc_registry[pc.pc_name] = updated
    return updated


@app.post("/pcs/{pc_name}/message", response_model=RemoteCommandResponse)
async def send_pc_message(
    pc_name: str,
    request: MessageCommandRequest,
    current_user: object = Depends(require_role("cashier")),
) -> RemoteCommandResponse:
    return await create_and_send_remote_command(
        pc_name,
        "show_message",
        request.model_dump(mode="json"),
        current_user,
    )


@app.post("/pcs/{pc_name}/power/{command_type}", response_model=RemoteCommandResponse)
async def send_power_command(
    pc_name: str,
    command_type: str,
    request: PowerCommandRequest,
    current_user: object = Depends(require_role("admin")),
) -> RemoteCommandResponse:
    if command_type not in POWER_COMMAND_TYPES:
        raise HTTPException(status_code=404, detail="Unsupported power command")

    pc = get_registered_pc_or_404(pc_name)
    active_session = session_repository.get_active_session_for_pc(pc.pc_name)
    if active_session is not None and active_session.status == "active":
        if not request.force:
            rejected_id = str(uuid4())
            remote_command_repository.create(
                command_id=rejected_id,
                pc_name=pc.pc_name,
                command_type=command_type,  # type: ignore[arg-type]
                payload=request.model_dump(mode="json"),
                requested_by_user_id=get_actor_user_id(current_user),
                status="rejected",
            )
            remote_command_repository.mark_failed(
                rejected_id,
                "rejected",
                "Active session power command rejected without force confirmation",
            )
            raise HTTPException(
                status_code=409,
                detail="Active sessions require force=true, confirmation, and a reason before power control",
            )
        if not request.confirm or not request.reason or not request.reason.strip():
            rejected_id = str(uuid4())
            remote_command_repository.create(
                command_id=rejected_id,
                pc_name=pc.pc_name,
                command_type=command_type,  # type: ignore[arg-type]
                payload=request.model_dump(mode="json"),
                requested_by_user_id=get_actor_user_id(current_user),
                forced=request.force,
                force_reason=request.reason,
                status="rejected",
            )
            remote_command_repository.mark_failed(
                rejected_id,
                "rejected",
                "Forced power command rejected without confirmation reason",
            )
            raise HTTPException(
                status_code=400,
                detail="Forced power commands require confirmation and a reason",
            )
        recovery_flags[pc.pc_name] = {
            "session_id": active_session.session_id,
            "reason": f"forced_{command_type}",
        }

    return await create_and_send_remote_command(
        pc.pc_name,
        command_type,  # type: ignore[arg-type]
        {
            "force": request.force,
            "reason": request.reason,
            "confirm": request.confirm,
        },
        current_user,
        forced=request.force,
        force_reason=request.reason,
    )


@app.post("/pcs/{pc_name}/wake", response_model=WakeCommandResponse)
def wake_pc(
    pc_name: str,
    current_user: object = Depends(require_role("cashier")),
) -> WakeCommandResponse:
    require_remote_command_permission("wake_on_lan", current_user)
    pc = get_known_pc_or_404(pc_name)
    if not pc.mac_address:
        raise HTTPException(status_code=409, detail="This PC does not have a saved MAC address")

    command_id = str(uuid4())
    record = remote_command_repository.create(
        command_id=command_id,
        pc_name=pc.pc_name,
        command_type="wake_on_lan",
        payload={"mac_address": pc.mac_address},
        requested_by_user_id=get_on_duty_actor_user_id(current_user),
    )
    try:
        send_wake_on_lan_burst(pc.mac_address)
    except OSError as error:
        remote_command_repository.mark_failed(command_id, "failed", str(error))
        raise HTTPException(status_code=502, detail=f"Wake signal failed: {error}") from error
    remote_command_repository.mark_completed(command_id, "Wake-on-LAN packet_sent")
    return WakeCommandResponse(
        command=remote_command_repository.get(command_id) or record,
        message="Wake signal sent. Waiting for PC to come online.",
    )


@app.get("/pcs/layout", response_model=list[PCLayoutRecord])
def list_pc_layouts(
    include_disabled: bool = False,
    _: object = Depends(require_owner),
) -> list[PCLayoutRecord]:
    return pc_layout_repository.list_layouts(include_disabled)


@app.get("/income-groups", response_model=list[IncomeGroupRecord])
def list_income_groups(
    _: object = Depends(require_owner),
) -> list[IncomeGroupRecord]:
    return income_group_repository.list_income_groups()


@app.put("/pcs/{pc_name}/layout", response_model=PCLayoutRecord)
def update_pc_layout(
    pc_name: str,
    request: PCLayoutUpdate,
    _: object = Depends(require_role("admin")),
) -> PCLayoutRecord:
    cleaned_pc_name = pc_name.strip()
    if request.income_group_id is not None:
        income_group = income_group_repository.get_income_group(request.income_group_id)
        if income_group is None:
            raise HTTPException(status_code=400, detail="Unknown income group")
    if request.mac_address is not None:
        pc_metadata_repository.set_mac_address(cleaned_pc_name, request.mac_address)
    layout = pc_layout_repository.upsert_layout(cleaned_pc_name, request)
    if layout.income_group_id is not None:
        # Loosely coupled by design (same posture as the member-session
        # sweep): the layout save has already succeeded. A failure here
        # must never undo or fail the layout change -- any payment that
        # can't be resolved just stays pending for a later attempt.
        try:
            income_group = income_group_repository.get_income_group(layout.income_group_id)
            if income_group is not None:
                resolve_pending_payments_for_configured_pc(
                    payment_repository,
                    cleaned_pc_name,
                    income_group.income_group_id,
                    income_group.name,
                    current_utc_time(),
                )
        except Exception:
            logger.exception(
                "Pending-attribution resolution failed after configuring %s -> %s",
                cleaned_pc_name,
                layout.income_group_id,
            )
    return layout


def clean_station_mac_path_param(mac_address: str) -> str:
    try:
        normalized = normalize_mac_address(mac_address)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if normalized is None:
        raise HTTPException(status_code=400, detail="mac_address must not be blank")
    return normalized


def require_configured_pc_layout(pc_name: str) -> None:
    # Station identity and PC Layout are separate concepts: assignment must
    # only ever target a station that already exists in pc_layouts, and
    # must never create one implicitly.
    if pc_layout_repository.get_layout(pc_name) is None:
        raise HTTPException(
            status_code=400,
            detail=f"{pc_name} is not a configured station. Add it under PC Layout first.",
        )


@app.get("/stations/unclaimed", response_model=list[StationAssignmentRecord])
def list_unclaimed_stations(_: object = Depends(require_owner)) -> list[StationAssignmentRecord]:
    return station_repository.list_unclaimed()


@app.get("/stations/assigned", response_model=list[StationAssignmentRecord])
def list_assigned_stations(_: object = Depends(require_owner)) -> list[StationAssignmentRecord]:
    return station_repository.list_assigned()


@app.post("/stations/{mac_address}/assign", response_model=StationAssignmentRecord)
def assign_station(
    mac_address: str,
    request: StationAssignRequest,
    current_user: object = Depends(require_owner),
) -> StationAssignmentRecord:
    cleaned_mac = clean_station_mac_path_param(mac_address)
    require_configured_pc_layout(request.pc_name)
    return station_repository.assign(
        cleaned_mac,
        request.pc_name,
        assigned_by_user_id=get_actor_user_id(current_user),
    )


@app.post("/stations/{mac_address}/reassign", response_model=StationAssignmentRecord)
def reassign_station(
    mac_address: str,
    request: StationAssignRequest,
    current_user: object = Depends(require_owner),
) -> StationAssignmentRecord:
    cleaned_mac = clean_station_mac_path_param(mac_address)
    require_configured_pc_layout(request.pc_name)
    return station_repository.reassign(
        cleaned_mac,
        request.pc_name,
        assigned_by_user_id=get_actor_user_id(current_user),
    )


@app.post("/stations/{mac_address}/revoke", response_model=StationAssignmentRecord)
def revoke_station(
    mac_address: str,
    _: object = Depends(require_owner),
) -> StationAssignmentRecord:
    cleaned_mac = clean_station_mac_path_param(mac_address)
    return station_repository.revoke(cleaned_mac)


@app.get("/operators", response_model=list[UserPublic])
def list_operators(
    _: object = Depends(require_role("cashier")),
) -> list[UserPublic]:
    return [
        to_public_user(user)
        for user in user_repository.list_users()
        if user.is_active and user.role in {"owner", "admin", "cashier"}
    ]


@app.get("/operators/current", response_model=OnDutyOperatorRecord)
def get_on_duty_operator(
    _: object = Depends(require_role("cashier")),
) -> OnDutyOperatorRecord:
    return operator_state_repository.get_current(user_repository.get_user)


@app.post("/operators/current", response_model=OnDutyOperatorRecord)
def set_on_duty_operator(
    request: OperatorSelectionRequest,
    current_user: object = Depends(require_role("cashier")),
) -> OnDutyOperatorRecord:
    operator = user_repository.get_user(request.operator_id)
    if operator is None or not operator.is_active:
        raise HTTPException(status_code=404, detail="Operator not found")
    operator_state_repository.set_current(
        operator.user_id,
        get_actor_user_id(current_user),
    )
    return operator_state_repository.get_current(user_repository.get_user)


def send_prepared_wake(
    prepared_session_id: str,
    current_user: object,
) -> PreparedSessionResponse:
    prepared = prepared_session_repository.get(prepared_session_id)
    if prepared is None:
        raise HTTPException(status_code=404, detail="Prepared session not found")
    pc = get_known_pc_or_404(prepared.pc_name)
    if not pc.mac_address:
        raise HTTPException(status_code=409, detail="This PC does not have a saved MAC address")

    command_id = str(uuid4())
    remote_command_repository.create(
        command_id=command_id,
        pc_name=pc.pc_name,
        command_type="wake_on_lan",
        payload={"mac_address": pc.mac_address, "prepared_session_id": prepared_session_id},
        requested_by_user_id=get_on_duty_actor_user_id(current_user),
    )
    try:
        send_wake_on_lan_burst(pc.mac_address)
    except OSError as error:
        remote_command_repository.mark_failed(command_id, "failed", str(error))
        failed = prepared_session_repository.update(
            prepared.model_copy(
                update={
                    "status": "failed",
                    "startup_status": "failed",
                    "failure_reason": f"Wake signal failed: {error}",
                }
            )
        )
        return PreparedSessionResponse(
            prepared_session=failed,
            message=f"Wake signal failed: {error}",
        )

    remote_command_repository.mark_completed(command_id, "Wake-on-LAN packet_sent")
    updated = session_service.mark_prepared_wake_sent(prepared_session_id)
    return PreparedSessionResponse(
        prepared_session=updated,
        message="Wake signal sent. Waiting for PC to come online.",
    )


@app.post("/sessions/prepare-startup", response_model=PreparedSessionResponse)
def prepare_startup_session(
    request: PreparedSessionStartRequest,
    current_user: object = Depends(require_role("cashier")),
) -> PreparedSessionResponse:
    mark_stale_pcs_offline()
    prepared_response = session_service.prepare_offline_session(
        request,
        get_on_duty_actor_user_id(current_user),
    )
    wake_response = send_prepared_wake(
        prepared_response.prepared_session.prepared_session_id,
        current_user,
    )
    return wake_response


@app.post("/prepared-sessions/{prepared_session_id}/retry-wake", response_model=PreparedSessionResponse)
def retry_prepared_wake(
    prepared_session_id: str,
    current_user: object = Depends(require_role("cashier")),
) -> PreparedSessionResponse:
    return send_prepared_wake(prepared_session_id, current_user)


@app.post("/prepared-sessions/{prepared_session_id}/cancel", response_model=PreparedSessionResponse)
def cancel_prepared_session(
    prepared_session_id: str,
    current_user: object = Depends(require_role("cashier")),
) -> PreparedSessionResponse:
    return session_service.cancel_prepared_session(
        prepared_session_id,
        get_on_duty_actor_user_id(current_user),
    )


@app.post("/commands/broadcast", response_model=BroadcastCommandResponse)
async def broadcast_announcement(
    request: BroadcastCommandRequest,
    current_user: object = Depends(require_role("cashier")),
) -> BroadcastCommandResponse:
    require_remote_command_permission("show_announcement", current_user)
    mark_stale_pcs_offline()
    requested_targets = {name.strip() for name in (request.pc_names or []) if name.strip()}
    targets = requested_targets or {
        pc.pc_name for pc in pc_registry.values() if pc.status == "online"
    }
    commands: list[RemoteCommandRecord] = []
    offline_targets: list[str] = []
    failed_targets: dict[str, str] = {}

    for target in sorted(targets):
        pc = pc_registry.get(target)
        if pc is None or pc.status == "offline" or not connection_manager.is_connected(target):
            offline_targets.append(target)
            continue
        try:
            response = await create_and_send_remote_command(
                target,
                "show_announcement",
                request.model_dump(mode="json"),
                current_user,
            )
            commands.append(response.command)
        except HTTPException as error:
            failed_targets[target] = str(error.detail)

    return BroadcastCommandResponse(
        commands=commands,
        offline_targets=offline_targets,
        failed_targets=failed_targets,
    )


@app.get("/remote-commands", response_model=list[RemoteCommandRecord])
def list_remote_commands(
    current_user: object = Depends(require_role("cashier")),
) -> list[RemoteCommandRecord]:
    return remote_command_repository.list_commands(
        getattr(current_user, "role", "cashier"),
        get_actor_user_id(current_user),
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
        get_on_duty_actor_user_id(current_user),
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
        get_on_duty_actor_user_id(current_user),
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
        get_on_duty_actor_user_id(current_user),
    )
    recovery_flags.pop(response.session.pc_name, None)
    return response


@app.post("/sessions/start-member", response_model=SessionCommandResponse)
async def start_member_session(
    request: MemberSessionStartRequest,
    current_user: object = Depends(require_role("cashier")),
) -> SessionCommandResponse:
    mark_stale_pcs_offline()
    response = await session_service.start_member_session(
        request.pc_name,
        request.member_username,
        request.duration_seconds,
        request.member_pin,
        get_on_duty_actor_user_id(current_user),
        request.member_id,
    )
    recovery_flags.pop(response.session.pc_name, None)
    return response


def friendly_member_login_error(error: HTTPException) -> HTTPException:
    detail = str(error.detail)
    if error.status_code in {403, 404} or "Invalid member username or PIN" in detail:
        return HTTPException(status_code=403, detail="Username or PIN is incorrect.")
    if error.status_code == 429:
        return HTTPException(status_code=429, detail="This member is temporarily locked. Please try again shortly.")
    if detail in {"Member is inactive", "Member is expired"}:
        return HTTPException(status_code=409, detail="This member account is disabled.")
    if "setup is required" in detail:
        return HTTPException(status_code=409, detail="Member account setup is required. Please ask the cashier for help.")
    if "no available time balance" in detail or "Insufficient member time balance" in detail:
        return HTTPException(
            status_code=409,
            detail="This account has no remaining play time. Please ask the cashier to add time.",
        )
    if detail == "Member already has an active session" or "active reserved session" in detail:
        return HTTPException(status_code=409, detail="This member is already using another PC.")
    if "offline" in detail or "WebSocket" in detail or "active session" in detail:
        return HTTPException(status_code=409, detail="This computer is not currently available.")
    return HTTPException(status_code=409, detail="We could not start your session. Please ask the cashier for help.")


@app.post("/client/member-login", response_model=ClientMemberLoginResponse)
async def client_member_login(
    request: ClientMemberLoginRequest,
) -> ClientMemberLoginResponse:
    mark_stale_pcs_offline()
    pc = require_registered_client_identity(
        request.pc_name,
        request.ip_address,
        request.mac_address,
    )
    try:
        response = await session_service.start_member_session(
            pc.pc_name,
            request.member_username,
            None,
            request.member_pin,
            None,
            None,
        )
    except HTTPException as error:
        raise friendly_member_login_error(error) from error

    recovery_flags.pop(response.session.pc_name, None)
    display_name = (
        response.session.member_name_snapshot
        or response.session.member_code_snapshot
        or request.member_username
    )
    return ClientMemberLoginResponse(
        session=response.session,
        member_display_name=display_name,
        remaining_seconds=response.session.total_seconds,
    )


def friendly_customer_unlock_error(error: HTTPException) -> HTTPException:
    detail = str(error.detail)
    if error.status_code == 429:
        return HTTPException(status_code=429, detail="Too many PIN attempts. Please try again shortly.")
    if error.status_code in {403, 404} or "Invalid member username or PIN" in detail:
        return HTTPException(status_code=403, detail="Incorrect PIN. Please try again.")
    return error


@app.post("/client/session-lock", response_model=ClientSessionLockResponse)
async def client_session_lock(
    request: ClientSessionLockRequest,
) -> ClientSessionLockResponse:
    """Member-initiated "Lock PC": reserves the workstation while their
    active member session keeps running unchanged. Eligibility is enforced
    independently here (session_service.engage_customer_lock), not trusted
    from the client's own presentation-only can_lock_pc check."""
    pc = require_registered_client_identity(
        request.pc_name,
        request.ip_address,
        request.mac_address,
    )
    session = session_service.engage_customer_lock(pc.pc_name, request.session_id)
    return ClientSessionLockResponse(session=session)


@app.post("/client/session-unlock", response_model=ClientSessionUnlockResponse)
async def client_session_unlock(
    request: ClientSessionUnlockRequest,
) -> ClientSessionUnlockResponse:
    pc = require_registered_client_identity(
        request.pc_name,
        request.ip_address,
        request.mac_address,
    )
    try:
        session, member_display_name = session_service.clear_customer_lock_by_pin(
            pc.pc_name,
            request.pin,
        )
    except HTTPException as error:
        raise friendly_customer_unlock_error(error) from error

    return ClientSessionUnlockResponse(session=session, member_display_name=member_display_name)


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
        get_on_duty_actor_user_id(current_user),
    )


@app.post("/sessions/{session_id}/clear-customer-lock", response_model=SessionCommandResponse)
async def clear_customer_lock(
    session_id: str,
    current_user: object = Depends(require_role("cashier")),
) -> SessionCommandResponse:
    """Cashier/operator override: clears only the temporary member lock and
    keeps the active session running. Never requires the member's PIN."""
    mark_stale_pcs_offline()
    return await session_service.clear_customer_lock_override(
        session_id,
        get_on_duty_actor_user_id(current_user),
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
        get_on_duty_actor_user_id(current_user),
    )


@app.post("/sessions/{session_id}/add-time-member", response_model=SessionCommandResponse)
async def add_member_time_to_session(
    session_id: str,
    request: MemberAddTimeRequest,
    current_user: object = Depends(require_role("cashier")),
) -> SessionCommandResponse:
    mark_stale_pcs_offline()
    return await session_service.add_member_time(
        session_id,
        request.member_id,
        request.seconds,
        get_on_duty_actor_user_id(current_user),
    )


@app.post("/sessions/{session_id}/end", response_model=SessionCommandResponse)
async def end_session(
    session_id: str,
    current_user: object = Depends(require_role("cashier")),
) -> SessionCommandResponse:
    mark_stale_pcs_offline()
    response = await session_service.end_session(
        session_id,
        get_on_duty_actor_user_id(current_user),
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
        actor_user_id=get_on_duty_actor_user_id(current_user),
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


@app.get("/rates/metered")
def get_metered_rate(
    _: object = Depends(require_role("cashier")),
) -> RateProfile:
    # Metered/Open Time customer operation is permanently retired. Existing
    # metered rate rows remain in the database for historical session
    # settlement (see compute_open_time_final_amount), but are no longer
    # exposed for active/normal use through this endpoint.
    raise HTTPException(
        status_code=410,
        detail="Metered/Open Time rate lookup is permanently retired.",
    )


@app.post("/rates/calculate-time")
def calculate_time(
    request: CalculateTimeRequest,
    _: object = Depends(require_role("cashier")),
) -> CalculatedTimeResponse:
    # Enter Amount / metered-rate time calculation is permanently retired.
    raise HTTPException(
        status_code=410,
        detail=(
            "Enter Amount sessions are no longer offered. "
            "Start a package or member-balance session instead."
        ),
    )


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

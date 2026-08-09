from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def normalize_money(value: Decimal) -> Decimal:
    if value < Decimal("0"):
        raise ValueError("Money values cannot be negative")

    return value.quantize(Decimal("0.01"))


def normalize_positive_money(value: Decimal) -> Decimal:
    normalized_value = normalize_money(value)
    if normalized_value <= Decimal("0.00"):
        raise ValueError("Amount must be greater than zero")

    return normalized_value


def validate_member_username_value(value: str | None) -> str | None:
    if value is None:
        return None
    username = value.strip()
    if not username:
        return None
    if len(username) < 3 or len(username) > 30:
        raise ValueError("Member username must be 3 to 30 characters")
    if any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for character in username):
        raise ValueError("Member username may only use letters, numbers, dots, underscores, or hyphens")
    return username


def validate_member_pin_value(value: str | None) -> str | None:
    if value is None:
        return None
    pin = value.strip()
    if not pin:
        return None
    if not pin.isdigit() or len(pin) < 4 or len(pin) > 6:
        raise ValueError("Member PIN must be 4 to 6 numeric digits")
    return pin


def normalize_mac_address(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = value.strip()
    if not cleaned:
        return None

    normalized = cleaned.replace("-", "").replace(":", "").replace(".", "")
    if len(normalized) != 12 or any(character not in "0123456789abcdefABCDEF" for character in normalized):
        raise ValueError("MAC address must use a standard 12-digit hexadecimal format")

    return ":".join(normalized[index : index + 2].upper() for index in range(0, 12, 2))


RateType = Literal["metered", "package"]
PaymentMethod = Literal["cash", "gcash", "maya", "other"]
PaymentStatus = Literal["completed", "voided"]
BillingPaymentStatus = Literal["unpaid", "partially_paid", "paid", "overpaid"]
SessionMode = Literal["prepaid", "open_time", "member_balance"]
PreparedSessionMode = Literal["prepaid", "open_time", "member_balance"]
PreparedSessionStatus = Literal[
    "prepared",
    "waking",
    "waiting_for_client",
    "starting",
    "active",
    "failed",
    "cancelled",
]
UserRole = Literal["owner", "admin", "cashier"]
OperatorAccessMethod = Literal["quick", "password"]
ShiftStatus = Literal["open", "closed"]
MemberStatus = Literal["active", "inactive"]
MembershipProductType = Literal["initial_membership", "member_topup"]
MemberTransactionType = Literal[
    "membership_purchase",
    "promo_topup",
    "purchase_refund",
    "session_reservation_created",
    "session_usage_finalized",
    "session_reservation_released",
    "session_reservation",
    "session_refund",
    "manual_adjustment",
    "expiration_adjustment",
]
RemoteCommandType = Literal[
    "shutdown",
    "restart",
    "sleep",
    "show_message",
    "show_announcement",
    "wake_on_lan",
]
RemoteCommandStatus = Literal[
    "pending",
    "received",
    "completed",
    "failed",
    "rejected",
    "timed_out",
]
MessageSeverity = Literal["info", "warning", "urgent"]
ManualEndBehavior = Literal["lock_only", "lock_and_settle"]
OverlayPosition = Literal["top-right", "top-left", "bottom-right", "bottom-left"]
SalesPeriodSetting = Literal["today", "yesterday", "this_week", "this_month"]


class PCRegistration(BaseModel):
    pc_name: str = Field(..., min_length=1)
    display_name: str | None = None
    ip_address: str = Field(..., min_length=1)
    mac_address: str | None = None


class PCHeartbeat(BaseModel):
    pc_name: str = Field(..., min_length=1)
    display_name: str | None = None
    ip_address: str = Field(..., min_length=1)


class PCRecord(BaseModel):
    pc_name: str
    ip_address: str
    status: str
    last_seen: datetime
    mac_address: str | None = None


class PCDashboardRecord(BaseModel):
    pc_name: str
    display_name: str | None = None
    area_label: str | None = None
    layout_order: int = 0
    is_vip: bool = False
    is_enabled: bool = True
    ip_address: str
    connection_status: Literal["online", "offline"]
    has_command_connection: bool = False
    power_state: Literal[
        "online",
        "powered_off",
        "shutting_down",
        "shutdown_failed",
        "waking",
        "starting",
        "connection_problem",
        "recovery_required",
    ] = "powered_off"
    last_seen: datetime
    session_status: Literal[
        "idle",
        "active",
        "grace_period",
        "shutting_down",
        "ending",
        "settlement_pending",
        "unknown",
    ]
    startup_status: PreparedSessionStatus | None = None
    prepared_session_id: str | None = None
    startup_elapsed_seconds: int | None = None
    startup_expires_at: datetime | None = None
    startup_failure_reason: str | None = None
    active_session_id: str | None = None
    started_at: datetime | None = None
    scheduled_end_at: datetime | None = None
    remaining_seconds: int | None = None
    grace_started_at: datetime | None = None
    shutdown_at: datetime | None = None
    rate_name_snapshot: str | None = None
    base_amount: Decimal = Decimal("0.00")
    additional_amount: Decimal = Decimal("0.00")
    total_amount: Decimal = Decimal("0.00")
    total_paid: Decimal = Decimal("0.00")
    balance_due: Decimal = Decimal("0.00")
    payment_status: BillingPaymentStatus = "unpaid"
    session_mode: SessionMode = "prepaid"
    actual_elapsed_seconds: int | None = None
    billed_seconds: int | None = None
    final_computed_amount: Decimal | None = None
    metered_rate_price_snapshot: Decimal | None = None
    metered_rate_duration_seconds_snapshot: int | None = None
    estimated_total_amount: Decimal | None = None
    estimated_balance_due: Decimal | None = None
    settlement_pending: bool = False
    recovery_required: bool = False
    recovery_reason: str | None = None
    member_id: str | None = None
    member_code_snapshot: str | None = None
    member_name_snapshot: str | None = None
    member_balance_before_seconds: int | None = None
    member_balance_after_seconds: int | None = None
    mac_address: str | None = None
    customer_locked: bool = False


class PCRegistrationResponse(PCRecord):
    message: str


class SettingsGeneral(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shop_display_name: str = Field(..., min_length=1, max_length=60)
    shop_subtitle: str | None = Field(default=None, max_length=80)
    pc_naming_prefix: str = Field(..., min_length=1, max_length=12)
    timezone_display: str = Field(..., min_length=1, max_length=64)

    @field_validator("shop_display_name", "shop_subtitle", "pc_naming_prefix", "timezone_display")
    @classmethod
    def clean_display_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.replace("\x00", "").replace("<", "").replace(">", "").split())
        if not cleaned:
            raise ValueError("Text value must not be blank")
        return cleaned


class SettingsSessions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expiration_grace_seconds: int = Field(..., ge=30, le=600)
    automatic_shutdown_on_expiration: bool
    reminder_thresholds_seconds: list[int] = Field(..., min_length=1, max_length=6)
    manual_end_default_behavior: ManualEndBehavior

    @field_validator("reminder_thresholds_seconds")
    @classmethod
    def validate_reminder_thresholds(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("Reminder thresholds must be positive seconds")
        if len(set(value)) != len(value):
            raise ValueError("Reminder thresholds must be unique")
        if value != sorted(value, reverse=True):
            raise ValueError("Reminder thresholds must be in descending order")
        if any(item > 24 * 60 * 60 for item in value):
            raise ValueError("Reminder thresholds must be less than or equal to 24 hours")
        return value


class SettingsCustomerClient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reminder_sounds_enabled: bool
    final_warning_sound_enabled: bool
    active_overlay_enabled: bool
    overlay_position: OverlayPosition
    customer_branding_text: str = Field(..., min_length=1, max_length=160)

    @field_validator("customer_branding_text")
    @classmethod
    def clean_customer_text(cls, value: str) -> str:
        cleaned = " ".join(value.replace("\x00", "").replace("<", "").replace(">", "").split())
        if not cleaned:
            raise ValueError("Customer text must not be blank")
        return cleaned


class SettingsWakePower(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wake_on_lan_enabled: bool
    shutdown_on_expiration_enabled: bool
    expected_shutdown_timeout_seconds: int = Field(..., ge=10, le=300)
    power_command_safety_status: str


class SettingsMembers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_member_pin_policy: Literal["optional"] = "optional"


class SettingsReports(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_sales_period: SalesPeriodSetting
    csv_timezone_display: str = Field(..., min_length=1, max_length=64)

    @field_validator("csv_timezone_display")
    @classmethod
    def clean_timezone(cls, value: str) -> str:
        cleaned = " ".join(value.replace("\x00", "").replace("<", "").replace(">", "").split())
        if not cleaned:
            raise ValueError("Timezone display must not be blank")
        return cleaned


class AdminSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    general: SettingsGeneral
    sessions: SettingsSessions
    customer_client: SettingsCustomerClient
    wake_power: SettingsWakePower
    members: SettingsMembers
    reports: SettingsReports


class AdminSettingsResponse(BaseModel):
    settings: AdminSettings
    environment_only: dict[str, str]
    read_only: dict[str, str]
    restart_required_fields: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class AdminSettingsUpdateRequest(AdminSettings):
    pass


class AdminSettingsResetRequest(BaseModel):
    category: Literal[
        "general",
        "sessions",
        "customer_client",
        "wake_power",
        "members",
        "reports",
        "all",
    ] = "all"


class ClientSafeSettings(BaseModel):
    shop_display_name: str
    shop_subtitle: str | None = None
    reminder_thresholds_seconds: list[int]
    reminder_sounds_enabled: bool
    final_warning_sound_enabled: bool
    active_overlay_enabled: bool
    overlay_position: OverlayPosition
    customer_branding_text: str


class ClientCommand(BaseModel):
    command_id: str
    type: Literal[
        "ping",
        "start_session",
        "add_time",
        "end_session",
        "shutdown",
        "restart",
        "sleep",
        "show_message",
        "show_announcement",
        "clear_customer_lock",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CommandAcknowledgement(BaseModel):
    command_id: str
    status: Literal["received", "completed", "failed"]
    message: str | None = None


class TestCommandRequest(BaseModel):
    pc_name: str = Field(..., min_length=1)


class TestCommandResponse(BaseModel):
    command: ClientCommand
    acknowledgements: list[CommandAcknowledgement]


class PCMacAddressUpdate(BaseModel):
    mac_address: str | None = None

    @field_validator("mac_address")
    @classmethod
    def validate_mac_address(cls, value: str | None) -> str | None:
        return normalize_mac_address(value)


class RemoteCommandRecord(BaseModel):
    command_id: str
    pc_name: str
    command_type: RemoteCommandType
    payload_json: str | None = None
    status: RemoteCommandStatus
    requested_by_user_id: str | None = None
    requested_by_display_name: str | None = None
    requested_at: datetime
    received_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    failure_reason: str | None = None
    client_response: str | None = None
    forced: bool = False
    force_reason: str | None = None


class RemoteCommandResponse(BaseModel):
    command: RemoteCommandRecord
    acknowledgements: list[CommandAcknowledgement] = Field(default_factory=list)


class PowerCommandRequest(BaseModel):
    force: bool = False
    reason: str | None = None
    confirm: bool = False


class MessageCommandRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=80)
    message: str = Field(..., min_length=1, max_length=500)
    severity: MessageSeverity = "info"
    duration_seconds: int | None = Field(default=None, ge=5, le=3600)
    require_acknowledgement: bool = False

    @field_validator("title", "message")
    @classmethod
    def clean_message_text(cls, value: str) -> str:
        cleaned = " ".join(value.replace("\x00", "").split())
        if not cleaned:
            raise ValueError("Message text must not be blank")
        return cleaned


class BroadcastCommandRequest(MessageCommandRequest):
    pc_names: list[str] | None = None


class BroadcastCommandResponse(BaseModel):
    commands: list[RemoteCommandRecord]
    offline_targets: list[str] = Field(default_factory=list)
    failed_targets: dict[str, str] = Field(default_factory=dict)


class WakeCommandResponse(BaseModel):
    command: RemoteCommandRecord
    message: str


class PCLayoutRecord(BaseModel):
    pc_name: str
    display_name: str
    area_label: str
    layout_order: int
    is_vip: bool = False
    is_enabled: bool = True
    mac_address: str | None = None
    updated_at: datetime


class PCLayoutUpdate(BaseModel):
    display_name: str = Field(..., min_length=1)
    area_label: str = Field(..., min_length=1)
    layout_order: int = Field(..., ge=0)
    is_vip: bool = False
    is_enabled: bool = True
    mac_address: str | None = None

    @field_validator("mac_address")
    @classmethod
    def validate_mac_address(cls, value: str | None) -> str | None:
        return normalize_mac_address(value)


class PreparedSessionRecord(BaseModel):
    prepared_session_id: str
    pc_name: str
    status: PreparedSessionStatus
    session_mode: PreparedSessionMode
    rate_id: str | None = None
    rate_name_snapshot: str | None = None
    duration_seconds: int | None = None
    amount: Decimal | None = None
    payment_json: str | None = None
    member_id: str | None = None
    member_pin: str | None = None
    member_name_snapshot: str | None = None
    member_open_play: bool = False
    prepared_by_user_id: str | None = None
    prepared_operator_id: str | None = None
    prepared_at: datetime
    wake_requested_at: datetime | None = None
    wake_status: str | None = None
    startup_status: PreparedSessionStatus
    failure_reason: str | None = None
    expires_at: datetime
    activated_session_id: str | None = None
    activated_at: datetime | None = None
    cancelled_by_user_id: str | None = None
    cancelled_at: datetime | None = None


class PreparedSessionStartRequest(BaseModel):
    pc_name: str = Field(..., min_length=1)
    session_mode: PreparedSessionMode = "prepaid"
    duration_seconds: int | None = Field(default=None, gt=0)
    rate_id: str | None = None
    amount: Decimal | None = None
    payment: "PaymentCreateRequest | None" = None
    member_id: str | None = None
    member_username: str | None = None
    member_pin: str | None = None
    operator_id: str | None = None

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal | None) -> Decimal | None:
        return normalize_positive_money(value) if value is not None else None

    @field_validator("member_username")
    @classmethod
    def validate_member_username(cls, value: str | None) -> str | None:
        return validate_member_username_value(value)

    @field_validator("member_pin")
    @classmethod
    def validate_member_pin(cls, value: str | None) -> str | None:
        return validate_member_pin_value(value)


class PreparedSessionResponse(BaseModel):
    prepared_session: PreparedSessionRecord
    message: str


class PreparedSessionActivationResponse(BaseModel):
    prepared_session: PreparedSessionRecord
    session: SessionRecord | None = None
    command: ClientCommand | None = None
    acknowledgements: list[CommandAcknowledgement] = Field(default_factory=list)


class OperatorSelectionRequest(BaseModel):
    operator_id: str = Field(..., min_length=1)


class OperatorAccessRecord(BaseModel):
    operator_id: str
    display_name: str
    password_required: bool
    user: "UserPublic | None" = None


class QuickOperatorLoginRequest(BaseModel):
    operator_id: str = Field(..., min_length=1)


class AdminOperatorLoginRequest(BaseModel):
    password: str = Field(..., min_length=1)


class PasswordOperatorLoginRequest(BaseModel):
    operator_id: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class OnDutyOperatorRecord(BaseModel):
    operator: "UserPublic | None"
    selected_by_user_id: str | None = None
    selected_at: datetime | None = None


SessionStatus = Literal[
    "active",
    "grace_period",
    "shutting_down",
    "ending",
    "settlement_pending",
    "completed",
    "ended_manually",
    "cancelled",
    "failed",
]
SessionEventType = Literal[
    "session_started",
    "time_added",
    "session_completed",
    "session_ended_manually",
    "session_cancelled",
    "command_failed",
    "payment_recorded",
    "payment_voided",
    "prepaid_start_paid",
    "prepaid_add_time_paid",
    "open_time_started",
    "open_time_ended",
    "open_time_settled",
    "payment_action_review_required",
    "recovery_force_end",
    "session_prepared",
    "wake_signal_sent",
    "client_connected",
    "session_activation_started",
    "session_activated",
    "startup_failed",
    "prepared_session_cancelled",
    "automatic_reminder_sent",
    "time_expired",
    "operator_changed",
    "shutdown_grace_started",
    "time_added_during_grace",
    "automatic_shutdown_cancelled",
    "shutdown_grace_completed",
    "automatic_shutdown_initiated",
    "shutdown_command_acknowledged",
    "shutdown_command_invoked",
    "automatic_shutdown_failed",
    "session_moved_to_settlement",
    "expiration_final_warning_reached",
    "customer_lock_engaged",
    "customer_lock_cleared",
]


class SessionRecord(BaseModel):
    session_id: str
    pc_name: str
    status: SessionStatus
    started_at: datetime
    scheduled_end_at: datetime
    ended_at: datetime | None = None
    original_seconds: int
    added_seconds: int = 0
    total_seconds: int
    amount_paid: float | None = None
    ended_reason: str | None = None
    created_at: datetime
    rate_id: str | None = None
    rate_name_snapshot: str | None = None
    base_amount: Decimal = Decimal("0.00")
    additional_amount: Decimal = Decimal("0.00")
    total_amount: Decimal = Decimal("0.00")
    total_paid: Decimal = Decimal("0.00")
    balance_due: Decimal = Decimal("0.00")
    payment_status: BillingPaymentStatus = "unpaid"
    session_mode: SessionMode = "prepaid"
    actual_elapsed_seconds: int | None = None
    billed_seconds: int | None = None
    final_computed_amount: Decimal | None = None
    metered_rate_price_snapshot: Decimal | None = None
    metered_rate_duration_seconds_snapshot: int | None = None
    estimated_total_amount: Decimal | None = None
    estimated_balance_due: Decimal | None = None
    settlement_pending: bool = False
    created_by_user_id: str | None = None
    ended_by_user_id: str | None = None
    member_id: str | None = None
    member_code_snapshot: str | None = None
    member_name_snapshot: str | None = None
    member_balance_before_seconds: int | None = None
    member_balance_after_seconds: int | None = None
    grace_started_at: datetime | None = None
    shutdown_at: datetime | None = None
    customer_locked: bool = False


class SessionEventRecord(BaseModel):
    event_id: str
    session_id: str
    event_type: SessionEventType
    seconds: int | None = None
    message: str | None = None
    created_at: datetime
    rate_id: str | None = None
    rate_name_snapshot: str | None = None
    amount: Decimal | None = None
    payment_id: str | None = None
    payment_method: PaymentMethod | None = None
    performed_by_user_id: str | None = None


class StartSessionRequest(BaseModel):
    pc_name: str = Field(..., min_length=1)
    duration_seconds: int | None = Field(default=None, gt=0)
    rate_id: str | None = None
    amount: Decimal | None = None

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal | None) -> Decimal | None:
        return normalize_positive_money(value) if value is not None else None


class PrepaidStartSessionRequest(StartSessionRequest):
    payment: "PaymentCreateRequest"


class AddTimeRequest(BaseModel):
    seconds: int | None = Field(default=None, gt=0)
    rate_id: str | None = None
    amount: Decimal | None = None

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal | None) -> Decimal | None:
        return normalize_positive_money(value) if value is not None else None


class PrepaidAddTimeRequest(AddTimeRequest):
    payment: "PaymentCreateRequest"


class MemberSessionStartRequest(BaseModel):
    pc_name: str = Field(..., min_length=1)
    member_username: str | None = None
    member_id: str | None = None
    duration_seconds: int | None = Field(default=None, gt=0)
    member_pin: str | None = None

    @field_validator("member_username")
    @classmethod
    def validate_member_username(cls, value: str | None) -> str | None:
        return validate_member_username_value(value)

    @field_validator("member_pin")
    @classmethod
    def validate_member_pin(cls, value: str | None) -> str | None:
        return validate_member_pin_value(value)

    @model_validator(mode="after")
    def require_member_identity(self) -> "MemberSessionStartRequest":
        if not self.member_username and not self.member_id:
            raise ValueError("Member username is required")
        return self


class ClientMemberLoginRequest(BaseModel):
    pc_name: str = Field(..., min_length=1)
    ip_address: str = Field(..., min_length=1)
    mac_address: str | None = None
    member_username: str
    member_pin: str

    @field_validator("member_username")
    @classmethod
    def validate_member_username(cls, value: str) -> str:
        username = validate_member_username_value(value)
        if username is None:
            raise ValueError("Member username is required")
        return username

    @field_validator("member_pin")
    @classmethod
    def validate_member_pin(cls, value: str) -> str:
        pin = validate_member_pin_value(value)
        if pin is None:
            raise ValueError("Member PIN is required")
        return pin


class ClientMemberLoginResponse(BaseModel):
    session: SessionRecord
    member_display_name: str
    remaining_seconds: int
    rounding_rule: str = "Balances are tracked and deducted in whole seconds."


class ClientSessionLockRequest(BaseModel):
    pc_name: str = Field(..., min_length=1)
    ip_address: str = Field(..., min_length=1)
    mac_address: str | None = None
    session_id: str = Field(..., min_length=1)


class ClientSessionLockResponse(BaseModel):
    session: SessionRecord


class ClientSessionUnlockRequest(BaseModel):
    pc_name: str = Field(..., min_length=1)
    ip_address: str = Field(..., min_length=1)
    mac_address: str | None = None
    pin: str

    @field_validator("pin")
    @classmethod
    def validate_pin(cls, value: str) -> str:
        pin = validate_member_pin_value(value)
        if pin is None:
            raise ValueError("PIN is required")
        return pin


class ClientSessionUnlockResponse(BaseModel):
    session: SessionRecord
    member_display_name: str


class MemberAddTimeRequest(BaseModel):
    member_id: str = Field(..., min_length=1)
    seconds: int = Field(..., gt=0)


class OpenTimeStartRequest(BaseModel):
    pc_name: str = Field(..., min_length=1)


class OpenTimeEndResponse(BaseModel):
    session: SessionRecord
    command: ClientCommand
    acknowledgements: list[CommandAcknowledgement]
    billing: "BillingSummary"


class SessionCommandResponse(BaseModel):
    session: SessionRecord
    command: ClientCommand
    acknowledgements: list[CommandAcknowledgement]


class ForceEndSessionResponse(BaseModel):
    session: SessionRecord
    command: ClientCommand | None = None
    acknowledgements: list[CommandAcknowledgement] = Field(default_factory=list)
    forced: bool
    reason: str


class ClientEvent(BaseModel):
    event_type: Literal[
        "session_finished",
        "reconciliation_unresolved",
        "expiration_grace_started",
        "expiration_shutdown_started",
        "expiration_shutdown_invoked",
        "expiration_shutdown_failed",
        "expiration_final_warning",
    ]
    session_id: str
    message: str | None = None
    reason: str | None = None


class SessionStateSummary(BaseModel):
    session_id: str
    status: SessionStatus
    started_at: datetime
    scheduled_end_at: datetime
    grace_started_at: datetime | None = None
    shutdown_at: datetime | None = None
    total_seconds: int
    session_mode: SessionMode = "prepaid"
    member_code_snapshot: str | None = None
    member_name_snapshot: str | None = None
    customer_locked: bool = False


class PCSessionStateResponse(BaseModel):
    pc_name: str
    has_active_session: bool
    session: SessionStateSummary | None
    server_time: datetime


class RateProfile(BaseModel):
    rate_id: str
    name: str
    duration_seconds: int
    price: Decimal
    rate_type: RateType = "package"
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class RateProfileCreate(BaseModel):
    name: str = Field(..., min_length=1)
    duration_seconds: int = Field(..., gt=0)
    price: Decimal
    rate_type: RateType = "package"
    sort_order: int = 0
    is_active: bool = True

    @field_validator("price")
    @classmethod
    def validate_price(cls, value: Decimal) -> Decimal:
        return normalize_money(value)


class RateProfileUpdate(BaseModel):
    name: str = Field(..., min_length=1)
    duration_seconds: int = Field(..., gt=0)
    price: Decimal
    rate_type: RateType = "package"
    sort_order: int = 0

    @field_validator("price")
    @classmethod
    def validate_price(cls, value: Decimal) -> Decimal:
        return normalize_money(value)


class RateStatusUpdate(BaseModel):
    is_active: bool


class CalculateTimeRequest(BaseModel):
    amount: Decimal

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        return normalize_positive_money(value)


class CalculatedTimeResponse(BaseModel):
    amount: Decimal
    duration_seconds: int
    rate_name: str
    rate_snapshot: str


class PaymentRecord(BaseModel):
    payment_id: str
    session_id: str | None = None
    amount: Decimal
    payment_method: PaymentMethod
    cash_received: Decimal | None = None
    change_given: Decimal | None = None
    reference_number: str | None = None
    notes: str | None = None
    action_type: Literal["session_start", "add_time", "settlement", "manual"] = "manual"
    related_command_id: str | None = None
    review_required: bool = False
    shift_id: str | None = None
    recorded_by_user_id: str | None = None
    idempotency_key: str | None = None
    voided_by_user_id: str | None = None
    voided_at: datetime | None = None
    void_reason: str | None = None
    status: PaymentStatus
    created_at: datetime


class PaymentCreateRequest(BaseModel):
    payment_method: PaymentMethod
    amount: Decimal
    cash_received: Decimal | None = None
    reference_number: str | None = None
    notes: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        return normalize_positive_money(value)

    @field_validator("cash_received")
    @classmethod
    def validate_cash_received(cls, value: Decimal | None) -> Decimal | None:
        return normalize_positive_money(value) if value is not None else None


class VoidPaymentRequest(BaseModel):
    reason: str = Field(..., min_length=1)
    notes: str | None = None


class BillingSummary(BaseModel):
    session: SessionRecord
    base_amount: Decimal
    additional_amount: Decimal
    total_amount: Decimal
    total_paid: Decimal
    balance_due: Decimal
    payment_status: BillingPaymentStatus
    payments: list[PaymentRecord]


class ShiftOpenRequest(BaseModel):
    opening_cash: Decimal

    @field_validator("opening_cash")
    @classmethod
    def validate_opening_cash(cls, value: Decimal) -> Decimal:
        return normalize_money(value)


class ShiftCloseRequest(BaseModel):
    actual_cash: Decimal
    closing_notes: str | None = None
    force_close_reason: str | None = None

    @field_validator("actual_cash")
    @classmethod
    def validate_actual_cash(cls, value: Decimal) -> Decimal:
        return normalize_money(value)


class ShiftTotals(BaseModel):
    expected_cash: Decimal
    gross_session_sales: Decimal
    cash_payments: Decimal
    gcash_payments: Decimal
    maya_payments: Decimal
    other_payments: Decimal
    voided_payments: Decimal
    payment_count: int
    sessions_handled: int
    outstanding_unsettled_balance: Decimal
    review_required_payments: int
    settlement_pending_sessions: int


class CashierShiftRecord(BaseModel):
    shift_id: str
    user_id: str
    cashier_name: str | None = None
    status: ShiftStatus
    opened_at: datetime
    closed_at: datetime | None = None
    opening_cash: Decimal
    expected_cash: Decimal | None = None
    actual_cash: Decimal | None = None
    difference: Decimal | None = None
    closing_notes: str | None = None
    force_close_reason: str | None = None
    opened_by_user_id: str
    closed_by_user_id: str | None = None
    gross_session_sales: Decimal = Decimal("0.00")
    cash_payments: Decimal = Decimal("0.00")
    gcash_payments: Decimal = Decimal("0.00")
    maya_payments: Decimal = Decimal("0.00")
    other_payments: Decimal = Decimal("0.00")
    voided_payments: Decimal = Decimal("0.00")
    payment_count: int = 0
    sessions_handled: int = 0
    outstanding_unsettled_balance: Decimal = Decimal("0.00")
    review_required_payments: int = 0
    settlement_pending_sessions: int = 0


class CashierShiftDetail(BaseModel):
    shift: CashierShiftRecord
    totals: ShiftTotals
    payments: list[PaymentRecord] = Field(default_factory=list)


class MemberRecord(BaseModel):
    member_id: str
    member_code: str
    username: str | None = None
    display_name: str | None = None
    full_name: str
    phone_number: str | None = None
    has_pin: bool = False
    requires_account_setup: bool = False
    status: MemberStatus
    time_balance_seconds: int
    joined_at: datetime
    expires_at: datetime | None = None
    created_by_user_id: str | None = None
    created_at: datetime
    updated_at: datetime


class MemberCreateRequest(BaseModel):
    username: str
    display_name: str | None = None
    pin: str
    confirm_pin: str
    phone_number: str | None = None
    expires_at: datetime | None = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        username = validate_member_username_value(value)
        if username is None:
            raise ValueError("Member username is required")
        return username

    @field_validator("pin", "confirm_pin")
    @classmethod
    def validate_pin(cls, value: str) -> str:
        pin = validate_member_pin_value(value)
        if pin is None:
            raise ValueError("Member PIN is required")
        return pin

    @model_validator(mode="after")
    def validate_pin_confirmation(self) -> "MemberCreateRequest":
        if self.pin != self.confirm_pin:
            raise ValueError("Member PIN confirmation does not match")
        return self


class MemberUpdateRequest(BaseModel):
    username: str
    display_name: str | None = None
    phone_number: str | None = None
    expires_at: datetime | None = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        username = validate_member_username_value(value)
        if username is None:
            raise ValueError("Member username is required")
        return username


class MemberStatusUpdate(BaseModel):
    status: MemberStatus


class MemberPinResetRequest(BaseModel):
    pin: str
    confirm_pin: str

    @field_validator("pin", "confirm_pin")
    @classmethod
    def validate_pin(cls, value: str) -> str:
        pin = validate_member_pin_value(value)
        if pin is None:
            raise ValueError("Member PIN is required")
        return pin

    @model_validator(mode="after")
    def validate_pin_confirmation(self) -> "MemberPinResetRequest":
        if self.pin != self.confirm_pin:
            raise ValueError("Member PIN confirmation does not match")
        return self


class MembershipProductRecord(BaseModel):
    product_id: str
    name: str
    price: Decimal
    time_seconds: int
    product_type: MembershipProductType
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class MembershipProductCreate(BaseModel):
    name: str = Field(..., min_length=1)
    price: Decimal
    time_seconds: int = Field(..., gt=0)
    product_type: MembershipProductType
    sort_order: int = 0
    is_active: bool = True

    @field_validator("price")
    @classmethod
    def validate_price(cls, value: Decimal) -> Decimal:
        return normalize_positive_money(value)


class MembershipProductUpdate(BaseModel):
    name: str = Field(..., min_length=1)
    price: Decimal
    time_seconds: int = Field(..., gt=0)
    product_type: MembershipProductType
    sort_order: int = 0

    @field_validator("price")
    @classmethod
    def validate_price(cls, value: Decimal) -> Decimal:
        return normalize_positive_money(value)


class MemberPurchaseRequest(BaseModel):
    product_id: str = Field(..., min_length=1)
    payment_method: PaymentMethod
    cash_received: Decimal | None = None
    reference_number: str | None = None
    notes: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)

    @field_validator("cash_received")
    @classmethod
    def validate_cash_received(cls, value: Decimal | None) -> Decimal | None:
        return normalize_positive_money(value) if value is not None else None


class MemberTimeTransactionRecord(BaseModel):
    transaction_id: str
    member_id: str
    transaction_type: MemberTransactionType
    seconds_delta: int
    balance_before_seconds: int
    balance_after_seconds: int
    amount_paid: Decimal | None = None
    related_session_id: str | None = None
    related_payment_id: str | None = None
    notes: str | None = None
    performed_by_user_id: str | None = None
    created_at: datetime


class MemberPurchaseResponse(BaseModel):
    member: MemberRecord
    product: MembershipProductRecord
    payment: PaymentRecord
    transaction: MemberTimeTransactionRecord


class MemberPurchaseRefundRequest(BaseModel):
    reason: Literal[
        "Duplicate transaction",
        "Wrong member",
        "Wrong package",
        "Wrong amount",
        "Customer cancellation",
        "Other",
    ]
    notes: str | None = None

    @model_validator(mode="after")
    def validate_notes(self) -> "MemberPurchaseRefundRequest":
        if self.reason == "Other" and not (self.notes or "").strip():
            raise ValueError("Notes are required when reason is Other")
        return self


class MemberPurchaseRefundResponse(BaseModel):
    member: MemberRecord
    payment: PaymentRecord
    reversal_transaction: MemberTimeTransactionRecord


class UserPublic(BaseModel):
    user_id: str
    username: str
    display_name: str
    role: UserRole


class UserRecord(UserPublic):
    password_hash: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None


class UserAdminRecord(UserPublic):
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None


class OperatorManagementRecord(BaseModel):
    operator_id: str
    username: str
    display_name: str
    access_method: OperatorAccessMethod
    password_required: bool
    role: UserRole
    is_active: bool
    is_protected: bool = False
    active_session_count: int = 0
    archived_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_login_at: datetime | None = None


class OperatorCreateRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=40)
    display_name: str = Field(..., min_length=1, max_length=80)
    access_method: OperatorAccessMethod
    password: str | None = Field(default=None, min_length=8)
    is_active: bool = True


class OperatorUpdateRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=40)
    display_name: str = Field(..., min_length=1, max_length=80)
    access_method: OperatorAccessMethod
    is_active: bool = True


class OperatorPasswordResetRequest(BaseModel):
    password: str = Field(..., min_length=8)


class OperatorActivityRecord(BaseModel):
    timestamp: datetime
    operator_id: str | None = None
    operator_name: str
    action: str
    category: str
    pc_name: str | None = None
    customer: str | None = None
    amount: Decimal | None = None
    payment_method: str | None = None
    result: str


SalesPeriod = Literal["today", "yesterday", "this_week", "this_month", "custom"]


class SalesSummary(BaseModel):
    total_sales: Decimal = Decimal("0.00")
    gross_sales: Decimal = Decimal("0.00")
    refund_total: Decimal = Decimal("0.00")
    net_sales: Decimal = Decimal("0.00")
    cash_received_total: Decimal = Decimal("0.00")
    change_given_total: Decimal = Decimal("0.00")
    non_cash_collected: Decimal = Decimal("0.00")
    cash_total: Decimal = Decimal("0.00")
    gcash_total: Decimal = Decimal("0.00")
    maya_total: Decimal = Decimal("0.00")
    other_total: Decimal = Decimal("0.00")
    payment_count: int = 0
    outstanding_amount: Decimal = Decimal("0.00")
    session_revenue: Decimal = Decimal("0.00")
    member_purchase_revenue: Decimal = Decimal("0.00")
    other_revenue: Decimal = Decimal("0.00")
    average_transaction_value: Decimal = Decimal("0.00")


class SalesPaymentMethodBreakdown(BaseModel):
    payment_method: PaymentMethod
    amount: Decimal = Decimal("0.00")
    payment_count: int = 0


class SalesTypeBreakdown(BaseModel):
    sales_type: Literal["pc_session", "member_purchase", "other"]
    label: str
    amount: Decimal = Decimal("0.00")
    payment_count: int = 0


class SalesOperatorBreakdown(BaseModel):
    operator_id: str | None = None
    operator_name: str
    total_amount: Decimal = Decimal("0.00")
    payment_count: int = 0
    cash_total: Decimal = Decimal("0.00")
    gcash_total: Decimal = Decimal("0.00")
    maya_total: Decimal = Decimal("0.00")
    other_total: Decimal = Decimal("0.00")


class SalesOperatorTransaction(BaseModel):
    payment_id: str
    timestamp: datetime
    customer: str | None = None
    pc_name: str | None = None
    sales_type: Literal["pc_session", "member_purchase", "other"]
    payment_method: PaymentMethod
    amount_due: Decimal = Decimal("0.00")
    cash_received: Decimal | None = None
    change_given: Decimal | None = None
    reference_number: str | None = None
    status: PaymentStatus
    recorded_by_operator: str
    voided_by_operator: str | None = None
    voided_at: datetime | None = None
    void_reason: str | None = None
    related_reference: str | None = None


class SalesOperatorCollection(BaseModel):
    operator_id: str | None = None
    operator_name: str
    transaction_count: int = 0
    gross_sales: Decimal = Decimal("0.00")
    refunds_voids: Decimal = Decimal("0.00")
    net_sales: Decimal = Decimal("0.00")
    cash_received: Decimal = Decimal("0.00")
    change_given: Decimal = Decimal("0.00")
    expected_cash: Decimal = Decimal("0.00")
    non_cash_collected: Decimal = Decimal("0.00")
    outstanding: Decimal = Decimal("0.00")
    transactions: list[SalesOperatorTransaction] = Field(default_factory=list)


class SalesPCBreakdown(BaseModel):
    pc_name: str
    total_amount: Decimal = Decimal("0.00")
    payment_count: int = 0
    paid_session_count: int = 0
    billable_seconds: int | None = None


class SalesTimeseriesPoint(BaseModel):
    label: str
    start_at: datetime
    amount: Decimal = Decimal("0.00")
    payment_count: int = 0


class SalesRecentPayment(BaseModel):
    payment_id: str
    timestamp: datetime
    operator_name: str
    customer: str | None = None
    pc_name: str | None = None
    sales_type: Literal["pc_session", "member_purchase", "other"]
    payment_method: PaymentMethod
    amount: Decimal
    status: PaymentStatus
    related_reference: str | None = None


class SalesOutstandingSettlement(BaseModel):
    session_id: str
    pc_name: str
    customer: str | None = None
    session_end_time: datetime | None = None
    amount_due: Decimal
    status: SessionStatus
    payment_status: BillingPaymentStatus


class SalesReport(BaseModel):
    period: SalesPeriod
    start_at: datetime
    end_at: datetime
    grouping: Literal["hour", "day"]
    summary: SalesSummary
    payment_methods: list[SalesPaymentMethodBreakdown] = Field(default_factory=list)
    sales_types: list[SalesTypeBreakdown] = Field(default_factory=list)
    operators: list[SalesOperatorBreakdown] = Field(default_factory=list)
    operator_collections: list[SalesOperatorCollection] = Field(default_factory=list)
    pcs: list[SalesPCBreakdown] = Field(default_factory=list)
    timeseries: list[SalesTimeseriesPoint] = Field(default_factory=list)
    recent_payments: list[SalesRecentPayment] = Field(default_factory=list)
    outstanding_settlements: list[SalesOutstandingSettlement] = Field(default_factory=list)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1)
    password: str = Field(..., min_length=8)
    role: UserRole = "cashier"


class UserUpdateRequest(BaseModel):
    username: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1)
    role: UserRole


class UserStatusUpdate(BaseModel):
    is_active: bool


class PasswordResetRequest(BaseModel):
    password: str = Field(..., min_length=8)

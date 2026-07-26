from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def normalize_money(value: Decimal) -> Decimal:
    if value < Decimal("0"):
        raise ValueError("Money values cannot be negative")

    return value.quantize(Decimal("0.01"))


def normalize_positive_money(value: Decimal) -> Decimal:
    normalized_value = normalize_money(value)
    if normalized_value <= Decimal("0.00"):
        raise ValueError("Amount must be greater than zero")

    return normalized_value


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
UserRole = Literal["owner", "admin", "cashier"]
ShiftStatus = Literal["open", "closed"]
MemberStatus = Literal["active", "inactive"]
MembershipProductType = Literal["initial_membership", "member_topup"]
MemberTransactionType = Literal[
    "membership_purchase",
    "promo_topup",
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


class PCRegistration(BaseModel):
    pc_name: str = Field(..., min_length=1)
    ip_address: str = Field(..., min_length=1)
    mac_address: str | None = None


class PCHeartbeat(BaseModel):
    pc_name: str = Field(..., min_length=1)
    ip_address: str = Field(..., min_length=1)


class PCRecord(BaseModel):
    pc_name: str
    ip_address: str
    status: str
    last_seen: datetime
    mac_address: str | None = None


class PCDashboardRecord(BaseModel):
    pc_name: str
    ip_address: str
    connection_status: Literal["online", "offline"]
    has_command_connection: bool = False
    last_seen: datetime
    session_status: Literal["idle", "active", "ending", "settlement_pending", "unknown"]
    active_session_id: str | None = None
    started_at: datetime | None = None
    scheduled_end_at: datetime | None = None
    remaining_seconds: int | None = None
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


class PCRegistrationResponse(PCRecord):
    message: str


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


SessionStatus = Literal[
    "active",
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
    member_id: str = Field(..., min_length=1)
    duration_seconds: int | None = Field(default=None, gt=0)
    member_pin: str | None = None

    @field_validator("member_pin")
    @classmethod
    def validate_member_pin(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not value.isdigit() or len(value) < 4 or len(value) > 6:
            raise ValueError("Member PIN must be 4 to 6 numeric digits")
        return value


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
    event_type: Literal["session_finished", "reconciliation_unresolved"]
    session_id: str
    message: str | None = None
    reason: str | None = None


class SessionStateSummary(BaseModel):
    session_id: str
    status: SessionStatus
    started_at: datetime
    scheduled_end_at: datetime
    total_seconds: int
    session_mode: SessionMode = "prepaid"


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
    full_name: str
    phone_number: str | None = None
    has_pin: bool = False
    status: MemberStatus
    time_balance_seconds: int
    joined_at: datetime
    expires_at: datetime | None = None
    created_by_user_id: str | None = None
    created_at: datetime
    updated_at: datetime


class MemberCreateRequest(BaseModel):
    member_code: str = Field(..., min_length=1)
    full_name: str = Field(..., min_length=1)
    phone_number: str | None = None
    expires_at: datetime | None = None


class MemberUpdateRequest(BaseModel):
    member_code: str = Field(..., min_length=1)
    full_name: str = Field(..., min_length=1)
    phone_number: str | None = None
    expires_at: datetime | None = None


class MemberStatusUpdate(BaseModel):
    status: MemberStatus


class MemberPinResetRequest(BaseModel):
    pin: str | None = None

    @field_validator("pin")
    @classmethod
    def validate_pin(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not value.isdigit() or len(value) < 4 or len(value) > 6:
            raise ValueError("Member PIN must be 4 to 6 numeric digits")
        return value


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

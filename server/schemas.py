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


RateType = Literal["metered", "package"]
PaymentMethod = Literal["cash", "gcash", "maya", "other"]
PaymentStatus = Literal["completed", "voided"]
BillingPaymentStatus = Literal["unpaid", "partially_paid", "paid", "overpaid"]
SessionMode = Literal["prepaid", "open_time"]
UserRole = Literal["owner", "admin", "cashier"]


class PCRegistration(BaseModel):
    pc_name: str = Field(..., min_length=1)
    ip_address: str = Field(..., min_length=1)


class PCHeartbeat(BaseModel):
    pc_name: str = Field(..., min_length=1)
    ip_address: str = Field(..., min_length=1)


class PCRecord(BaseModel):
    pc_name: str
    ip_address: str
    status: str
    last_seen: datetime


class PCDashboardRecord(BaseModel):
    pc_name: str
    ip_address: str
    connection_status: Literal["online", "offline"]
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


class PCRegistrationResponse(PCRecord):
    message: str


class ClientCommand(BaseModel):
    command_id: str
    type: Literal["ping", "start_session", "add_time", "end_session"]
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
    session_id: str
    amount: Decimal
    payment_method: PaymentMethod
    cash_received: Decimal | None = None
    change_given: Decimal | None = None
    reference_number: str | None = None
    notes: str | None = None
    action_type: Literal["session_start", "add_time", "settlement", "manual"] = "manual"
    related_command_id: str | None = None
    review_required: bool = False
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

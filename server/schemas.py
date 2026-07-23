from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def normalize_money(value: Decimal) -> Decimal:
    if value < Decimal("0"):
        raise ValueError("Money values cannot be negative")

    return value.quantize(Decimal("0.01"))


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
    session_status: Literal["idle", "active", "unknown"]
    active_session_id: str | None = None
    scheduled_end_at: datetime | None = None
    remaining_seconds: int | None = None


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


class StartSessionRequest(BaseModel):
    pc_name: str = Field(..., min_length=1)
    duration_seconds: int | None = Field(default=None, gt=0)
    rate_id: str | None = None


class AddTimeRequest(BaseModel):
    seconds: int | None = Field(default=None, gt=0)
    rate_id: str | None = None


class SessionCommandResponse(BaseModel):
    session: SessionRecord
    command: ClientCommand
    acknowledgements: list[CommandAcknowledgement]


class ClientEvent(BaseModel):
    event_type: Literal["session_finished"]
    session_id: str
    message: str | None = None


class SessionStateSummary(BaseModel):
    session_id: str
    status: SessionStatus
    scheduled_end_at: datetime
    total_seconds: int


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
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class RateProfileCreate(BaseModel):
    name: str = Field(..., min_length=1)
    duration_seconds: int = Field(..., gt=0)
    price: Decimal
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
    sort_order: int = 0

    @field_validator("price")
    @classmethod
    def validate_price(cls, value: Decimal) -> Decimal:
        return normalize_money(value)


class RateStatusUpdate(BaseModel):
    is_active: bool

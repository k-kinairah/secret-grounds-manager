import asyncio
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING
from uuid import uuid4

from fastapi import HTTPException

from server.connection_manager import ClientConnectionManager
from server.billing_service import BillingService
from server.rate_calculator import calculate_metered_time
from server.rate_repository import RateRepository
from server.schemas import (
    ClientCommand,
    CommandAcknowledgement,
    PCRecord,
    SessionCommandResponse,
    SessionEventRecord,
    SessionRecord,
    PaymentCreateRequest,
)
from server.session_repository import SessionRepository
from server.time_utils import current_utc_time


class SessionService:
    """Production-facing session rules built on SQLite plus live WebSockets."""

    def __init__(
        self,
        repository: SessionRepository,
        rate_repository: RateRepository,
        connection_manager: ClientConnectionManager,
        pc_registry: dict[str, PCRecord],
        command_timeout_seconds: int,
        billing_service: BillingService | None = None,
    ) -> None:
        self.repository = repository
        self.rate_repository = rate_repository
        self.connection_manager = connection_manager
        self.pc_registry = pc_registry
        self.command_timeout_seconds = command_timeout_seconds
        self.billing_service = billing_service

    def require_available_client(self, pc_name: str) -> PCRecord:
        pc = self.pc_registry.get(pc_name)
        if pc is None or pc.status == "offline":
            raise HTTPException(status_code=409, detail=f"{pc_name} is offline")

        if not self.connection_manager.is_connected(pc_name):
            raise HTTPException(
                status_code=409,
                detail=f"{pc_name} has no active WebSocket connection",
            )

        return pc

    def create_event(
        self,
        session_id: str,
        event_type: str,
        seconds: int | None = None,
        message: str | None = None,
        rate_id: str | None = None,
        rate_name_snapshot: str | None = None,
        amount: Decimal | None = None,
        payment_id: str | None = None,
        payment_method: str | None = None,
    ) -> SessionEventRecord:
        event = SessionEventRecord(
            event_id=str(uuid4()),
            session_id=session_id,
            event_type=event_type,
            seconds=seconds,
            message=message,
            created_at=current_utc_time(),
            rate_id=rate_id,
            rate_name_snapshot=rate_name_snapshot,
            amount=amount,
            payment_id=payment_id,
            payment_method=payment_method,  # type: ignore[arg-type]
        )
        return self.repository.add_event(event)

    def resolve_session_package(
        self,
        rate_id: str | None,
        custom_seconds: int | None,
        amount: Decimal | None,
        custom_field_name: str,
    ) -> tuple[int, str | None, str | None, Decimal]:
        selected_inputs = [
            rate_id is not None,
            custom_seconds is not None,
            amount is not None,
        ]
        if sum(1 for is_selected in selected_inputs if is_selected) != 1:
            raise HTTPException(
                status_code=400,
                detail=f"Use exactly one of rate_id, amount, or {custom_field_name}",
            )

        if rate_id:
            rate = self.rate_repository.get_rate(rate_id)
            if rate is None or not rate.is_active:
                raise HTTPException(status_code=409, detail="Rate is inactive or missing")
            if rate.rate_type != "package":
                raise HTTPException(status_code=400, detail="rate_id must reference a package")
            return rate.duration_seconds, rate.rate_id, rate.name, rate.price

        if amount is not None:
            metered_rate = self.rate_repository.get_active_metered_rate()
            if metered_rate is None:
                raise HTTPException(status_code=409, detail="Active metered rate is missing")

            calculated = calculate_metered_time(amount, metered_rate)
            return (
                calculated.duration_seconds,
                metered_rate.rate_id,
                f"{calculated.rate_name} ({calculated.rate_snapshot})",
                calculated.amount,
            )

        if custom_seconds is None or custom_seconds <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"{custom_field_name} must be a positive integer",
            )

        return custom_seconds, None, None, Decimal("0.00")

    def create_command(
        self,
        command_type: str,
        payload: dict[str, object],
    ) -> ClientCommand:
        return ClientCommand(
            command_id=str(uuid4()),
            type=command_type,
            payload=payload,
            created_at=current_utc_time(),
        )

    async def send_required_command(
        self,
        pc_name: str,
        command: ClientCommand,
    ) -> list[CommandAcknowledgement]:
        acknowledgements = await asyncio.wait_for(
            self.connection_manager.send_command(pc_name, command),
            timeout=self.command_timeout_seconds,
        )

        if len(acknowledgements) < 2:
            raise RuntimeError("Command did not return both acknowledgements")

        if acknowledgements[0].status != "received":
            raise RuntimeError("Command was not acknowledged as received")

        final_acknowledgement = acknowledgements[-1]
        if final_acknowledgement.status != "completed":
            raise RuntimeError(final_acknowledgement.message or "Command failed")

        return acknowledgements

    async def start_session(
        self,
        pc_name: str,
        duration_seconds: int | None = None,
        rate_id: str | None = None,
        amount: Decimal | None = None,
    ) -> SessionCommandResponse:
        duration_seconds, resolved_rate_id, rate_name_snapshot, base_amount = (
            self.resolve_session_package(rate_id, duration_seconds, amount, "duration_seconds")
        )

        cleaned_pc_name = pc_name.strip()
        self.require_available_client(cleaned_pc_name)

        if self.repository.get_active_session_for_pc(cleaned_pc_name) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"{cleaned_pc_name} already has an active session",
            )

        now = current_utc_time()
        session = SessionRecord(
            session_id=str(uuid4()),
            pc_name=cleaned_pc_name,
            status="active",
            started_at=now,
            scheduled_end_at=now + timedelta(seconds=duration_seconds),
            ended_at=None,
            original_seconds=duration_seconds,
            added_seconds=0,
            total_seconds=duration_seconds,
            amount_paid=None,
            ended_reason=None,
            created_at=now,
            rate_id=resolved_rate_id,
            rate_name_snapshot=rate_name_snapshot,
            base_amount=base_amount,
            additional_amount=Decimal("0.00"),
            total_amount=base_amount,
            session_mode="prepaid",
        )
        self.repository.create_session(session)
        self.create_event(
            session.session_id,
            "session_started",
            seconds=duration_seconds,
            rate_id=resolved_rate_id,
            rate_name_snapshot=rate_name_snapshot,
            amount=base_amount,
        )

        command = self.create_command(
            "start_session",
            {
                "session_id": session.session_id,
                "duration_seconds": duration_seconds,
                "scheduled_end_at": session.scheduled_end_at.isoformat(),
            },
        )

        try:
            acknowledgements = await self.send_required_command(
                cleaned_pc_name,
                command,
            )
        except Exception as error:
            failed_session = session.model_copy(
                update={
                    "status": "failed",
                    "ended_at": current_utc_time(),
                    "ended_reason": "command_failed",
                }
            )
            self.repository.update_session(failed_session)
            self.create_event(
                session.session_id,
                "command_failed",
                message=str(error),
            )
            raise HTTPException(
                status_code=502,
                detail=f"Start session command failed: {error}",
            ) from error

        saved_session = self.repository.get_session(session.session_id)
        if saved_session is None:
            raise HTTPException(status_code=500, detail="Session was not saved")

        return SessionCommandResponse(
            session=saved_session,
            command=command,
            acknowledgements=acknowledgements,
        )

    async def add_time(
        self,
        session_id: str,
        seconds: int | None = None,
        rate_id: str | None = None,
        amount: Decimal | None = None,
    ) -> SessionCommandResponse:
        seconds, resolved_rate_id, rate_name_snapshot, amount = (
            self.resolve_session_package(rate_id, seconds, amount, "seconds")
        )

        session = self.repository.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.status != "active":
            raise HTTPException(status_code=409, detail="Session is not active")
        if session.session_mode == "open_time":
            raise HTTPException(status_code=400, detail="Open-time sessions do not support add time")

        self.require_available_client(session.pc_name)
        command = self.create_command(
            "add_time",
            {"session_id": session.session_id, "seconds": seconds},
        )
        try:
            acknowledgements = await self.send_required_command(
                session.pc_name,
                command,
            )
        except Exception as error:
            self.create_event(
                session.session_id,
                "command_failed",
                message=str(error),
            )
            raise HTTPException(
                status_code=502,
                detail=f"Add time command failed: {error}",
            ) from error

        updated_session = session.model_copy(
            update={
                "added_seconds": session.added_seconds + seconds,
                "total_seconds": session.total_seconds + seconds,
                "scheduled_end_at": session.scheduled_end_at
                + timedelta(seconds=seconds),
                "additional_amount": session.additional_amount + amount,
                "total_amount": session.total_amount + amount,
            }
        )
        self.repository.update_session(updated_session)
        self.create_event(
            session.session_id,
            "time_added",
            seconds=seconds,
            rate_id=resolved_rate_id,
            rate_name_snapshot=rate_name_snapshot,
            amount=amount,
        )

        return SessionCommandResponse(
            session=updated_session,
            command=command,
            acknowledgements=acknowledgements,
        )

    async def start_prepaid_session(
        self,
        pc_name: str,
        payment: PaymentCreateRequest,
        duration_seconds: int | None = None,
        rate_id: str | None = None,
        amount: Decimal | None = None,
    ) -> SessionCommandResponse:
        if self.billing_service is None:
            raise HTTPException(status_code=500, detail="Billing service is unavailable")

        duration_seconds, resolved_rate_id, rate_name_snapshot, base_amount = (
            self.resolve_session_package(rate_id, duration_seconds, amount, "duration_seconds")
        )
        self.billing_service.validate_payment_for_amount(payment, base_amount)

        cleaned_pc_name = pc_name.strip()
        self.require_available_client(cleaned_pc_name)
        if self.repository.get_active_session_for_pc(cleaned_pc_name) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"{cleaned_pc_name} already has an active session",
            )

        now = current_utc_time()
        session = SessionRecord(
            session_id=str(uuid4()),
            pc_name=cleaned_pc_name,
            status="active",
            started_at=now,
            scheduled_end_at=now + timedelta(seconds=duration_seconds),
            ended_at=None,
            original_seconds=duration_seconds,
            added_seconds=0,
            total_seconds=duration_seconds,
            amount_paid=None,
            ended_reason=None,
            created_at=now,
            rate_id=resolved_rate_id,
            rate_name_snapshot=rate_name_snapshot,
            base_amount=base_amount,
            additional_amount=Decimal("0.00"),
            total_amount=base_amount,
            session_mode="prepaid",
        )
        self.repository.create_session(session)
        billing = self.billing_service.record_payment(
            session.session_id,
            payment,
            action_type="session_start",
            max_amount=base_amount,
        )
        recorded_payment = billing.payments[-1]
        self.create_event(
            session.session_id,
            "prepaid_start_paid",
            seconds=duration_seconds,
            amount=recorded_payment.amount,
            payment_id=recorded_payment.payment_id,
            payment_method=recorded_payment.payment_method,
        )

        command = self.create_command(
            "start_session",
            {
                "session_id": session.session_id,
                "session_mode": "prepaid",
                "duration_seconds": duration_seconds,
                "scheduled_end_at": session.scheduled_end_at.isoformat(),
            },
        )
        try:
            acknowledgements = await self.send_required_command(cleaned_pc_name, command)
        except Exception as error:
            self.billing_service.mark_payment_review_required(
                recorded_payment.payment_id,
                command.command_id,
            )
            failed_session = session.model_copy(
                update={
                    "status": "failed",
                    "ended_at": current_utc_time(),
                    "ended_reason": "command_failed_after_payment",
                }
            )
            self.repository.update_session(failed_session)
            self.create_event(
                session.session_id,
                "payment_action_review_required",
                message=str(error),
                payment_id=recorded_payment.payment_id,
                payment_method=recorded_payment.payment_method,
            )
            raise HTTPException(
                status_code=502,
                detail=f"Payment was recorded but start command failed and needs review: {error}",
            ) from error

        saved_session = self.repository.get_session(session.session_id)
        if saved_session is None:
            raise HTTPException(status_code=500, detail="Session was not saved")

        return SessionCommandResponse(
            session=saved_session,
            command=command,
            acknowledgements=acknowledgements,
        )

    async def add_prepaid_time(
        self,
        session_id: str,
        payment: PaymentCreateRequest,
        seconds: int | None = None,
        rate_id: str | None = None,
        amount: Decimal | None = None,
    ) -> SessionCommandResponse:
        if self.billing_service is None:
            raise HTTPException(status_code=500, detail="Billing service is unavailable")

        seconds, resolved_rate_id, rate_name_snapshot, amount_due = (
            self.resolve_session_package(rate_id, seconds, amount, "seconds")
        )
        self.billing_service.validate_payment_for_amount(payment, amount_due)

        session = self.repository.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.status != "active":
            raise HTTPException(status_code=409, detail="Session is not active")
        if session.session_mode == "open_time":
            raise HTTPException(status_code=400, detail="Open-time sessions do not support add time")

        self.require_available_client(session.pc_name)
        updated_session = session.model_copy(
            update={
                "added_seconds": session.added_seconds + seconds,
                "total_seconds": session.total_seconds + seconds,
                "scheduled_end_at": session.scheduled_end_at + timedelta(seconds=seconds),
                "additional_amount": session.additional_amount + amount_due,
                "total_amount": session.total_amount + amount_due,
            }
        )
        self.repository.update_session(updated_session)
        billing = self.billing_service.record_payment(
            session.session_id,
            payment,
            action_type="add_time",
            max_amount=amount_due,
        )
        recorded_payment = billing.payments[-1]
        self.create_event(
            session.session_id,
            "prepaid_add_time_paid",
            seconds=seconds,
            amount=recorded_payment.amount,
            payment_id=recorded_payment.payment_id,
            payment_method=recorded_payment.payment_method,
        )

        command = self.create_command(
            "add_time",
            {"session_id": session.session_id, "seconds": seconds},
        )
        try:
            acknowledgements = await self.send_required_command(session.pc_name, command)
        except Exception as error:
            self.billing_service.mark_payment_review_required(
                recorded_payment.payment_id,
                command.command_id,
            )
            self.create_event(
                session.session_id,
                "payment_action_review_required",
                message=str(error),
                payment_id=recorded_payment.payment_id,
                payment_method=recorded_payment.payment_method,
            )
            raise HTTPException(
                status_code=502,
                detail=f"Payment was recorded but add-time command failed and needs review: {error}",
            ) from error

        return SessionCommandResponse(
            session=updated_session,
            command=command,
            acknowledgements=acknowledgements,
        )

    async def start_open_time_session(self, pc_name: str) -> SessionCommandResponse:
        cleaned_pc_name = pc_name.strip()
        self.require_available_client(cleaned_pc_name)
        if self.repository.get_active_session_for_pc(cleaned_pc_name) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"{cleaned_pc_name} already has an active session",
            )

        now = current_utc_time()
        session = SessionRecord(
            session_id=str(uuid4()),
            pc_name=cleaned_pc_name,
            status="active",
            started_at=now,
            scheduled_end_at=now,
            ended_at=None,
            original_seconds=0,
            added_seconds=0,
            total_seconds=0,
            amount_paid=None,
            ended_reason=None,
            created_at=now,
            base_amount=Decimal("0.00"),
            additional_amount=Decimal("0.00"),
            total_amount=Decimal("0.00"),
            session_mode="open_time",
            settlement_pending=True,
        )
        self.repository.create_session(session)
        self.create_event(session.session_id, "open_time_started")
        command = self.create_command(
            "start_session",
            {
                "session_id": session.session_id,
                "session_mode": "open_time",
                "started_at": session.started_at.isoformat(),
            },
        )
        try:
            acknowledgements = await self.send_required_command(cleaned_pc_name, command)
        except Exception as error:
            failed_session = session.model_copy(
                update={
                    "status": "failed",
                    "ended_at": current_utc_time(),
                    "ended_reason": "command_failed",
                    "settlement_pending": False,
                }
            )
            self.repository.update_session(failed_session)
            self.create_event(session.session_id, "command_failed", message=str(error))
            raise HTTPException(
                status_code=502,
                detail=f"Open-time start command failed: {error}",
            ) from error

        return SessionCommandResponse(
            session=session,
            command=command,
            acknowledgements=acknowledgements,
        )

    def compute_open_time_final_amount(
        self,
        session: SessionRecord,
        ended_at: datetime,
    ) -> tuple[int, int, Decimal]:
        metered_rate = self.rate_repository.get_active_metered_rate()
        if metered_rate is None:
            raise HTTPException(status_code=409, detail="Active metered rate is missing")

        actual_elapsed_seconds = max(
            1,
            int((ended_at - session.started_at).total_seconds()),
        )
        billable_units = int(
            (Decimal(actual_elapsed_seconds) / Decimal(metered_rate.duration_seconds))
            .to_integral_value(rounding=ROUND_CEILING)
        )
        billed_seconds = billable_units * metered_rate.duration_seconds
        final_amount = metered_rate.price * Decimal(billable_units)
        return actual_elapsed_seconds, billed_seconds, final_amount

    async def end_session(self, session_id: str) -> SessionCommandResponse:
        session = self.repository.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.status != "active":
            raise HTTPException(status_code=409, detail="Session is not active")

        self.require_available_client(session.pc_name)
        command = self.create_command(
            "end_session",
            {"session_id": session.session_id},
        )
        try:
            acknowledgements = await self.send_required_command(session.pc_name, command)
        except Exception as error:
            self.create_event(
                session.session_id,
                "command_failed",
                message=str(error),
            )
            raise HTTPException(
                status_code=502,
                detail=f"End session command failed: {error}",
            ) from error

        updated_session = session.model_copy(
            update={
                "status": "ended_manually",
                "ended_at": current_utc_time(),
                "ended_reason": "manual_end",
            }
        )
        if session.session_mode == "open_time":
            ended_at = current_utc_time()
            actual_elapsed_seconds, billed_seconds, final_amount = (
                self.compute_open_time_final_amount(session, ended_at)
            )
            updated_session = session.model_copy(
                update={
                    "status": "ended_manually",
                    "ended_at": ended_at,
                    "ended_reason": "manual_end",
                    "actual_elapsed_seconds": actual_elapsed_seconds,
                    "billed_seconds": billed_seconds,
                    "final_computed_amount": final_amount,
                    "base_amount": final_amount,
                    "total_amount": final_amount,
                    "total_seconds": actual_elapsed_seconds,
                    "settlement_pending": True,
                }
            )
        self.repository.update_session(updated_session)
        self.create_event(
            session.session_id,
            "open_time_ended" if session.session_mode == "open_time" else "session_ended_manually",
            seconds=updated_session.actual_elapsed_seconds,
            amount=updated_session.final_computed_amount,
        )

        return SessionCommandResponse(
            session=updated_session,
            command=command,
            acknowledgements=acknowledgements,
        )

    def complete_from_client(
        self,
        session_id: str,
        message: str | None = None,
    ) -> bool:
        session = self.repository.get_session(session_id)
        if session is None:
            return False

        if session.status != "active":
            return session.status == "completed"

        if self.repository.event_exists(session_id, "session_completed"):
            return True

        completed_session = session.model_copy(
            update={
                "status": "completed",
                "ended_at": current_utc_time(),
                "ended_reason": message or "timer_finished",
            }
        )
        self.repository.update_session(completed_session)
        self.create_event(
            session_id,
            "session_completed",
            message=message or "Timer finished",
        )
        return True

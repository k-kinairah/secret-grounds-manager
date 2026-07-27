import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException

from server.connection_manager import ClientConnectionManager
from server.billing_service import BillingService
from server.billing_service import calculate_open_time_estimate
from server.rate_calculator import calculate_metered_time
from server.rate_repository import RateRepository
from server.schemas import (
    ClientCommand,
    CommandAcknowledgement,
    ForceEndSessionResponse,
    PCRecord,
    PreparedSessionRecord,
    PreparedSessionResponse,
    PreparedSessionActivationResponse,
    PreparedSessionStartRequest,
    SessionCommandResponse,
    SessionEventRecord,
    SessionRecord,
    PaymentCreateRequest,
)
from server.prepared_session_repository import PreparedSessionRepository, payment_from_json, payment_to_json
from server.session_repository import SessionRepository
from server.settings import EXPIRATION_GRACE_SECONDS
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
        member_repository: object | None = None,
        prepared_session_repository: PreparedSessionRepository | None = None,
    ) -> None:
        self.repository = repository
        self.rate_repository = rate_repository
        self.connection_manager = connection_manager
        self.pc_registry = pc_registry
        self.command_timeout_seconds = command_timeout_seconds
        self.billing_service = billing_service
        self.member_repository = member_repository
        self.prepared_session_repository = prepared_session_repository

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
        performed_by_user_id: str | None = None,
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
            performed_by_user_id=performed_by_user_id,
        )
        return self.repository.add_event(event)

    def create_prepared_audit_event(
        self,
        prepared: PreparedSessionRecord,
        event_type: str,
        message: str | None = None,
        performed_by_user_id: str | None = None,
    ) -> None:
        session_id = prepared.activated_session_id
        if session_id is None:
            return
        self.create_event(
            session_id,
            event_type,
            message=message,
            performed_by_user_id=performed_by_user_id,
        )

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

    def require_prepared_repository(self) -> PreparedSessionRepository:
        if self.prepared_session_repository is None:
            raise HTTPException(status_code=500, detail="Prepared startup service is unavailable")
        return self.prepared_session_repository

    def prepare_offline_session(
        self,
        request: PreparedSessionStartRequest,
        actor_user_id: str | None = None,
    ) -> PreparedSessionResponse:
        repository = self.require_prepared_repository()
        pc_name = request.pc_name.strip()
        if not pc_name:
            raise HTTPException(status_code=400, detail="PC name is required")
        if self.repository.get_active_session_for_pc(pc_name) is not None:
            raise HTTPException(status_code=409, detail="This PC already has an active session")
        existing = repository.get_active_for_pc(pc_name)
        if existing is not None:
            return PreparedSessionResponse(
                prepared_session=existing,
                message="This PC already has a prepared startup in progress.",
            )

        duration_seconds: int | None = request.duration_seconds
        resolved_rate_id: str | None = None
        rate_name_snapshot: str | None = None
        amount = request.amount
        member_name_snapshot: str | None = None
        member_open_play = False

        if request.session_mode == "prepaid":
            duration_seconds, resolved_rate_id, rate_name_snapshot, amount = (
                self.resolve_session_package(
                    request.rate_id,
                    request.duration_seconds,
                    request.amount,
                    "duration_seconds",
                )
            )
            if amount > Decimal("0.00") and request.payment is None:
                raise HTTPException(status_code=400, detail="Payment details are required for prepared prepaid sessions")
            if request.payment is not None and self.billing_service is not None:
                self.billing_service.validate_payment_for_amount(request.payment, amount)
        elif request.session_mode == "member_balance":
            if self.member_repository is None or request.member_id is None:
                raise HTTPException(status_code=400, detail="Member is required")
            member = self.member_repository.require_usable_member(request.member_id)  # type: ignore[attr-defined]
            self.member_repository.verify_member_pin_if_required(member.member_id, request.member_pin)  # type: ignore[attr-defined]
            member_name_snapshot = member.full_name
            member_open_play = request.duration_seconds is None
            duration_seconds = request.duration_seconds or member.time_balance_seconds
            if duration_seconds <= 0:
                raise HTTPException(status_code=409, detail="Member has no available time balance")
            if member.time_balance_seconds < duration_seconds:
                raise HTTPException(status_code=409, detail="Insufficient member time balance")
        elif request.session_mode == "open_time":
            duration_seconds = None
        else:
            raise HTTPException(status_code=400, detail="Unsupported session mode")

        now = current_utc_time()
        prepared = PreparedSessionRecord(
            prepared_session_id=repository.new_id(),
            pc_name=pc_name,
            status="prepared",
            session_mode=request.session_mode,
            rate_id=resolved_rate_id or request.rate_id,
            rate_name_snapshot=rate_name_snapshot,
            duration_seconds=duration_seconds,
            amount=amount,
            payment_json=payment_to_json(request.payment),
            member_id=request.member_id,
            member_pin=request.member_pin,
            member_name_snapshot=member_name_snapshot,
            member_open_play=member_open_play,
            prepared_by_user_id=actor_user_id,
            prepared_operator_id=request.operator_id,
            prepared_at=now,
            wake_requested_at=None,
            wake_status=None,
            startup_status="prepared",
            failure_reason=None,
            expires_at=now + timedelta(seconds=120),
        )
        return PreparedSessionResponse(
            prepared_session=repository.create(prepared),
            message="Prepared session created. Wake signal can be sent.",
        )

    def mark_prepared_wake_sent(
        self,
        prepared_session_id: str,
        wake_status: str = "Wake signal sent",
    ) -> PreparedSessionRecord:
        repository = self.require_prepared_repository()
        prepared = repository.get(prepared_session_id)
        if prepared is None:
            raise HTTPException(status_code=404, detail="Prepared session not found")
        if prepared.status in {"active", "cancelled", "failed"}:
            raise HTTPException(status_code=409, detail="Prepared session is no longer waiting")
        now = current_utc_time()
        return repository.update(
            prepared.model_copy(
                update={
                    "status": "waiting_for_client",
                    "startup_status": "waiting_for_client",
                    "wake_status": wake_status,
                    "wake_requested_at": now,
                    "failure_reason": None,
                }
            )
        )

    def cancel_prepared_session(
        self,
        prepared_session_id: str,
        actor_user_id: str | None = None,
    ) -> PreparedSessionResponse:
        repository = self.require_prepared_repository()
        prepared = repository.get(prepared_session_id)
        if prepared is None:
            raise HTTPException(status_code=404, detail="Prepared session not found")
        if prepared.status in {"active", "cancelled", "failed"}:
            return PreparedSessionResponse(
                prepared_session=prepared,
                message="Prepared session is already closed.",
            )
        cancelled = repository.update(
            prepared.model_copy(
                update={
                    "status": "cancelled",
                    "startup_status": "cancelled",
                    "cancelled_by_user_id": actor_user_id,
                    "cancelled_at": current_utc_time(),
                    "failure_reason": "Cancelled before the client connected",
                }
            )
        )
        return PreparedSessionResponse(
            prepared_session=cancelled,
            message="Prepared session cancelled. No charge was created.",
        )

    async def activate_prepared_session_for_pc(
        self,
        pc_name: str,
    ) -> PreparedSessionActivationResponse | None:
        repository = self.require_prepared_repository()
        repository.expire_old_preparations()
        prepared = repository.get_active_for_pc(pc_name)
        if prepared is None:
            return None
        if prepared.activated_session_id is not None:
            return PreparedSessionActivationResponse(
                prepared_session=prepared,
                session=self.repository.get_session(prepared.activated_session_id),
            )
        if self.repository.get_active_session_for_pc(pc_name) is not None:
            failed = repository.update(
                prepared.model_copy(
                    update={
                        "status": "failed",
                        "startup_status": "failed",
                        "failure_reason": "PC already has an active billable session",
                    }
                )
            )
            return PreparedSessionActivationResponse(prepared_session=failed)

        starting = repository.update(
            prepared.model_copy(
                update={"status": "starting", "startup_status": "starting"}
            )
        )
        now = current_utc_time()
        session = self.build_session_from_prepared(starting, now)
        self.repository.create_session(session)
        if session.session_mode == "member_balance":
            if self.member_repository is None or session.member_id is None:
                raise HTTPException(status_code=500, detail="Member reservation service is unavailable")
            member = self.member_repository.require_usable_member(session.member_id)  # type: ignore[attr-defined]
            self.member_repository.create_reservation(  # type: ignore[attr-defined]
                member,
                session.session_id,
                session.total_seconds,
                starting.prepared_by_user_id,
            )
        elif session.session_mode == "prepaid" and self.billing_service is not None:
            payment = payment_from_json(starting.payment_json)
            if payment is not None and session.total_amount > Decimal("0.00"):
                self.billing_service.record_payment(
                    session.session_id,
                    payment,
                    action_type="session_start",
                    max_amount=session.total_amount,
                    actor_user_id=starting.prepared_by_user_id,
                )

        self.create_event(
            session.session_id,
            "session_prepared",
            message=f"Prepared startup activated from {starting.prepared_session_id}",
            performed_by_user_id=starting.prepared_operator_id or starting.prepared_by_user_id,
        )
        self.create_event(
            session.session_id,
            "session_activation_started",
            performed_by_user_id=starting.prepared_operator_id or starting.prepared_by_user_id,
        )

        command = self.create_start_command_for_session(session)
        try:
            acknowledgements = await self.send_required_command(pc_name, command)
        except Exception as error:
            failed_session = session.model_copy(
                update={
                    "status": "failed",
                    "ended_at": current_utc_time(),
                    "ended_reason": "prepared_start_command_failed",
                }
            )
            self.repository.update_session(failed_session)
            if session.session_mode == "member_balance" and session.member_id is not None and self.member_repository is not None:
                self.member_repository.release_reservation(  # type: ignore[attr-defined]
                    session.member_id,
                    session.session_id,
                    starting.prepared_by_user_id,
                    f"Released prepared startup reservation after command failure: {error}",
                )
            failed = repository.update(
                starting.model_copy(
                    update={
                        "status": "failed",
                        "startup_status": "failed",
                        "failure_reason": str(error),
                        "activated_session_id": session.session_id,
                    }
                )
            )
            self.create_event(
                session.session_id,
                "startup_failed",
                message=str(error),
                performed_by_user_id=starting.prepared_operator_id or starting.prepared_by_user_id,
            )
            return PreparedSessionActivationResponse(
                prepared_session=failed,
                session=failed_session,
                command=command,
            )

        activated = repository.update(
            starting.model_copy(
                update={
                    "status": "active",
                    "startup_status": "active",
                    "activated_session_id": session.session_id,
                    "activated_at": now,
                    "failure_reason": None,
                }
            )
        )
        self.create_event(
            session.session_id,
            "session_activated",
            performed_by_user_id=starting.prepared_operator_id or starting.prepared_by_user_id,
        )
        return PreparedSessionActivationResponse(
            prepared_session=activated,
            session=(
                self.billing_service.enrich_session(session)
                if self.billing_service is not None
                else session
            ),
            command=command,
            acknowledgements=acknowledgements,
        )

    def build_session_from_prepared(
        self,
        prepared: PreparedSessionRecord,
        activated_at: datetime,
    ) -> SessionRecord:
        session_id = str(uuid4())
        if prepared.session_mode == "open_time":
            metered_rate = self.rate_repository.get_active_metered_rate()
            if metered_rate is None:
                raise HTTPException(status_code=409, detail="Active metered rate is missing")
            return SessionRecord(
                session_id=session_id,
                pc_name=prepared.pc_name,
                status="active",
                started_at=activated_at,
                scheduled_end_at=activated_at,
                original_seconds=0,
                added_seconds=0,
                total_seconds=0,
                amount_paid=None,
                ended_reason=None,
                created_at=activated_at,
                base_amount=Decimal("0.00"),
                additional_amount=Decimal("0.00"),
                total_amount=Decimal("0.00"),
                session_mode="open_time",
                settlement_pending=True,
                rate_id=metered_rate.rate_id,
                rate_name_snapshot=f"{metered_rate.name}: {metered_rate.price} per {metered_rate.duration_seconds}s",
                metered_rate_price_snapshot=metered_rate.price,
                metered_rate_duration_seconds_snapshot=metered_rate.duration_seconds,
                created_by_user_id=prepared.prepared_operator_id or prepared.prepared_by_user_id,
            )

        if prepared.duration_seconds is None or prepared.duration_seconds <= 0:
            raise HTTPException(status_code=400, detail="Prepared duration is invalid")
        return SessionRecord(
            session_id=session_id,
            pc_name=prepared.pc_name,
            status="active",
            started_at=activated_at,
            scheduled_end_at=activated_at + timedelta(seconds=prepared.duration_seconds),
            original_seconds=prepared.duration_seconds,
            added_seconds=0,
            total_seconds=prepared.duration_seconds,
            amount_paid=None,
            ended_reason=None,
            created_at=activated_at,
            rate_id=prepared.rate_id,
            rate_name_snapshot=prepared.rate_name_snapshot,
            base_amount=prepared.amount or Decimal("0.00"),
            additional_amount=Decimal("0.00"),
            total_amount=prepared.amount or Decimal("0.00"),
            session_mode=prepared.session_mode,
            created_by_user_id=prepared.prepared_operator_id or prepared.prepared_by_user_id,
            member_id=prepared.member_id,
            member_name_snapshot=prepared.member_name_snapshot,
        )

    def create_start_command_for_session(self, session: SessionRecord) -> ClientCommand:
        if session.session_mode == "open_time":
            return self.create_command(
                "start_session",
                {
                    "session_id": session.session_id,
                    "session_mode": "open_time",
                    "started_at": session.started_at.isoformat(),
                },
            )
        payload: dict[str, object] = {
            "session_id": session.session_id,
            "session_mode": session.session_mode,
            "duration_seconds": session.total_seconds,
            "scheduled_end_at": session.scheduled_end_at.isoformat(),
        }
        if session.session_mode == "member_balance":
            payload["timer_mode"] = "elapsed" if session.original_seconds == 0 else "countdown"
            payload["max_usable_seconds"] = session.total_seconds
        return self.create_command("start_session", payload)

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

    def begin_expiration_grace(
        self,
        session_id: str,
        grace_started_at: datetime | None = None,
    ) -> SessionRecord | None:
        session = self.repository.get_session(session_id)
        if session is None:
            return None
        if session.status in {"settlement_pending", "completed", "ended_manually", "cancelled", "failed"}:
            return session
        if session.session_mode == "open_time":
            return session
        if session.status == "grace_period":
            return session
        if session.status == "shutting_down":
            return session
        if session.status != "active":
            return session

        started_at = grace_started_at or current_utc_time()
        shutdown_at = started_at + timedelta(seconds=EXPIRATION_GRACE_SECONDS)
        updated = session.model_copy(
            update={
                "status": "grace_period",
                "grace_started_at": started_at,
                "shutdown_at": shutdown_at,
            }
        )
        self.repository.update_session(updated)
        self.create_event(
            session.session_id,
            "time_expired",
            message="Time expired; shutdown grace period started",
        )
        self.create_event(
            session.session_id,
            "shutdown_grace_started",
            seconds=EXPIRATION_GRACE_SECONDS,
            message=f"Shutdown scheduled at {shutdown_at.isoformat()}",
        )
        return updated

    def finalize_expiration_shutdown(
        self,
        session_id: str,
        message: str | None = None,
    ) -> bool:
        session = self.repository.get_session(session_id)
        if session is None:
            return False
        if session.status in {"completed", "settlement_pending", "ended_manually"}:
            return True
        if session.status not in {"grace_period", "shutting_down", "active"}:
            return False

        shutting_down = session.model_copy(
            update={
                "status": "shutting_down",
                "ended_reason": "expiration_shutdown",
            }
        )
        self.repository.update_session(shutting_down)
        self.create_event(
            session_id,
            "shutdown_grace_completed",
            message=message or "Expiration grace period completed",
        )
        self.create_event(
            session_id,
            "automatic_shutdown_initiated",
            message=message or "Expiration grace period completed",
        )
        self.create_event(
            session_id,
            "shutdown_command_acknowledged",
            message="Client acknowledged automatic shutdown finalization",
        )
        completed = self.finalize_ended_session(
            shutting_down,
            "timer_finished",
            None,
        )
        self.create_event(
            session_id,
            "session_completed",
            message=message or "Timer finished after grace period",
        )
        if completed.status == "settlement_pending":
            self.create_event(
                session_id,
                "session_moved_to_settlement",
                message="Session moved to settlement after expiration shutdown",
            )
        return True

    async def send_required_command(
        self,
        pc_name: str,
        command: ClientCommand,
    ) -> list[CommandAcknowledgement]:
        print(
            "Sending client command payload: "
            f"pc_name={pc_name}, command={command.model_dump(mode='json')}"
        )
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
            print(
                "Client rejected command: "
                f"command_id={final_acknowledgement.command_id}, "
                f"reason={final_acknowledgement.message}"
            )
            raise RuntimeError(final_acknowledgement.message or "Command failed")

        return acknowledgements

    def finalize_ended_session(
        self,
        session: SessionRecord,
        ended_reason: str,
        actor_user_id: str | None,
    ) -> SessionRecord:
        ended_at = current_utc_time()
        next_status = (
            "settlement_pending"
            if session.session_mode == "open_time"
            else "completed"
        )
        update_values: dict[str, object] = {
            "status": next_status,
            "ended_at": ended_at,
            "ended_reason": ended_reason,
            "ended_by_user_id": actor_user_id,
        }
        if session.session_mode == "open_time":
            actual_elapsed_seconds, billed_seconds, final_amount = (
                self.compute_open_time_final_amount(session, ended_at)
            )
            update_values.update(
                {
                    "actual_elapsed_seconds": actual_elapsed_seconds,
                    "billed_seconds": billed_seconds,
                    "final_computed_amount": final_amount,
                    "base_amount": final_amount,
                    "total_amount": final_amount,
                    "total_seconds": actual_elapsed_seconds,
                    "settlement_pending": True,
                }
            )
        elif session.session_mode == "member_balance":
            if self.member_repository is None or session.member_id is None:
                raise HTTPException(status_code=500, detail="Member reservation service is unavailable")
            actual_elapsed_seconds = max(0, int((ended_at - session.started_at).total_seconds()))
            consumed_seconds, transaction = self.member_repository.finalize_reservation_usage(  # type: ignore[attr-defined]
                session.member_id,
                session.session_id,
                actual_elapsed_seconds,
                actor_user_id,
            )
            member = self.member_repository.get_member(session.member_id)  # type: ignore[attr-defined]
            update_values.update(
                {
                    "actual_elapsed_seconds": actual_elapsed_seconds,
                    "billed_seconds": consumed_seconds,
                    "total_seconds": consumed_seconds,
                    "member_balance_after_seconds": (
                        transaction.balance_after_seconds
                        if transaction is not None
                        else member.time_balance_seconds if member is not None else session.member_balance_after_seconds
                    ),
                }
            )

        updated_session = session.model_copy(update=update_values)
        self.repository.update_session(updated_session)
        return (
            self.billing_service.enrich_session(updated_session)
            if self.billing_service is not None
            else updated_session
        )

    async def start_session(
        self,
        pc_name: str,
        duration_seconds: int | None = None,
        rate_id: str | None = None,
        amount: Decimal | None = None,
        actor_user_id: str | None = None,
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
            created_by_user_id=actor_user_id,
        )
        self.repository.create_session(session)
        self.create_event(
            session.session_id,
            "session_started",
            seconds=duration_seconds,
            rate_id=resolved_rate_id,
            rate_name_snapshot=rate_name_snapshot,
            amount=base_amount,
            performed_by_user_id=actor_user_id,
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
                performed_by_user_id=actor_user_id,
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
        actor_user_id: str | None = None,
    ) -> SessionCommandResponse:
        seconds, resolved_rate_id, rate_name_snapshot, amount = (
            self.resolve_session_package(rate_id, seconds, amount, "seconds")
        )

        session = self.repository.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        is_grace_rescue = session.status == "grace_period"
        if session.status not in {"active", "ending", "grace_period"}:
            raise HTTPException(status_code=409, detail="Session is not active")
        if session.session_mode == "open_time":
            raise HTTPException(status_code=400, detail="Open-time sessions do not support add time")
        if session.session_mode == "member_balance":
            raise HTTPException(status_code=400, detail="Use member balance to add time to this session")

        self.require_available_client(session.pc_name)
        now = current_utc_time()
        updated_session = session.model_copy(
            update={
                "status": "active",
                "added_seconds": session.added_seconds + seconds,
                "total_seconds": session.total_seconds + seconds,
                "scheduled_end_at": (
                    now + timedelta(seconds=seconds)
                    if is_grace_rescue
                    else session.scheduled_end_at + timedelta(seconds=seconds)
                ),
                "additional_amount": session.additional_amount + amount,
                "total_amount": session.total_amount + amount,
                "grace_started_at": None,
                "shutdown_at": None,
            }
        )
        command = self.create_command(
            "add_time",
            {
                "session_id": session.session_id,
                "seconds": seconds,
                "cancel_expiration": is_grace_rescue,
                "scheduled_end_at": updated_session.scheduled_end_at.isoformat(),
            },
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
                performed_by_user_id=actor_user_id,
            )
            raise HTTPException(
                status_code=502,
                detail=f"Add time command failed: {error}",
            ) from error
        self.repository.update_session(updated_session)
        self.create_event(
            session.session_id,
            "time_added_during_grace" if is_grace_rescue else "time_added",
            seconds=seconds,
            rate_id=resolved_rate_id,
            rate_name_snapshot=rate_name_snapshot,
            amount=amount,
            performed_by_user_id=actor_user_id,
        )
        if is_grace_rescue:
            self.create_event(
                session.session_id,
                "automatic_shutdown_cancelled",
                message="Time added during grace period",
                performed_by_user_id=actor_user_id,
            )

        response_session = (
            self.billing_service.enrich_session(updated_session)
            if self.billing_service is not None
            else updated_session
        )

        return SessionCommandResponse(
            session=response_session,
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
        actor_user_id: str | None = None,
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
            created_by_user_id=actor_user_id,
        )
        self.repository.create_session(session)
        billing = self.billing_service.record_payment(
            session.session_id,
            payment,
            action_type="session_start",
            max_amount=base_amount,
            actor_user_id=actor_user_id,
        )
        recorded_payment = billing.payments[-1]
        self.create_event(
            session.session_id,
            "prepaid_start_paid",
            seconds=duration_seconds,
            amount=recorded_payment.amount,
            payment_id=recorded_payment.payment_id,
            payment_method=recorded_payment.payment_method,
            performed_by_user_id=actor_user_id,
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
                performed_by_user_id=actor_user_id,
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
        actor_user_id: str | None = None,
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
        is_grace_rescue = session.status == "grace_period"
        if session.status not in {"active", "grace_period"}:
            raise HTTPException(status_code=409, detail="Session is not active")
        if session.session_mode == "open_time":
            raise HTTPException(status_code=400, detail="Open-time sessions do not support add time")
        if session.session_mode == "member_balance":
            raise HTTPException(status_code=400, detail="Use member balance to add time to this session")

        now = current_utc_time()
        self.require_available_client(session.pc_name)
        updated_session = session.model_copy(
            update={
                "status": "active",
                "added_seconds": session.added_seconds + seconds,
                "total_seconds": session.total_seconds + seconds,
                "scheduled_end_at": (
                    now + timedelta(seconds=seconds)
                    if is_grace_rescue
                    else session.scheduled_end_at + timedelta(seconds=seconds)
                ),
                "additional_amount": session.additional_amount + amount_due,
                "total_amount": session.total_amount + amount_due,
                "grace_started_at": None,
                "shutdown_at": None,
            }
        )
        billing = self.billing_service.record_payment(
            session.session_id,
            payment,
            action_type="add_time",
            max_amount=amount_due,
            actor_user_id=actor_user_id,
        )
        recorded_payment = billing.payments[-1]
        command = self.create_command(
            "add_time",
            {
                "session_id": session.session_id,
                "seconds": seconds,
                "cancel_expiration": is_grace_rescue,
                "scheduled_end_at": updated_session.scheduled_end_at.isoformat(),
            },
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
                performed_by_user_id=actor_user_id,
            )
            raise HTTPException(
                status_code=502,
                detail=f"Payment was recorded but add-time command failed and needs review: {error}",
            ) from error

        self.repository.update_session(updated_session)
        self.create_event(
            session.session_id,
            "time_added_during_grace" if is_grace_rescue else "prepaid_add_time_paid",
            seconds=seconds,
            amount=recorded_payment.amount,
            payment_id=recorded_payment.payment_id,
            payment_method=recorded_payment.payment_method,
            performed_by_user_id=actor_user_id,
        )
        if is_grace_rescue:
            self.create_event(
                session.session_id,
                "automatic_shutdown_cancelled",
                message="Prepaid time added during grace period",
                performed_by_user_id=actor_user_id,
            )

        return SessionCommandResponse(
            session=updated_session,
            command=command,
            acknowledgements=acknowledgements,
        )

    async def start_open_time_session(
        self,
        pc_name: str,
        actor_user_id: str | None = None,
    ) -> SessionCommandResponse:
        cleaned_pc_name = pc_name.strip()
        self.require_available_client(cleaned_pc_name)
        if self.repository.get_active_session_for_pc(cleaned_pc_name) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"{cleaned_pc_name} already has an active session",
            )
        metered_rate = self.rate_repository.get_active_metered_rate()
        if metered_rate is None:
            raise HTTPException(status_code=409, detail="Active metered rate is missing")

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
            rate_id=metered_rate.rate_id,
            rate_name_snapshot=(
                f"{metered_rate.name}: {metered_rate.price} per "
                f"{metered_rate.duration_seconds}s"
            ),
            metered_rate_price_snapshot=metered_rate.price,
            metered_rate_duration_seconds_snapshot=metered_rate.duration_seconds,
            created_by_user_id=actor_user_id,
        )
        self.repository.create_session(session)
        self.create_event(
            session.session_id,
            "open_time_started",
            performed_by_user_id=actor_user_id,
        )
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
            self.create_event(
                session.session_id,
                "command_failed",
                message=str(error),
                performed_by_user_id=actor_user_id,
            )
            raise HTTPException(
                status_code=502,
                detail=f"Open-time start command failed: {error}",
            ) from error

        return SessionCommandResponse(
            session=session,
            command=command,
            acknowledgements=acknowledgements,
        )

    async def start_member_session(
        self,
        pc_name: str,
        member_id: str,
        duration_seconds: int | None,
        member_pin: str | None = None,
        actor_user_id: str | None = None,
    ) -> SessionCommandResponse:
        if self.member_repository is None:
            raise HTTPException(status_code=500, detail="Member service is unavailable")
        if duration_seconds is not None and duration_seconds <= 0:
            raise HTTPException(status_code=400, detail="Duration must be positive")

        cleaned_pc_name = pc_name.strip()
        self.require_available_client(cleaned_pc_name)
        if self.repository.get_active_session_for_pc(cleaned_pc_name) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"{cleaned_pc_name} already has an active session",
            )

        member = self.member_repository.require_usable_member(member_id)  # type: ignore[attr-defined]
        self.member_repository.verify_member_pin_if_required(member.member_id, member_pin)  # type: ignore[attr-defined]
        session_id = str(uuid4())
        reserved_seconds = duration_seconds or member.time_balance_seconds
        if reserved_seconds <= 0:
            raise HTTPException(status_code=409, detail="Member has no available time balance")
        self.member_repository.create_reservation(  # type: ignore[attr-defined]
            member,
            session_id,
            reserved_seconds,
            actor_user_id,
        )
        now = current_utc_time()
        session = SessionRecord(
            session_id=session_id,
            pc_name=cleaned_pc_name,
            status="active",
            started_at=now,
            scheduled_end_at=now + timedelta(seconds=reserved_seconds),
            ended_at=None,
            original_seconds=duration_seconds or 0,
            added_seconds=0,
            total_seconds=reserved_seconds,
            amount_paid=None,
            ended_reason=None,
            created_at=now,
            base_amount=Decimal("0.00"),
            additional_amount=Decimal("0.00"),
            total_amount=Decimal("0.00"),
            session_mode="member_balance",
            created_by_user_id=actor_user_id,
            member_id=member.member_id,
            member_code_snapshot=member.member_code,
            member_name_snapshot=member.full_name,
            member_balance_before_seconds=member.time_balance_seconds,
            member_balance_after_seconds=member.time_balance_seconds,
        )
        self.repository.create_session(session)
        self.create_event(
            session.session_id,
            "session_started",
            seconds=reserved_seconds,
            message="Member balance session started; only actual finalized usage is deducted.",
            performed_by_user_id=actor_user_id,
        )
        command = self.create_command(
            "start_session",
            {
                "session_id": session.session_id,
                "session_mode": "member_balance",
                "timer_mode": "elapsed" if duration_seconds is None else "countdown",
                "duration_seconds": reserved_seconds,
                "max_usable_seconds": reserved_seconds,
                "scheduled_end_at": session.scheduled_end_at.isoformat(),
            },
        )
        try:
            acknowledgements = await self.send_required_command(cleaned_pc_name, command)
        except Exception as error:
            self.member_repository.release_reservation(  # type: ignore[attr-defined]
                member.member_id,
                session.session_id,
                actor_user_id,
                f"Released failed member start command reservation: {error}",
            )
            failed_session = session.model_copy(
                update={
                    "status": "failed",
                    "ended_at": current_utc_time(),
                    "ended_reason": "command_failed_after_member_reservation",
                }
            )
            self.repository.update_session(failed_session)
            self.create_event(
                session.session_id,
                "command_failed",
                message=str(error),
                performed_by_user_id=actor_user_id,
            )
            raise HTTPException(
                status_code=502,
                detail=f"Member time was refunded after start command failed: {error}",
            ) from error

        return SessionCommandResponse(
            session=session,
            command=command,
            acknowledgements=acknowledgements,
        )

    async def add_member_time(
        self,
        session_id: str,
        member_id: str,
        seconds: int,
        actor_user_id: str | None = None,
    ) -> SessionCommandResponse:
        if self.member_repository is None:
            raise HTTPException(status_code=500, detail="Member service is unavailable")
        if seconds <= 0:
            raise HTTPException(status_code=400, detail="Seconds must be positive")

        session = self.repository.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        is_grace_rescue = session.status == "grace_period"
        if session.status not in {"active", "grace_period"}:
            raise HTTPException(status_code=409, detail="Session is not active")
        if session.session_mode != "member_balance" or session.member_id != member_id:
            raise HTTPException(status_code=400, detail="Session is not funded by this member")

        self.require_available_client(session.pc_name)
        member = self.member_repository.require_usable_member(member_id)  # type: ignore[attr-defined]
        if member.time_balance_seconds < session.total_seconds + seconds:
            raise HTTPException(status_code=409, detail="Insufficient member time balance")
        now = current_utc_time()
        scheduled_end_at = (
            now + timedelta(seconds=seconds)
            if is_grace_rescue
            else session.scheduled_end_at + timedelta(seconds=seconds)
        )
        command = self.create_command(
            "add_time",
            {
                "session_id": session.session_id,
                "seconds": seconds,
                "cancel_expiration": is_grace_rescue,
                "scheduled_end_at": scheduled_end_at.isoformat(),
            },
        )
        try:
            acknowledgements = await self.send_required_command(session.pc_name, command)
        except Exception as error:
            self.create_event(
                session.session_id,
                "command_failed",
                message=str(error),
                performed_by_user_id=actor_user_id,
            )
            raise HTTPException(
                status_code=502,
                detail=f"Member add-time command failed before reservation changed: {error}",
            ) from error

        reserved_seconds = self.member_repository.extend_reservation(  # type: ignore[attr-defined]
            member,
            session.session_id,
            seconds,
            actor_user_id,
        )
        updated_session = session.model_copy(
            update={
                "status": "active",
                "added_seconds": session.added_seconds + seconds,
                "total_seconds": session.total_seconds + seconds,
                "scheduled_end_at": scheduled_end_at,
                "member_balance_after_seconds": member.time_balance_seconds,
                "grace_started_at": None,
                "shutdown_at": None,
            }
        )
        self.repository.update_session(updated_session)
        self.create_event(
            session.session_id,
            "time_added_during_grace" if is_grace_rescue else "time_added",
            seconds=seconds,
            message=f"Member balance reservation extended to {reserved_seconds} seconds.",
            performed_by_user_id=actor_user_id,
        )
        if is_grace_rescue:
            self.create_event(
                session.session_id,
                "automatic_shutdown_cancelled",
                message="Member time added during grace period",
                performed_by_user_id=actor_user_id,
            )
        return SessionCommandResponse(
            session=updated_session,
            command=command,
            acknowledgements=acknowledgements,
        )

    def compute_open_time_final_amount(
        self,
        session: SessionRecord,
        ended_at: datetime,
    ) -> tuple[int, int, Decimal]:
        metered_price = session.metered_rate_price_snapshot
        metered_duration_seconds = session.metered_rate_duration_seconds_snapshot
        if metered_price is None or metered_duration_seconds is None:
            metered_rate = self.rate_repository.get_active_metered_rate()
            if metered_rate is None:
                raise HTTPException(status_code=409, detail="Active metered rate is missing")
            metered_price = metered_rate.price
            metered_duration_seconds = metered_rate.duration_seconds

        return calculate_open_time_estimate(
            session.started_at,
            ended_at,
            metered_price,
            metered_duration_seconds,
        )

    async def end_session(
        self,
        session_id: str,
        actor_user_id: str | None = None,
    ) -> SessionCommandResponse:
        session = self.repository.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.status == "settlement_pending":
            raise HTTPException(status_code=409, detail="Session is already awaiting settlement")
        if session.status not in {"active", "ending"}:
            raise HTTPException(status_code=409, detail="Session is not active")

        self.require_available_client(session.pc_name)
        ending_session = session.model_copy(update={"status": "ending"})
        self.repository.update_session(ending_session)
        command = self.create_command(
            "end_session",
            {"session_id": session.session_id},
        )
        try:
            acknowledgements = await self.send_required_command(session.pc_name, command)
        except Exception as error:
            self.repository.update_session(session)
            self.create_event(
                session.session_id,
                "command_failed",
                message=str(error),
                performed_by_user_id=actor_user_id,
            )
            raise HTTPException(
                status_code=502,
                detail=f"End session command failed: {error}",
            ) from error

        updated_session = self.finalize_ended_session(
            session,
            "manual_end",
            actor_user_id,
        )
        self.create_event(
            session.session_id,
            "open_time_ended" if session.session_mode == "open_time" else "session_ended_manually",
            seconds=updated_session.actual_elapsed_seconds,
            amount=updated_session.final_computed_amount,
            performed_by_user_id=actor_user_id,
        )

        return SessionCommandResponse(
            session=updated_session,
            command=command,
            acknowledgements=acknowledgements,
        )

    async def force_end_session(
        self,
        session_id: str,
        actor_user_id: str | None = None,
    ) -> ForceEndSessionResponse:
        session = self.repository.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.status == "settlement_pending":
            raise HTTPException(status_code=409, detail="Session is already awaiting settlement")
        if session.status not in {"active", "ending"}:
            raise HTTPException(status_code=409, detail="Session is not active")

        command: ClientCommand | None = None
        acknowledgements: list[CommandAcknowledgement] = []
        force_reason = "client_not_connected"
        should_force = not self.connection_manager.is_connected(session.pc_name)

        if not should_force:
            command = self.create_command(
                "end_session",
                {"session_id": session.session_id},
            )
            if session.status != "ending":
                ending_session = session.model_copy(update={"status": "ending"})
                self.repository.update_session(ending_session)
            try:
                acknowledgements = await self.send_required_command(session.pc_name, command)
                updated_session = self.finalize_ended_session(
                    session,
                    "manual_end",
                    actor_user_id,
                )
                self.create_event(
                    session.session_id,
                    (
                        "open_time_ended"
                        if session.session_mode == "open_time"
                        else "session_ended_manually"
                    ),
                    seconds=updated_session.actual_elapsed_seconds,
                    amount=updated_session.final_computed_amount,
                    performed_by_user_id=actor_user_id,
                )
                return ForceEndSessionResponse(
                    session=updated_session,
                    command=command,
                    acknowledgements=acknowledgements,
                    forced=False,
                    reason="normal_end_completed",
                )
            except Exception as error:
                force_reason = str(error)
                should_force = force_reason in {
                    "no_matching_local_session",
                    "session_id_mismatch",
                }
                if not should_force:
                    self.repository.update_session(session)

        if not should_force:
            self.create_event(
                session.session_id,
                "command_failed",
                message=f"Force-end normal attempt failed: {force_reason}",
                performed_by_user_id=actor_user_id,
            )
            raise HTTPException(
                status_code=502,
                detail=f"Client did not confirm recovery-safe end: {force_reason}",
            )

        updated_session = self.finalize_ended_session(
            session,
            "recovery_force_end",
            actor_user_id,
        )
        self.create_event(
            session.session_id,
            "recovery_force_end",
            seconds=updated_session.actual_elapsed_seconds,
            message=force_reason,
            amount=updated_session.final_computed_amount,
            performed_by_user_id=actor_user_id,
        )
        return ForceEndSessionResponse(
            session=updated_session,
            command=command,
            acknowledgements=acknowledgements,
            forced=True,
            reason=force_reason,
        )

    def complete_from_client(
        self,
        session_id: str,
        message: str | None = None,
    ) -> bool:
        session = self.repository.get_session(session_id)
        if session is None:
            return False

        if message == "grace_started":
            self.begin_expiration_grace(session_id)
            return True

        if session.status == "grace_period":
            return self.finalize_expiration_shutdown(session_id, message)

        if session.status == "shutting_down":
            return True

        if session.status != "active":
            return session.status in {"completed", "settlement_pending"}

        if self.repository.event_exists(session_id, "session_completed"):
            return True

        completed_session = self.finalize_ended_session(
            session,
            message or "timer_finished",
            None,
        )
        self.create_event(
            session_id,
            "session_completed",
            message=message or "Timer finished",
        )
        return True

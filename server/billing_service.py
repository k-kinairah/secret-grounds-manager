from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException

from server.payment_repository import PaymentRepository
from server.schemas import (
    BillingPaymentStatus,
    BillingSummary,
    PaymentCreateRequest,
    PaymentRecord,
    SessionEventRecord,
    SessionRecord,
)
from server.session_repository import SessionRepository
from server.time_utils import current_utc_time


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def get_payment_status(total_amount: Decimal, total_paid: Decimal) -> BillingPaymentStatus:
    normalized_total = money(total_amount)
    normalized_paid = money(total_paid)

    if normalized_paid == Decimal("0.00"):
        return "paid" if normalized_total == Decimal("0.00") else "unpaid"
    if normalized_paid < normalized_total:
        return "partially_paid"
    if normalized_paid == normalized_total:
        return "paid"
    return "overpaid"


class BillingService:
    """Derived billing rules for sessions and auditable payment records."""

    def __init__(
        self,
        session_repository: SessionRepository,
        payment_repository: PaymentRepository,
    ) -> None:
        self.session_repository = session_repository
        self.payment_repository = payment_repository

    def summarize_session(self, session: SessionRecord) -> BillingSummary:
        payments = self.payment_repository.list_payments(session.session_id)
        total_paid = money(
            sum(
                (payment.amount for payment in payments if payment.status == "completed"),
                Decimal("0.00"),
            )
        )
        balance_due = money(max(session.total_amount - total_paid, Decimal("0.00")))
        payment_status = get_payment_status(session.total_amount, total_paid)
        enriched_session = session.model_copy(
            update={
                "total_paid": total_paid,
                "balance_due": balance_due,
                "payment_status": payment_status,
            }
        )

        return BillingSummary(
            session=enriched_session,
            base_amount=session.base_amount,
            additional_amount=session.additional_amount,
            total_amount=session.total_amount,
            total_paid=total_paid,
            balance_due=balance_due,
            payment_status=payment_status,
            payments=payments,
        )

    def get_billing(self, session_id: str) -> BillingSummary:
        session = self.session_repository.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        return self.summarize_session(session)

    def enrich_session(self, session: SessionRecord) -> SessionRecord:
        return self.summarize_session(session).session

    def record_payment(
        self,
        session_id: str,
        request: PaymentCreateRequest,
    ) -> BillingSummary:
        billing = self.get_billing(session_id)
        amount = money(request.amount)

        if amount > billing.balance_due:
            raise HTTPException(
                status_code=400,
                detail="Payment amount cannot exceed the balance due",
            )

        cash_received = money(request.cash_received) if request.cash_received is not None else None
        change_given: Decimal | None = None

        if request.payment_method == "cash":
            if cash_received is None:
                cash_received = amount
            if cash_received < amount:
                raise HTTPException(
                    status_code=400,
                    detail="Cash received cannot be lower than payment amount",
                )
            change_given = money(cash_received - amount)
        elif cash_received is not None:
            raise HTTPException(
                status_code=400,
                detail="Cash received is only valid for cash payments",
            )

        payment = PaymentRecord(
            payment_id=str(uuid4()),
            session_id=session_id,
            amount=amount,
            payment_method=request.payment_method,
            cash_received=cash_received,
            change_given=change_given,
            reference_number=request.reference_number,
            notes=request.notes,
            status="completed",
            created_at=current_utc_time(),
        )
        self.payment_repository.create_payment(payment)
        self.session_repository.add_event(
            SessionEventRecord(
                event_id=str(uuid4()),
                session_id=session_id,
                event_type="payment_recorded",
                amount=amount,
                message="Payment recorded",
                created_at=current_utc_time(),
                payment_id=payment.payment_id,
                payment_method=payment.payment_method,
            )
        )

        return self.get_billing(session_id)

    def void_payment(self, payment_id: str, reason: str) -> BillingSummary:
        cleaned_reason = reason.strip()
        if not cleaned_reason:
            raise HTTPException(status_code=400, detail="Void reason is required")

        payment = self.payment_repository.get_payment(payment_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        if payment.status == "voided":
            raise HTTPException(status_code=409, detail="Payment is already voided")

        voided_payment = self.payment_repository.void_payment(payment_id)
        if voided_payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")

        self.session_repository.add_event(
            SessionEventRecord(
                event_id=str(uuid4()),
                session_id=payment.session_id,
                event_type="payment_voided",
                amount=payment.amount,
                message=cleaned_reason,
                created_at=current_utc_time(),
                payment_id=payment.payment_id,
                payment_method=payment.payment_method,
            )
        )

        return self.get_billing(payment.session_id)

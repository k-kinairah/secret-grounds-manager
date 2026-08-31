import { useRef, useState } from "react";
import styles from "@/app/page.module.css";
import { formatMoney } from "@/lib/time";
import type { PaymentInput, PaymentMethod, PcSummary } from "@/lib/types";

interface PaymentDialogProps {
  pc: PcSummary;
  isLoading: boolean;
  onCancel: () => void;
  onConfirm: (input: PaymentInput) => void;
}

const quickCash = ["exact", "20.00", "50.00", "100.00", "500.00", "1000.00"];

function createIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? `payment-${Date.now()}-${Math.random()}`;
}

function getChangeState(amount: string, cashReceived: string): { label: string; state: "muted" | "warning" | "ready" } {
  if (!cashReceived.trim()) {
    return { label: "Enter cash received", state: "muted" };
  }
  const amountValue = Number(amount);
  const cashValue = Number(cashReceived);
  if (!Number.isFinite(amountValue) || !Number.isFinite(cashValue) || cashValue <= 0 || cashValue < amountValue) {
    return { label: "Insufficient cash", state: "warning" };
  }
  return { label: formatMoney(cashValue - amountValue), state: "ready" };
}

export function PaymentDialog({ pc, isLoading, onCancel, onConfirm }: PaymentDialogProps) {
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>("cash");
  const [amount, setAmount] = useState(pc.balance_due);
  const [cashReceived, setCashReceived] = useState("");
  const [referenceNumber, setReferenceNumber] = useState("");
  const [notes, setNotes] = useState("");
  const [validationError, setValidationError] = useState("");
  const amountRef = useRef<HTMLInputElement | null>(null);
  const cashRef = useRef<HTMLInputElement | null>(null);
  const referenceRef = useRef<HTMLInputElement | null>(null);
  const idempotencyKeyRef = useRef(createIdempotencyKey());
  const changeState = getChangeState(amount, cashReceived);

  function submit() {
    if (isLoading) {
      return;
    }
    const amountValue = Number(amount);
    const balanceDue = Number(pc.balance_due);
    if (!Number.isFinite(amountValue) || amountValue <= 0 || amountValue > balanceDue) {
      setValidationError("Enter a payment amount up to the outstanding balance.");
      amountRef.current?.focus();
      return;
    }
    if (paymentMethod === "cash") {
      if (!cashReceived.trim()) {
        setValidationError("Enter the cash received.");
        cashRef.current?.focus();
        return;
      }
      const cashValue = Number(cashReceived);
      if (!Number.isFinite(cashValue) || cashValue <= 0) {
        setValidationError("Enter a valid cash amount.");
        cashRef.current?.focus();
        return;
      }
      if (cashValue < amountValue) {
        setValidationError(`Cash received must be at least ${formatMoney(amount)}.`);
        cashRef.current?.focus();
        return;
      }
    } else if (!referenceNumber.trim()) {
      setValidationError("Enter the payment reference.");
      referenceRef.current?.focus();
      return;
    }
    setValidationError("");
    onConfirm({
      payment_method: paymentMethod,
      amount,
      cash_received: paymentMethod === "cash" ? cashReceived : undefined,
      reference_number: paymentMethod !== "cash" ? referenceNumber : undefined,
      notes: notes || undefined,
      idempotency_key: idempotencyKeyRef.current
    });
  }

  return (
    <div className={styles.overlay} role="presentation">
      <section className={styles.dialog} role="dialog" aria-modal="true" aria-label="Record payment">
        <h2>Record Payment</h2>
        <div className={styles.paymentSummary}>
          <span>Outstanding balance</span>
          <strong>{formatMoney(pc.balance_due)}</strong>
        </div>
        <label className={styles.field}>
          Payment amount
          <input
            ref={amountRef}
            inputMode="decimal"
            onChange={(event) => setAmount(event.target.value)}
            value={amount}
          />
        </label>
        <label className={styles.field}>
          Payment method
          <select
            onChange={(event) => setPaymentMethod(event.target.value as PaymentMethod)}
            value={paymentMethod}
          >
            <option value="cash">Cash</option>
            <option value="gcash">GCash</option>
          </select>
        </label>
        {paymentMethod === "cash" ? (
          <>
            <div className={styles.quickAmountRow}>
              {quickCash.map((value) => (
                <button
                  key={value}
                  onClick={() => setCashReceived(value === "exact" ? amount : value)}
                  type="button"
                >
                  {value === "exact" ? "Exact" : formatMoney(value)}
                </button>
              ))}
            </div>
            <label className={styles.field}>
              Cash received
              <input
                ref={cashRef}
                inputMode="decimal"
                onChange={(event) => setCashReceived(event.target.value)}
                value={cashReceived}
              />
            </label>
            {validationError ? <p className={styles.fieldError}>{validationError}</p> : null}
            <div className={`${styles.changeSummary} ${styles[`changeSummary-${changeState.state}`]}`}>
              <span>Change</span>
              <strong>{changeState.label}</strong>
            </div>
          </>
        ) : (
          <label className={styles.field}>
            Reference number
            <input
              ref={referenceRef}
              onChange={(event) => setReferenceNumber(event.target.value)}
              placeholder="Required"
              value={referenceNumber}
            />
            {validationError ? <p className={styles.fieldError}>{validationError}</p> : null}
          </label>
        )}
        <label className={styles.field}>
          Notes
          <input
            onChange={(event) => setNotes(event.target.value)}
            placeholder="Optional"
            value={notes}
          />
        </label>
        <div className={styles.dialogActions}>
          <button className={styles.secondaryButton} disabled={isLoading} onClick={onCancel}>
            Cancel
          </button>
          <button
            className={styles.primaryButton}
            disabled={isLoading}
            onClick={submit}
          >
            {isLoading ? "Recording..." : "Record Payment"}
          </button>
        </div>
      </section>
    </div>
  );
}

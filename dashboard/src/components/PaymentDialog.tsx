import { useMemo, useState } from "react";
import styles from "@/app/page.module.css";
import { calculateCashChange, isPaymentInputValid } from "@/lib/paymentSelection";
import { formatMoney } from "@/lib/time";
import type { PaymentInput, PaymentMethod, PcSummary } from "@/lib/types";

interface PaymentDialogProps {
  pc: PcSummary;
  isLoading: boolean;
  onCancel: () => void;
  onConfirm: (input: PaymentInput) => void;
}

const quickCash = ["exact", "20.00", "50.00", "100.00", "500.00", "1000.00"];

export function PaymentDialog({ pc, isLoading, onCancel, onConfirm }: PaymentDialogProps) {
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>("cash");
  const [amount, setAmount] = useState(pc.balance_due);
  const [cashReceived, setCashReceived] = useState(pc.balance_due);
  const [referenceNumber, setReferenceNumber] = useState("");
  const [notes, setNotes] = useState("");
  const change = useMemo(
    () => calculateCashChange(amount, cashReceived || amount),
    [amount, cashReceived]
  );
  const isValid = isPaymentInputValid(paymentMethod, amount, pc.balance_due, cashReceived);

  function submit() {
    onConfirm({
      payment_method: paymentMethod,
      amount,
      cash_received: paymentMethod === "cash" ? cashReceived || amount : undefined,
      reference_number: paymentMethod !== "cash" ? referenceNumber : undefined,
      notes: notes || undefined
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
            <option value="maya">Maya</option>
            <option value="other">Other</option>
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
                inputMode="decimal"
                onChange={(event) => setCashReceived(event.target.value)}
                value={cashReceived}
              />
            </label>
            <div className={styles.paymentSummary}>
              <span>Change</span>
              <strong>{change === null ? "Invalid cash" : formatMoney(Math.max(change, 0))}</strong>
            </div>
          </>
        ) : (
          <label className={styles.field}>
            Reference number
            <input
              onChange={(event) => setReferenceNumber(event.target.value)}
              placeholder="Optional"
              value={referenceNumber}
            />
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
            disabled={isLoading || !isValid}
            onClick={submit}
          >
            {isLoading ? "Recording..." : "Record Payment"}
          </button>
        </div>
      </section>
    </div>
  );
}

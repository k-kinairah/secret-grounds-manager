import { useEffect, useRef, useState } from "react";
import styles from "@/app/page.module.css";
import { CloseButton } from "./CloseButton";
import { FancySelect, type FancySelectOption } from "./FancySelect";
import { fetchCalculatedTime, fetchMembershipProducts } from "@/lib/api";
import { formatMoney, formatNaturalDuration } from "@/lib/time";
import type { CalculatedTimeResponse, MembershipProduct, PaymentMethod, PcSummary } from "@/lib/types";

// Deliberately no ₱50/₱100 here (unlike the general Standard add-time
// chips) -- amounts that size already have dedicated Member packages
// below, so a cashier tops those up from there instead. This row exists
// for the smaller amounts a member sometimes wants that no package
// covers (e.g. half an hour), priced off the same active metered rate
// as everyone else's Standard time.
const QUICK_STANDARD_AMOUNTS = ["5", "10", "15", "20", "25", "30", "35", "40"];

const PAYMENT_METHOD_OPTIONS: FancySelectOption<PaymentMethod>[] = [
  { value: "cash", label: "Cash" },
  { value: "gcash", label: "GCash" }
];

function createIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? `member-topup-${Date.now()}-${Math.random()}`;
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

export interface MemberTopUpInput {
  mode: "package" | "custom";
  product_id?: string;
  amount?: string;
  payment_method: PaymentMethod;
  cash_received?: string;
  reference_number?: string;
  idempotency_key: string;
}

interface MemberTopUpDialogProps {
  pc: PcSummary;
  isLoading: boolean;
  onCancel: () => void;
  onConfirm: (input: MemberTopUpInput) => void;
}

type TopUpTab = "standard" | "package";

// For a PC that already has an active member_balance session, "Add Time"
// used to reopen the same Start Session UI: a member search box (pointless
// -- the member is already sitting at this PC) plus a required PIN that was
// never even sent to the backend. Add-time-member also only draws down a
// balance the member has already banked, so it fails for the common case of
// actually wanting to sell them more time. This dialog is the top-up flow
// instead: the member is already known from the PC's own session data, so
// there's nothing to search for and nothing to authenticate -- just how
// much they're paying and how. Two ways to pay, same as a non-member add
// time: fixed Member packages (Standard/VIP), or a Standard tab for a
// smaller custom cash amount priced off the same metered rate.
export function MemberTopUpDialog({ pc, isLoading, onCancel, onConfirm }: MemberTopUpDialogProps) {
  const [activeTab, setActiveTab] = useState<TopUpTab>("package");
  const [products, setProducts] = useState<MembershipProduct[]>([]);
  const [selectedProductId, setSelectedProductId] = useState("");
  const [customAmount, setCustomAmount] = useState("");
  const [calculatedTime, setCalculatedTime] = useState<CalculatedTimeResponse | null>(null);
  const [isCalculatingTime, setIsCalculatingTime] = useState(false);
  const [calculationError, setCalculationError] = useState<string | null>(null);
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>("cash");
  const [cashReceived, setCashReceived] = useState("");
  const [referenceNumber, setReferenceNumber] = useState("");
  const [validationError, setValidationError] = useState("");
  const cashRef = useRef<HTMLInputElement | null>(null);
  const referenceRef = useRef<HTMLInputElement | null>(null);
  const amountRef = useRef<HTMLInputElement | null>(null);
  const idempotencyKeyRef = useRef(createIdempotencyKey());

  const memberLabel = pc.member_name_snapshot ?? pc.member_code_snapshot ?? "this member";
  const pcDisplayName = pc.display_name ?? pc.pc_name;
  const title = `Add time for ${memberLabel}`;

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  useEffect(() => {
    let isMounted = true;
    fetchMembershipProducts()
      .then((allProducts) => {
        if (!isMounted) {
          return;
        }
        // Never the Membership Fee (initial_membership) -- that product is
        // only ever charged at account creation, same exclusion the
        // Members page top-up picker applies.
        const topups = allProducts.filter(
          (product) => product.is_active && product.product_type === "member_topup"
        );
        setProducts(topups);
        setSelectedProductId(topups[0]?.product_id ?? "");
      })
      .catch(() => {
        if (isMounted) {
          setProducts([]);
        }
      });
    return () => {
      isMounted = false;
    };
  }, []);

  const selectedProduct =
    products.find((product) => product.product_id === selectedProductId) ?? products[0] ?? null;
  const customAmountValue = Number(customAmount);
  const hasValidCustomAmount = customAmount.trim() !== "" && Number.isFinite(customAmountValue) && customAmountValue > 0;
  const isStandardTab = activeTab === "standard";

  // Same debounced amount -> time preview as the non-member Standard add-
  // time flow, against the same backend metered rate -- no separate pricing
  // logic duplicated here.
  useEffect(() => {
    if (!isStandardTab || !hasValidCustomAmount) {
      return;
    }
    let isMounted = true;
    const timeoutId = window.setTimeout(() => {
      setIsCalculatingTime(true);
      setCalculationError(null);
      fetchCalculatedTime(customAmountValue.toFixed(2))
        .then((result) => {
          if (isMounted) {
            setCalculatedTime(result);
            setIsCalculatingTime(false);
          }
        })
        .catch((error) => {
          if (isMounted) {
            setCalculatedTime(null);
            setIsCalculatingTime(false);
            setCalculationError(
              error instanceof Error ? error.message : "Unable to calculate time for this amount."
            );
          }
        });
    }, 350);
    return () => {
      isMounted = false;
      window.clearTimeout(timeoutId);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- customAmountValue is derived from customAmount each render; depending on the string is enough to refire correctly.
  }, [isStandardTab, hasValidCustomAmount, customAmount]);

  const selectedPaymentAmount = isStandardTab
    ? hasValidCustomAmount
      ? customAmountValue.toFixed(2)
      : "0.00"
    : selectedProduct?.price ?? "0.00";
  const changeState = getChangeState(selectedPaymentAmount, cashReceived);

  function validatePayment(amountValue: string): boolean {
    if (paymentMethod === "cash" && cashReceived.trim()) {
      const cashValue = Number(cashReceived);
      if (!Number.isFinite(cashValue) || cashValue <= 0) {
        setValidationError("Enter a valid cash amount.");
        cashRef.current?.focus();
        return false;
      }
      if (cashValue < Number(amountValue)) {
        setValidationError(`Cash received must be at least ${formatMoney(amountValue)}.`);
        cashRef.current?.focus();
        return false;
      }
    }
    return true;
  }

  function confirm() {
    if (isLoading) {
      return;
    }
    if (isStandardTab) {
      if (!hasValidCustomAmount || !calculatedTime) {
        setValidationError("Enter an amount and wait for the calculated time.");
        amountRef.current?.focus();
        return;
      }
      const amountValue = customAmountValue.toFixed(2);
      if (!validatePayment(amountValue)) {
        return;
      }
      setValidationError("");
      onConfirm({
        mode: "custom",
        amount: amountValue,
        payment_method: paymentMethod,
        cash_received: paymentMethod === "cash" && cashReceived.trim() ? cashReceived : undefined,
        reference_number: paymentMethod !== "cash" && referenceNumber.trim() ? referenceNumber : undefined,
        idempotency_key: idempotencyKeyRef.current
      });
      return;
    }
    if (!selectedProduct) {
      return;
    }
    if (!validatePayment(selectedProduct.price)) {
      return;
    }
    setValidationError("");
    onConfirm({
      mode: "package",
      product_id: selectedProduct.product_id,
      payment_method: paymentMethod,
      cash_received: paymentMethod === "cash" && cashReceived.trim() ? cashReceived : undefined,
      reference_number: paymentMethod !== "cash" && referenceNumber.trim() ? referenceNumber : undefined,
      idempotency_key: idempotencyKeyRef.current
    });
  }

  const confirmDisabled = isLoading || (isStandardTab ? !hasValidCustomAmount || !calculatedTime : !selectedProduct);

  return (
    <div className={styles.overlay} role="presentation">
      <section
        className={`${styles.dialog} ${styles.sessionDialogFrame}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header className={styles.sessionDialogHeader}>
          <h2>{title}</h2>
          <CloseButton disabled={isLoading} label={`Close ${title}`} onClick={onCancel} />
        </header>
        <div className={styles.sessionDialogBody}>
          <div className={styles.memberTopUpSummaryCard}>
            <div className={styles.memberTopUpSummaryName}>
              <strong>{memberLabel}</strong>
            </div>
            <div className={styles.memberTopUpSummaryStation}>
              <strong>{pcDisplayName}</strong>
            </div>
          </div>

          <div className={styles.tabList} role="tablist" aria-label="Top-up method">
            <button
              aria-selected={activeTab === "standard"}
              className={`${styles.tabButton} ${activeTab === "standard" ? styles.tabActive : ""}`}
              onClick={() => setActiveTab("standard")}
              role="tab"
              type="button"
            >
              Standard
            </button>
            <button
              aria-selected={activeTab === "package"}
              className={`${styles.tabButton} ${activeTab === "package" ? styles.tabActive : ""}`}
              onClick={() => setActiveTab("package")}
              role="tab"
              type="button"
            >
              Member
            </button>
          </div>

          {isStandardTab ? (
            <div role="tabpanel">
              <div className={styles.standardAmountPanel}>
                <div className={styles.standardAmountInputGroup}>
                  <label className={styles.field}>
                    Amount
                    <input
                      ref={amountRef}
                      inputMode="decimal"
                      onChange={(event) => setCustomAmount(event.target.value)}
                      placeholder="0.00"
                      value={customAmount}
                    />
                  </label>
                  <div className={`${styles.amountChipRow} ${styles.amountChipGrid}`}>
                    {QUICK_STANDARD_AMOUNTS.map((amount) => (
                      <button
                        className={`${styles.amountChip} ${
                          customAmount.trim() === amount ? styles.amountChipSelected : ""
                        }`}
                        key={amount}
                        onClick={() => setCustomAmount(amount)}
                        type="button"
                      >
                        ₱{amount}
                      </button>
                    ))}
                  </div>
                </div>
                <div className={styles.amountPreview}>
                  <span>Calculated time</span>
                  <strong>
                    {!customAmount.trim()
                      ? "Enter an amount to preview time"
                      : !hasValidCustomAmount
                        ? "Enter a valid amount above ₱0.00"
                        : isCalculatingTime
                          ? "Calculating..."
                          : calculationError
                            ? calculationError
                            : calculatedTime
                              ? `${formatMoney(calculatedTime.amount)} = ${formatNaturalDuration(
                                  calculatedTime.duration_seconds
                                )}`
                              : "Enter a valid amount above ₱0.00"}
                  </strong>
                </div>
              </div>
            </div>
          ) : products.length === 0 ? (
            <p className={styles.panelText}>No top-up packages are configured yet.</p>
          ) : (
            <div className={styles.standardAmountPanel}>
              <div className={`${styles.amountChipRow} ${styles.amountChipFill}`}>
                {products.map((product) => (
                  <button
                    className={`${styles.amountChip} ${
                      selectedProductId === product.product_id ? styles.amountChipSelected : ""
                    }`}
                    key={product.product_id}
                    onClick={() => setSelectedProductId(product.product_id)}
                    type="button"
                  >
                    {formatMoney(product.price)} — {formatNaturalDuration(product.time_seconds)}
                    {product.balance_type === "vip" ? " (VIP)" : ""}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className={styles.paymentBox}>
            <div className={styles.paymentRow}>
              <div className={styles.field}>
                <span>Payment method</span>
                <FancySelect
                  ariaLabel="Payment method"
                  onChange={setPaymentMethod}
                  options={PAYMENT_METHOD_OPTIONS}
                  value={paymentMethod}
                />
              </div>
              {paymentMethod === "cash" ? (
                <label className={styles.field}>
                  Cash received
                  <input
                    ref={cashRef}
                    inputMode="decimal"
                    onChange={(event) => setCashReceived(event.target.value)}
                    placeholder={selectedPaymentAmount}
                    value={cashReceived}
                  />
                </label>
              ) : (
                <label className={styles.field}>
                  Reference number
                  <input
                    ref={referenceRef}
                    onChange={(event) => setReferenceNumber(event.target.value)}
                    placeholder="e.g. GC-102938"
                    value={referenceNumber}
                  />
                </label>
              )}
            </div>
            {paymentMethod === "cash" ? (
              <div className={`${styles.changeSummary} ${styles[`changeSummary-${changeState.state}`]}`}>
                <span>Change</span>
                <strong>{changeState.label}</strong>
              </div>
            ) : null}
          </div>
        </div>

        <footer className={styles.sessionDialogFooter}>
          {validationError ? <p className={styles.formError}>{validationError}</p> : null}
          <div className={styles.dialogActions}>
            <button className={styles.primaryButton} disabled={confirmDisabled} onClick={confirm}>
              {isLoading ? "Working..." : "Confirm"}
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}

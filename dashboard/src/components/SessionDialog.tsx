import { useEffect, useRef, useState } from "react";
import styles from "@/app/page.module.css";
import { CloseButton } from "./CloseButton";
import { FancySelect, type FancySelectOption } from "./FancySelect";
import { fetchCalculatedTime, fetchMemberVipPreview, fetchMembers } from "@/lib/api";
import { formatMoney, formatNaturalDuration, minutesToSeconds } from "@/lib/time";
import type {
  CalculatedTimeResponse,
  CurrentUser,
  MemberRecord,
  MemberSessionSelection,
  MemberVipPlayablePreview,
  PaymentMethod,
  PcSummary,
  PrepaidSessionSelection,
  RateProfile
} from "@/lib/types";

const QUICK_STANDARD_AMOUNTS = ["5", "10", "15", "20", "30", "40", "50", "100"];

const PAYMENT_METHOD_OPTIONS: FancySelectOption<PaymentMethod>[] = [
  { value: "cash", label: "Cash" },
  { value: "gcash", label: "GCash" }
];

type DialogMode = "start" | "add";
type SelectionTab = "standard" | "member";
type SessionSelection = PrepaidSessionSelection | MemberSessionSelection;

interface SessionDialogProps {
  mode: DialogMode;
  pc: PcSummary;
  packageRates: RateProfile[];
  isLoading: boolean;
  onCancel: () => void;
  onConfirm: (selection: SessionSelection) => void;
  currentOperator?: CurrentUser | null;
  operators?: CurrentUser[];
}

function createIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? `session-payment-${Date.now()}-${Math.random()}`;
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

export function SessionDialog({
  mode,
  pc,
  packageRates,
  isLoading,
  onCancel,
  onConfirm,
  currentOperator
}: SessionDialogProps) {
  const [activeTab, setActiveTab] = useState<SelectionTab>("standard");
  const [selectedRateId, setSelectedRateId] = useState<string | "custom">(
    packageRates[0]?.rate_id ?? "custom"
  );
  const [customMinutes, setCustomMinutes] = useState("30");
  const [customAmount, setCustomAmount] = useState("");
  const [calculatedTime, setCalculatedTime] = useState<CalculatedTimeResponse | null>(null);
  const [isCalculatingTime, setIsCalculatingTime] = useState(false);
  const [calculationError, setCalculationError] = useState<string | null>(null);
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>("cash");
  const [cashReceived, setCashReceived] = useState("");
  const [referenceNumber, setReferenceNumber] = useState("");
  const [members, setMembers] = useState<MemberRecord[]>([]);
  const [memberQuery, setMemberQuery] = useState("");
  const [selectedMember, setSelectedMember] = useState<MemberRecord | null>(null);
  const [memberPin, setMemberPin] = useState("");
  const [validationError, setValidationError] = useState("");
  const [vipPreview, setVipPreview] = useState<MemberVipPlayablePreview | null>(null);
  const cashRef = useRef<HTMLInputElement | null>(null);
  const referenceRef = useRef<HTMLInputElement | null>(null);
  const amountRef = useRef<HTMLInputElement | null>(null);
  const idempotencyKeyRef = useRef(createIdempotencyKey());
  const pcDisplayName = pc.display_name ?? pc.pc_name;
  const title = mode === "start" ? `Start ${pcDisplayName}` : `Add time to ${pcDisplayName}`;
  // Standard tab: the amount-based flow (chips + free amount) on every
  // station. On a VIP PC the backend prices that amount off the Standard
  // metered rate scaled by the configured VIP multiplier; the live preview
  // below passes pc_name so it shows the same VIP-scaled minutes.
  const isStandardAmountMode = activeTab === "standard";
  const selectedPackage = packageRates.find((rate) => rate.rate_id === selectedRateId);
  const customAmountValue = Number(customAmount);
  const hasValidCustomAmount = customAmount.trim() !== "" && Number.isFinite(customAmountValue) && customAmountValue > 0;
  const selectedPaymentAmount = isStandardAmountMode
    ? hasValidCustomAmount
      ? customAmountValue.toFixed(2)
      : "0.00"
    : selectedRateId === "custom"
      ? "0.00"
      : selectedPackage?.price ?? "0.00";
  const changeState = getChangeState(selectedPaymentAmount, cashReceived);
  const selectedMemberId = selectedMember?.member_id ?? "";
  const isMemberPinValid = memberPin.length >= 4 && memberPin.length <= 6;
  // "Usable balance" depends on the station: a VIP station plays off the
  // combined VIP+Standard-at-2x preview, a Standard station plays off the
  // member's own Standard balance directly.
  const isVipPreviewPending = pc.is_vip && Boolean(selectedMember) && vipPreview === null;
  const hasUsableMemberBalance = selectedMember
    ? pc.is_vip
      ? (vipPreview?.playable_seconds ?? 0) > 0
      : selectedMember.time_balance_seconds > 0
    : false;
  const isMemberSelectionValid = Boolean(selectedMember) && isMemberPinValid && hasUsableMemberBalance;
  // Only shown once we actually know the balance is unusable -- for a VIP
  // station that means waiting for the (async) VIP preview to load first,
  // so this never flashes true while it's still "Calculating...".
  const showNoUsableBalanceMessage =
    Boolean(selectedMember) && !isVipPreviewPending && !hasUsableMemberBalance;

  // Same body-scroll lock pattern as ActivityFeed/SalesManager -- this
  // dialog is only ever mounted while open (the parent conditionally
  // renders it), so locking for the component's whole lifetime is enough.
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  // Only searches while no member is selected yet -- once one is chosen,
  // the search field/results disappear in favor of the compact selected-
  // member card, so there is nothing left to search for. No setState for
  // the "not applicable" case: `members` is only ever rendered behind a
  // `!selectedMember && memberQuery.trim()` check in the JSX below, so a
  // stale list left over from a previous query can never actually be
  // shown once either condition stops holding.
  useEffect(() => {
    let isMounted = true;
    if (selectedMember || !memberQuery.trim()) {
      return;
    }
    fetchMembers(memberQuery)
      .then((nextMembers) => {
        if (!isMounted) {
          return;
        }
        const playableMembers = nextMembers.filter(
          (member) => member.status === "active" && !member.requires_account_setup && member.username
        );
        setMembers(playableMembers);
      })
      .catch(() => {
        if (isMounted) {
          setMembers([]);
        }
      });
    return () => {
      isMounted = false;
    };
  }, [memberQuery, selectedMember]);

  useEffect(() => {
    let isMounted = true;
    if (!pc.is_vip || !selectedMemberId) {
      const timeoutId = window.setTimeout(() => {
        if (isMounted) {
          setVipPreview(null);
        }
      }, 0);
      return () => {
        isMounted = false;
        window.clearTimeout(timeoutId);
      };
    }
    // Always the full (uncapped) preview -- the "maximum time" cap concept
    // was removed from the member flow, so this always reflects the
    // member's actual full playable VIP time.
    fetchMemberVipPreview(selectedMemberId, pc.pc_name)
      .then((preview) => {
        if (isMounted) {
          setVipPreview(preview);
        }
      })
      .catch(() => {
        if (isMounted) {
          setVipPreview(null);
        }
      });
    return () => {
      isMounted = false;
    };
  }, [pc.is_vip, pc.pc_name, selectedMemberId]);

  // Amount -> time preview uses the backend's active metered rate (never a
  // duplicated ₱-per-minute calculation in the dashboard), debounced so a
  // fetch is not fired on every keystroke while the cashier is still typing.
  useEffect(() => {
    // No setState for the "not applicable" cases -- calculatedTime/
    // isCalculatingTime/calculationError are only ever read (both at
    // render time and inside confirm()) behind their own
    // isStandardAmountMode/hasValidCustomAmount checks, so a stale value
    // left over from a previous amount can never actually be shown or
    // submitted; confirm() also independently re-checks that
    // calculatedTime.amount matches the amount being submitted before
    // trusting it. Nothing to clear.
    if (!isStandardAmountMode || !hasValidCustomAmount) {
      return;
    }
    let isMounted = true;
    // "Calculating..." only starts once the debounce delay elapses and
    // the fetch actually begins (inside the timeout callback, not
    // synchronously here) -- both more honest UI (no spinner during the
    // debounce wait itself) and keeps every setState call in this effect
    // inside a callback rather than the effect body's own synchronous run.
    const timeoutId = window.setTimeout(() => {
      setIsCalculatingTime(true);
      setCalculationError(null);
      fetchCalculatedTime(customAmountValue.toFixed(2), pc.pc_name)
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
  }, [isStandardAmountMode, hasValidCustomAmount, customAmount]);

  function buildPayment(paymentAmount: string) {
    return {
      payment_method: paymentMethod,
      amount: paymentAmount,
      cash_received: paymentMethod === "cash" && cashReceived.trim() ? cashReceived : undefined,
      reference_number: paymentMethod !== "cash" && referenceNumber.trim() ? referenceNumber : undefined,
      idempotency_key: idempotencyKeyRef.current
    };
  }

  function validatePayment(paymentAmount: string): boolean {
    const amountValue = Number(paymentAmount);
    if (!Number.isFinite(amountValue) || amountValue < 0) {
      setValidationError("Enter a valid payment amount.");
      return false;
    }
    if (amountValue === 0) {
      setValidationError("");
      return true;
    }
    if (paymentMethod === "cash" && cashReceived.trim()) {
      const cashValue = Number(cashReceived);
      if (!Number.isFinite(cashValue) || cashValue <= 0) {
        setValidationError("Enter a valid cash amount.");
        cashRef.current?.focus();
        return false;
      }
      if (cashValue < amountValue) {
        setValidationError(`Cash received must be at least ${formatMoney(paymentAmount)}.`);
        cashRef.current?.focus();
        return false;
      }
    }
    setValidationError("");
    return true;
  }

  function confirm() {
    if (isLoading) {
      return;
    }
    if (activeTab === "member") {
      if (!selectedMember || !isMemberPinValid) {
        setValidationError("Select a member and enter a valid 4-6 digit PIN.");
        return;
      }
      if (!hasUsableMemberBalance) {
        setValidationError(
          pc.is_vip
            ? "This member has no playable VIP time."
            : "This member has no remaining time balance."
        );
        return;
      }
      setValidationError("");
      onConfirm({
        sessionMode: "member_balance",
        memberUsername: selectedMember.username ?? "",
        memberId: selectedMember.member_id,
        // No session-length cap in the simplified flow -- the session
        // runs until ended or the balance is exhausted, same as before
        // when "Set maximum time" was left unchecked.
        durationSeconds: undefined,
        memberPin
      });
      return;
    }

    if (isStandardAmountMode) {
      if (!hasValidCustomAmount) {
        setValidationError("Enter a valid amount above ₱0.00.");
        amountRef.current?.focus();
        return;
      }
      const normalizedAmount = customAmountValue.toFixed(2);
      if (
        isCalculatingTime ||
        !calculatedTime ||
        Number(calculatedTime.amount).toFixed(2) !== normalizedAmount
      ) {
        setValidationError(calculationError ?? "Waiting for the calculated time preview.");
        return;
      }
      if (!validatePayment(normalizedAmount)) {
        return;
      }
      onConfirm({ amount: normalizedAmount, payment: buildPayment(normalizedAmount) });
      return;
    }

    if (selectedRateId === "custom") {
      setValidationError("");
      onConfirm({
        durationSeconds: minutesToSeconds(Number(customMinutes)),
        payment: buildPayment("0.00")
      });
      return;
    }

    if (!validatePayment(selectedPackage?.price ?? "0.00")) {
      return;
    }
    onConfirm({ rateId: selectedRateId, payment: buildPayment(selectedPackage?.price ?? "0.00") });
  }

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
        {pc.connection_status === "offline" ? (
          <div className={styles.amountPreview}>
            <span>Power state</span>
            <strong>
              PC is currently powered off. The system will send a wake signal, wait for the client, then start the session automatically.
            </strong>
          </div>
        ) : null}
        {mode === "start" ? (
          // Starting a brand-new session always draws from the PC's own
          // Standard/VIP pricing -- a member's existing balance is meant to
          // be spent through their own kiosk login, not a cashier-assisted
          // Start here, so there is nothing to toggle to. The pill stays
          // (just non-interactive) so this still reads as "Standard" the
          // same way the add-time dialogs do.
          <div className={styles.tabList} role="tablist" aria-label="Session type">
            <div
              aria-selected="true"
              className={`${styles.tabButton} ${styles.tabActive} ${styles.tabButtonStatic}`}
              role="tab"
            >
              {pc.is_vip ? "VIP" : "Standard"}
            </div>
          </div>
        ) : (
          <div className={styles.tabList} role="tablist" aria-label="Session selection method">
            <button
              aria-selected={activeTab === "standard"}
              className={`${styles.tabButton} ${activeTab === "standard" ? styles.tabActive : ""}`}
              onClick={() => setActiveTab("standard")}
              role="tab"
              type="button"
            >
              {pc.is_vip ? "VIP" : "Standard"}
            </button>
            <button
              aria-selected={activeTab === "member"}
              className={`${styles.tabButton} ${activeTab === "member" ? styles.tabActive : ""}`}
              onClick={() => setActiveTab("member")}
              role="tab"
              type="button"
            >
              Member
            </button>
          </div>
        )}

        {activeTab === "member" ? (
          <div className={styles.amountPanel} role="tabpanel">
            {!selectedMember ? (
              <>
                <label className={styles.field}>
                  Find member
                  <input
                    onChange={(event) => setMemberQuery(event.target.value)}
                    placeholder="Search username, name, or phone"
                    value={memberQuery}
                  />
                </label>
                {memberQuery.trim() ? (
                  members.length > 0 ? (
                    <div className={styles.memberResultList} role="listbox" aria-label="Matching members">
                      {members.map((member) => (
                        <button
                          aria-selected={false}
                          className={styles.memberResultItem}
                          key={member.member_id}
                          onClick={() => {
                            setSelectedMember(member);
                            setMemberQuery("");
                          }}
                          role="option"
                          type="button"
                        >
                          <strong>{member.display_name ?? member.username ?? member.full_name}</strong>
                          <span>
                            {member.username ? `@${member.username}` : member.full_name} ·{" "}
                            {formatNaturalDuration(member.time_balance_seconds)}
                          </span>
                        </button>
                      ))}
                    </div>
                  ) : (
                    <p className={styles.panelText}>No matching members.</p>
                  )
                ) : null}
              </>
            ) : (
              <div className={styles.memberCard}>
                <div className={styles.memberCardHeader}>
                  <div>
                    <strong>{selectedMember.display_name ?? selectedMember.username ?? selectedMember.full_name}</strong>
                    {selectedMember.username ? <span>@{selectedMember.username}</span> : null}
                  </div>
                  <button
                    className={styles.secondaryButton}
                    onClick={() => {
                      setSelectedMember(null);
                      setMemberPin("");
                    }}
                    type="button"
                  >
                    Change
                  </button>
                </div>
                <div className={styles.compactDetailList}>
                  <div className={styles.detailRow}>
                    <span>Available time balance</span>
                    <strong>{formatNaturalDuration(selectedMember.time_balance_seconds)}</strong>
                  </div>
                  <div className={styles.detailRow}>
                    <span>Status</span>
                    <strong>{selectedMember.status === "active" ? "Active" : "Inactive"}</strong>
                  </div>
                </div>
              </div>
            )}
            <label className={styles.field}>
              Member PIN
              <input
                inputMode="numeric"
                maxLength={6}
                minLength={4}
                onChange={(event) => setMemberPin(event.target.value)}
                placeholder="4-6 digits"
                type="password"
                value={memberPin}
              />
            </label>
            <p className={styles.panelText}>
              Only the time actually used will be deducted from the member&apos;s balance.
            </p>
            {pc.is_vip && selectedMember ? (
              <div className={styles.amountPreview}>
                <div className={styles.detailRow}>
                  <span>Member balance</span>
                  <strong>{formatNaturalDuration(selectedMember.time_balance_seconds)}</strong>
                </div>
                <div className={styles.detailRow}>
                  <span>Available VIP playtime</span>
                  <strong>
                    {vipPreview ? formatNaturalDuration(vipPreview.playable_seconds) : "Calculating..."}
                  </strong>
                </div>
                {vipPreview && vipPreview.standard_seconds > 0 ? (
                  <span className={styles.panelText}>VIP uses balance at 2× rate</span>
                ) : null}
              </div>
            ) : null}
            {showNoUsableBalanceMessage ? (
              <p className={styles.formError}>
                {pc.is_vip ? "This member has no playable VIP time." : "This member has no remaining time balance."}
              </p>
            ) : null}
          </div>
        ) : (
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
        )}

        {activeTab !== "member" ? (
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
        ) : null}
        </div>

        <footer className={styles.sessionDialogFooter}>
          {validationError ? <p className={styles.formError}>{validationError}</p> : null}
          <div className={styles.dialogActions}>
            <button
              className={styles.primaryButton}
              disabled={
                isLoading ||
                (mode === "start" && !currentOperator) ||
                (activeTab === "member" && !isMemberSelectionValid)
              }
              onClick={confirm}
            >
              {isLoading
                ? "Working..."
                : pc.connection_status === "offline" && mode === "start"
                  ? "Start & Wake PC"
                  : "Confirm"}
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}

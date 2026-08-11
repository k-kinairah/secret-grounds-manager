import { useEffect, useRef, useState } from "react";
import styles from "@/app/page.module.css";
import { fetchMemberVipPreview, fetchMembers } from "@/lib/api";
import { formatClockTime, formatMoney, formatNaturalDuration, formatRateDuration, minutesToSeconds } from "@/lib/time";
import type {
  CurrentUser,
  MemberRecord,
  MemberSessionSelection,
  MemberVipPlayablePreview,
  PaymentMethod,
  PcSummary,
  PrepaidSessionSelection,
  RateProfile
} from "@/lib/types";

type DialogMode = "start" | "add";
type SelectionTab = "packages" | "member";
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
  const [activeTab, setActiveTab] = useState<SelectionTab>("packages");
  const [selectedRateId, setSelectedRateId] = useState<string | "custom">(
    packageRates[0]?.rate_id ?? "custom"
  );
  const [customMinutes, setCustomMinutes] = useState("30");
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>("cash");
  const [cashReceived, setCashReceived] = useState("");
  const [referenceNumber, setReferenceNumber] = useState("");
  const [notes, setNotes] = useState("");
  const [members, setMembers] = useState<MemberRecord[]>([]);
  const [memberQuery, setMemberQuery] = useState("");
  const [selectedMemberId, setSelectedMemberId] = useState("");
  const [memberPin, setMemberPin] = useState("");
  const [validationError, setValidationError] = useState("");
  const [useMemberCap, setUseMemberCap] = useState(false);
  const [memberMinutes, setMemberMinutes] = useState("60");
  const [vipPreview, setVipPreview] = useState<MemberVipPlayablePreview | null>(null);
  const cashRef = useRef<HTMLInputElement | null>(null);
  const referenceRef = useRef<HTMLInputElement | null>(null);
  const idempotencyKeyRef = useRef(createIdempotencyKey());
  const title = mode === "start" ? `Start ${pc.pc_name}` : `Add time to ${pc.pc_name}`;
  const selectedPackage = packageRates.find((rate) => rate.rate_id === selectedRateId);
  const selectedPaymentAmount = selectedRateId === "custom" ? "0.00" : selectedPackage?.price ?? "0.00";
  const changeState = getChangeState(selectedPaymentAmount, cashReceived);
  const selectedMember = members.find((member) => member.member_id === selectedMemberId) ?? null;
  const memberDurationMinutes = Number(memberMinutes || "0");
  const memberDurationSeconds =
    Number.isFinite(memberDurationMinutes) && memberDurationMinutes > 0
      ? minutesToSeconds(memberDurationMinutes)
      : 0;
  const memberBalanceAfter =
    selectedMember === null
      ? null
      : selectedMember.time_balance_seconds - (useMemberCap ? memberDurationSeconds : 0);

  useEffect(() => {
    let isMounted = true;
    fetchMembers(memberQuery)
      .then((nextMembers) => {
        if (!isMounted) {
          return;
        }
        const playableMembers = nextMembers.filter(
          (member) => member.status === "active" && !member.requires_account_setup && member.username
        );
        setMembers(playableMembers);
        if (!selectedMemberId && playableMembers[0]) {
          setSelectedMemberId(playableMembers[0].member_id);
        }
      })
      .catch(() => {
        if (isMounted) {
          setMembers([]);
        }
      });
    return () => {
      isMounted = false;
    };
  }, [memberQuery, selectedMemberId]);

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
    const cap = useMemberCap && memberDurationSeconds > 0 ? memberDurationSeconds : undefined;
    fetchMemberVipPreview(selectedMemberId, pc.pc_name, cap)
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
  }, [pc.is_vip, pc.pc_name, selectedMemberId, useMemberCap, memberDurationSeconds]);

  function buildPayment(paymentAmount: string) {
    return {
      payment_method: paymentMethod,
      amount: paymentAmount,
      cash_received: paymentMethod === "cash" ? cashReceived : undefined,
      reference_number: paymentMethod !== "cash" ? referenceNumber : undefined,
      notes: notes || undefined,
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
    if (paymentMethod === "cash") {
      if (!cashReceived.trim()) {
        setValidationError("Enter the cash received.");
        cashRef.current?.focus();
        return false;
      }
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
    } else if (!referenceNumber.trim()) {
      setValidationError("Enter the payment reference.");
      referenceRef.current?.focus();
      return false;
    }
    setValidationError("");
    return true;
  }

  function confirm() {
    if (isLoading) {
      return;
    }
    if (activeTab === "member") {
      if (
        !selectedMember ||
        memberPin.length < 4 ||
        memberPin.length > 6 ||
        (useMemberCap && memberDurationSeconds <= 0) ||
        memberBalanceAfter === null ||
        memberBalanceAfter < 0
      ) {
        setValidationError("Select a member and enter a valid 4-6 digit PIN.");
        return;
      }
      setValidationError("");
      onConfirm({
        sessionMode: "member_balance",
        memberUsername: selectedMember.username ?? "",
        memberId: selectedMember.member_id,
        durationSeconds: useMemberCap ? memberDurationSeconds : undefined,
        memberPin
      });
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
      <section className={styles.dialog} role="dialog" aria-modal="true" aria-label={title}>
        <h2>{title}</h2>
        <div className={styles.amountPreview}>
          <span>Operator</span>
          <strong>{currentOperator?.display_name ?? "Select an on-duty operator before starting."}</strong>
          {pc.connection_status === "offline" ? (
            <>
              <span>Power state</span>
              <strong>
                PC is currently powered off. The system will send a wake signal, wait for the client, then start the session automatically.
              </strong>
            </>
          ) : null}
        </div>
        <div className={styles.tabList} role="tablist" aria-label="Session selection method">
          <button
            aria-selected={activeTab === "packages"}
            className={`${styles.tabButton} ${activeTab === "packages" ? styles.tabActive : ""}`}
            onClick={() => setActiveTab("packages")}
            role="tab"
            type="button"
          >
            Packages
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

        {activeTab === "member" ? (
          <div className={styles.amountPanel} role="tabpanel">
            <label className={styles.field}>
              Search username
              <input
                onChange={(event) => setMemberQuery(event.target.value)}
                placeholder="Username, display name, or phone"
                value={memberQuery}
              />
            </label>
            <label className={styles.field}>
              Member username
              <select
                onChange={(event) => setSelectedMemberId(event.target.value)}
                value={selectedMemberId}
              >
                {members.map((member) => (
                  <option key={member.member_id} value={member.member_id}>
                    {member.username} - {member.full_name} (Std {formatNaturalDuration(
                      member.time_balance_seconds
                    )} · VIP {formatNaturalDuration(member.vip_balance_seconds)})
                  </option>
                ))}
              </select>
            </label>
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
            {selectedMember ? (
              <div className={styles.memberBalanceGrid}>
                <div className={styles.amountPreview}>
                  <span>Standard Time</span>
                  <strong>{formatNaturalDuration(selectedMember.time_balance_seconds)}</strong>
                </div>
                <div className={styles.amountPreview}>
                  <span>VIP Time</span>
                  <strong>{formatNaturalDuration(selectedMember.vip_balance_seconds)}</strong>
                </div>
              </div>
            ) : null}
            <div className={styles.amountPreview}>
              <span>Member Balance</span>
              <strong>Only the time actually used will be deducted.</strong>
            </div>
            <label className={styles.field}>
              <span>
                <input
                  checked={useMemberCap}
                  onChange={(event) => setUseMemberCap(event.target.checked)}
                  type="checkbox"
                />{" "}
                Set maximum time
              </span>
            </label>
            {useMemberCap ? (
              <>
                <div className={styles.quickAmountRow}>
                  {[
                    ["30", "30 min"],
                    ["60", "1 hour"],
                    ["120", "2 hours"]
                  ].map(([minutes, label]) => (
                    <button key={minutes} onClick={() => setMemberMinutes(minutes)} type="button">
                      {label}
                    </button>
                  ))}
                </div>
                <label className={styles.field}>
                  Custom minutes
                  <input
                    inputMode="numeric"
                    onChange={(event) => setMemberMinutes(event.target.value)}
                    value={memberMinutes}
                  />
                </label>
              </>
            ) : null}
            {pc.is_vip ? (
              <div className={styles.amountPreview}>
                <span>Playable on VIP</span>
                <strong>
                  {!selectedMember
                    ? "Select a member"
                    : vipPreview
                      ? vipPreview.playable_seconds > 0
                        ? formatNaturalDuration(vipPreview.playable_seconds)
                        : "Insufficient balance for this VIP station"
                      : "Calculating..."}
                </strong>
                {selectedMember && vipPreview && vipPreview.playable_seconds > 0 ? (
                  <div className={styles.vipPreviewBreakdown}>
                    <span>VIP balance: {formatNaturalDuration(vipPreview.vip_seconds)}</span>
                    {vipPreview.standard_seconds > 0 ? (
                      <span>
                        Standard balance: {formatNaturalDuration(vipPreview.standard_seconds)} contributes{" "}
                        {formatNaturalDuration(vipPreview.standard_real_seconds_equivalent)} of VIP play
                      </span>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ) : null}
            <div className={styles.amountPreview}>
              <span>Balance preview</span>
              <strong>
                {selectedMember
                  ? useMemberCap
                    ? `Current balance: ${formatNaturalDuration(
                        selectedMember.time_balance_seconds
                      )}. Maximum session: ${formatNaturalDuration(memberDurationSeconds)}. ${
                        memberBalanceAfter !== null && memberBalanceAfter >= 0
                          ? "Unused time remains in the account."
                          : "Insufficient balance"
                      }`
                    : `Current balance: ${formatNaturalDuration(
                        selectedMember.time_balance_seconds
                      )}. Session can continue until ended or balance is exhausted.`
                  : "Select a member"}
              </strong>
            </div>
          </div>
        ) : (
          <div role="tabpanel">
            <div className={styles.optionGrid}>
              {packageRates.map((rate) => {
                const isTimeLimited = Boolean(
                  rate.eligible_start_local_time && rate.eligible_end_local_time
                );
                const promoLabel = rate.name.replace(/^Standard\s+|^VIP\s+/i, "").toUpperCase();
                return (
                  <button
                    className={`${styles.rateOption} ${
                      selectedRateId === rate.rate_id ? styles.rateOptionSelected : ""
                    } ${isTimeLimited ? styles.rateOptionPromo : ""}`}
                    key={rate.rate_id}
                    onClick={() => setSelectedRateId(rate.rate_id)}
                    type="button"
                  >
                    {isTimeLimited ? (
                      <span className={styles.ratePromoLabel}>{promoLabel}</span>
                    ) : null}
                    <span className={styles.ratePrice}>{formatMoney(rate.price)}</span>
                    <span className={styles.rateDuration}>
                      {formatRateDuration(rate.duration_seconds)}
                    </span>
                    {isTimeLimited ? (
                      <span className={styles.ratePromoWindow}>
                        {formatClockTime(rate.eligible_start_local_time as string)}
                        {"–"}
                        {formatClockTime(rate.eligible_end_local_time as string)}
                        {rate.guest_only ? " · Guest only" : ""}
                      </span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {activeTab !== "member" ? (
          <div className={styles.paymentBox}>
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
          </div>
        ) : null}

        {validationError ? <p className={styles.formError}>{validationError}</p> : null}
        <div className={styles.dialogActions}>
          <button className={styles.secondaryButton} disabled={isLoading} onClick={onCancel}>
            Cancel
          </button>
          <button
            className={styles.primaryButton}
            disabled={isLoading || (mode === "start" && !currentOperator)}
            onClick={confirm}
          >
            {isLoading
              ? "Working..."
              : pc.connection_status === "offline" && mode === "start"
                ? "Start & Wake PC"
                : "Confirm"}
          </button>
        </div>
      </section>
    </div>
  );
}

import { useEffect, useMemo, useState } from "react";
import styles from "@/app/page.module.css";
import { fetchMembers } from "@/lib/api";
import { calculateCashChange } from "@/lib/paymentSelection";
import { calculatePreviewSeconds } from "@/lib/sessionSelection";
import { formatMoney, formatRateDuration, minutesToSeconds } from "@/lib/time";
import type { MemberRecord, MemberSessionSelection, PaymentMethod, PcSummary, PrepaidSessionSelection, RateProfile } from "@/lib/types";

type DialogMode = "start" | "add";
type SelectionTab = "amount" | "packages" | "member" | "open_time";
type SessionSelection = PrepaidSessionSelection | MemberSessionSelection | { sessionMode: "open_time" };

interface SessionDialogProps {
  mode: DialogMode;
  pc: PcSummary;
  meteredRate: RateProfile | null;
  packageRates: RateProfile[];
  isLoading: boolean;
  onCancel: () => void;
  onConfirm: (selection: SessionSelection) => void;
}

const quickAmounts = ["5.00", "10.00", "20.00", "50.00"];

export function SessionDialog({
  mode,
  pc,
  meteredRate,
  packageRates,
  isLoading,
  onCancel,
  onConfirm
}: SessionDialogProps) {
  const [activeTab, setActiveTab] = useState<SelectionTab>("amount");
  const [amount, setAmount] = useState("");
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
  const [useMemberCap, setUseMemberCap] = useState(false);
  const [memberMinutes, setMemberMinutes] = useState("60");
  const title = mode === "start" ? `Start ${pc.pc_name}` : `Add time to ${pc.pc_name}`;
  const previewSeconds = useMemo(
    () => calculatePreviewSeconds(amount, meteredRate),
    [amount, meteredRate]
  );
  const selectedPackage = packageRates.find((rate) => rate.rate_id === selectedRateId);
  const selectedPaymentAmount =
    activeTab === "amount"
      ? amount
      : selectedRateId === "custom"
        ? "0.00"
        : selectedPackage?.price ?? "0.00";
  const change = calculateCashChange(
    selectedPaymentAmount,
    cashReceived || selectedPaymentAmount
  );
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
        setMembers(nextMembers.filter((member) => member.status === "active"));
        if (!selectedMemberId && nextMembers[0]) {
          setSelectedMemberId(nextMembers[0].member_id);
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

  function buildPayment(paymentAmount: string) {
    return {
      payment_method: paymentMethod,
      amount: paymentAmount,
      cash_received: paymentMethod === "cash" ? cashReceived || paymentAmount : undefined,
      reference_number: paymentMethod !== "cash" ? referenceNumber : undefined,
      notes: notes || undefined
    };
  }

  function confirm() {
    if (activeTab === "open_time") {
      onConfirm({ sessionMode: "open_time" });
      return;
    }

    if (activeTab === "member") {
      if (
        !selectedMember ||
        (useMemberCap && memberDurationSeconds <= 0) ||
        memberBalanceAfter === null ||
        memberBalanceAfter < 0
      ) {
        return;
      }
      onConfirm({
        sessionMode: "member_balance",
        memberId: selectedMember.member_id,
        durationSeconds: useMemberCap ? memberDurationSeconds : undefined
      });
      return;
    }

    if (activeTab === "amount") {
      onConfirm({ amount, payment: buildPayment(amount) });
      return;
    }

    if (selectedRateId === "custom") {
      onConfirm({
        durationSeconds: minutesToSeconds(Number(customMinutes)),
        payment: buildPayment("0.00")
      });
      return;
    }

    onConfirm({ rateId: selectedRateId, payment: buildPayment(selectedPackage?.price ?? "0.00") });
  }

  return (
    <div className={styles.overlay} role="presentation">
      <section className={styles.dialog} role="dialog" aria-modal="true" aria-label={title}>
        <h2>{title}</h2>
        <div className={styles.tabList} role="tablist" aria-label="Session selection method">
          <button
            aria-selected={activeTab === "amount"}
            className={`${styles.tabButton} ${activeTab === "amount" ? styles.tabActive : ""}`}
            onClick={() => setActiveTab("amount")}
            role="tab"
            type="button"
          >
            Enter Amount
          </button>
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
          {mode === "start" ? (
            <button
              aria-selected={activeTab === "open_time"}
              className={`${styles.tabButton} ${activeTab === "open_time" ? styles.tabActive : ""}`}
              onClick={() => setActiveTab("open_time")}
              role="tab"
              type="button"
            >
              Open Time
            </button>
          ) : null}
        </div>

        {activeTab === "open_time" ? (
          <div className={styles.amountPreview} role="tabpanel">
            <span>Open Time</span>
            <strong>Charges are calculated from actual usage when the session ends.</strong>
            <span>Current regular rate</span>
            <strong>
              {meteredRate
                ? `${formatMoney(meteredRate.price)} = ${formatRateDuration(
                    meteredRate.duration_seconds
                  )}`
                : "No active metered rate"}
            </strong>
          </div>
        ) : activeTab === "member" ? (
          <div className={styles.amountPanel} role="tabpanel">
            <label className={styles.field}>
              Search member
              <input
                onChange={(event) => setMemberQuery(event.target.value)}
                placeholder="Code, name, or phone"
                value={memberQuery}
              />
            </label>
            <label className={styles.field}>
              Member
              <select
                onChange={(event) => setSelectedMemberId(event.target.value)}
                value={selectedMemberId}
              >
                {members.map((member) => (
                  <option key={member.member_id} value={member.member_id}>
                    {member.member_code} - {member.full_name} ({formatRateDuration(member.time_balance_seconds)})
                  </option>
                ))}
              </select>
            </label>
            <div className={styles.amountPreview}>
              <span>Member Open Time</span>
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
            <div className={styles.amountPreview}>
              <span>Balance preview</span>
              <strong>
                {selectedMember
                  ? useMemberCap
                    ? `Current balance: ${formatRateDuration(
                        selectedMember.time_balance_seconds
                      )}. Maximum session: ${formatRateDuration(memberDurationSeconds)}. ${
                        memberBalanceAfter !== null && memberBalanceAfter >= 0
                          ? "Unused time remains in the account."
                          : "Insufficient balance"
                      }`
                    : `Current balance: ${formatRateDuration(
                        selectedMember.time_balance_seconds
                      )}. Session can continue until ended or balance is exhausted.`
                  : "Select a member"}
              </strong>
            </div>
          </div>
        ) : activeTab === "amount" ? (
          <div className={styles.amountPanel} role="tabpanel">
            <label className={styles.moneyField}>
              Amount
              <span>
                ₱
                <input
                  inputMode="decimal"
                  onChange={(event) => setAmount(event.target.value)}
                  placeholder="20.00"
                  value={amount}
                />
              </span>
            </label>
            <div className={styles.quickAmountRow}>
              {quickAmounts.map((quickAmount) => (
                <button key={quickAmount} onClick={() => setAmount(quickAmount)} type="button">
                  {formatMoney(quickAmount)}
                </button>
              ))}
            </div>
            <div className={styles.amountPreview}>
              <span>Active base rate</span>
              <strong>
                {meteredRate
                  ? `${formatMoney(meteredRate.price)} = ${formatRateDuration(
                      meteredRate.duration_seconds
                    )}`
                  : "No active metered rate"}
              </strong>
              <span>Preview</span>
              <strong>
                Amount: {amount ? formatMoney(amount) : formatMoney(0)} · Time:{" "}
                {previewSeconds ? formatRateDuration(previewSeconds) : "Enter an amount"}
              </strong>
            </div>
          </div>
        ) : (
          <div role="tabpanel">
            <div className={styles.optionGrid}>
              {packageRates.map((rate) => (
                <button
                  className={`${styles.rateOption} ${
                    selectedRateId === rate.rate_id ? styles.rateOptionSelected : ""
                  }`}
                  key={rate.rate_id}
                  onClick={() => setSelectedRateId(rate.rate_id)}
                  type="button"
                >
                  <span className={styles.rateTitle}>{rate.name}</span>
                  <span className={styles.rateDuration}>
                    {formatRateDuration(rate.duration_seconds)}
                  </span>
                  <span className={styles.ratePrice}>{formatMoney(rate.price)}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {activeTab !== "open_time" && activeTab !== "member" ? (
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
                    inputMode="decimal"
                    onChange={(event) => setCashReceived(event.target.value)}
                    placeholder={selectedPaymentAmount}
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
          </div>
        ) : null}

        <div className={styles.dialogActions}>
          <button className={styles.secondaryButton} disabled={isLoading} onClick={onCancel}>
            Cancel
          </button>
          <button className={styles.primaryButton} disabled={isLoading} onClick={confirm}>
            {isLoading ? "Working..." : "Confirm"}
          </button>
        </div>
      </section>
    </div>
  );
}

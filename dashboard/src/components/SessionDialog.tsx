import { useMemo, useState } from "react";
import styles from "@/app/page.module.css";
import { calculatePreviewSeconds } from "@/lib/sessionSelection";
import { formatMoney, formatRateDuration, minutesToSeconds } from "@/lib/time";
import type { PcSummary, RateProfile } from "@/lib/types";

type DialogMode = "start" | "add";
type SessionSelection = { amount?: string; seconds?: number; rateId?: string };
type SelectionTab = "amount" | "packages";

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
  const title = mode === "start" ? `Start ${pc.pc_name}` : `Add time to ${pc.pc_name}`;
  const previewSeconds = useMemo(
    () => calculatePreviewSeconds(amount, meteredRate),
    [amount, meteredRate]
  );

  function confirm() {
    if (activeTab === "amount") {
      onConfirm({ amount });
      return;
    }

    if (selectedRateId === "custom") {
      onConfirm({ seconds: minutesToSeconds(Number(customMinutes)) });
      return;
    }

    onConfirm({ rateId: selectedRateId });
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
        </div>

        {activeTab === "amount" ? (
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
                <button
                  key={quickAmount}
                  onClick={() => setAmount(quickAmount)}
                  type="button"
                >
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
              <button
                className={`${styles.rateOption} ${
                  selectedRateId === "custom" ? styles.rateOptionSelected : ""
                }`}
                onClick={() => setSelectedRateId("custom")}
                type="button"
              >
                <span className={styles.rateTitle}>Custom Time</span>
                <span className={styles.rateDuration}>Enter minutes</span>
                <span className={styles.ratePrice}>{formatMoney(0)}</span>
              </button>
            </div>
            {selectedRateId === "custom" ? (
              <label className={styles.field}>
                Custom minutes
                <input
                  min="0.5"
                  onChange={(event) => setCustomMinutes(event.target.value)}
                  step="0.5"
                  type="number"
                  value={customMinutes}
                />
              </label>
            ) : null}
          </div>
        )}

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

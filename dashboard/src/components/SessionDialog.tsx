import { useState } from "react";
import styles from "@/app/page.module.css";
import { formatDuration, formatMoney, minutesToSeconds } from "@/lib/time";
import type { PcSummary, RateProfile } from "@/lib/types";

type DialogMode = "start" | "add";

interface SessionDialogProps {
  mode: DialogMode;
  pc: PcSummary;
  rates: RateProfile[];
  isLoading: boolean;
  onCancel: () => void;
  onConfirm: (selection: { seconds?: number; rateId?: string }) => void;
}

export function SessionDialog({
  mode,
  pc,
  rates,
  isLoading,
  onCancel,
  onConfirm
}: SessionDialogProps) {
  const [selectedRateId, setSelectedRateId] = useState<string | "custom">(
    rates[0]?.rate_id ?? "custom"
  );
  const [customMinutes, setCustomMinutes] = useState("30");
  const title = mode === "start" ? `Start ${pc.pc_name}` : `Add time to ${pc.pc_name}`;

  function confirm() {
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
        <div className={styles.optionGrid}>
          {rates.map((rate) => (
            <button
              className={styles.secondaryButton}
              key={rate.rate_id}
              onClick={() => setSelectedRateId(rate.rate_id)}
              type="button"
            >
              <strong>{rate.name}</strong>
              <span>{formatDuration(rate.duration_seconds)}</span>
              <span>Price {formatMoney(rate.price)}</span>
            </button>
          ))}
          <button
            className={styles.secondaryButton}
            onClick={() => setSelectedRateId("custom")}
            type="button"
          >
            <strong>Custom Time</strong>
            <span>No package price</span>
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
        ) : (
          <p>
            Selected package:{" "}
            {rates.find((rate) => rate.rate_id === selectedRateId)?.name ?? "Rate"}
          </p>
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

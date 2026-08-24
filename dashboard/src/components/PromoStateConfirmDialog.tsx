"use client";

import { useState } from "react";
import dialogStyles from "@/app/page.module.css";
import styles from "@/app/promotions/promotions.module.css";
import type { PromoTransitionAction } from "@/lib/promoForm";
import type { Promo } from "@/lib/types";

interface PromoStateConfirmDialogProps {
  promo: Promo;
  action: PromoTransitionAction;
  isLoading: boolean;
  onCancel: () => void;
  onConfirm: (endsAtIso?: string) => void;
}

const ACTION_COPY: Record<
  PromoTransitionAction,
  { title: string; verb: string; danger: boolean; description: string }
> = {
  enable: {
    title: "Enable Promo",
    verb: "Enable",
    danger: false,
    description: "This promo becomes eligible for redemption as soon as its start date/time is reached."
  },
  disable: {
    title: "Disable Promo",
    verb: "Disable",
    danger: true,
    description: "This promo will stop being offered immediately. Existing redemption history is preserved."
  },
  end: {
    title: "End Promo",
    verb: "End",
    danger: true,
    description: "This promo is permanently marked Ended and can only be brought back via Reactivate."
  },
  reactivate: {
    title: "Reactivate Promo",
    verb: "Reactivate",
    danger: false,
    description: "This ended promo becomes enabled again with a new end date/time you choose below."
  }
};

export function PromoStateConfirmDialog({
  promo,
  action,
  isLoading,
  onCancel,
  onConfirm
}: PromoStateConfirmDialogProps) {
  const [reactivateEndsAt, setReactivateEndsAt] = useState("");
  const copy = ACTION_COPY[action];
  const needsEndsAt = action === "reactivate";

  function handleConfirm() {
    if (needsEndsAt) {
      const date = new Date(reactivateEndsAt);
      if (Number.isNaN(date.getTime())) {
        return;
      }
      onConfirm(date.toISOString());
      return;
    }
    onConfirm();
  }

  return (
    <div className={dialogStyles.overlay} role="presentation">
      <section
        aria-label={copy.title}
        aria-modal="true"
        className={dialogStyles.confirmDialog}
        role="dialog"
      >
        <h2>{copy.title}</h2>
        <p>
          {copy.description} <strong>{promo.name}</strong>
        </p>
        {needsEndsAt ? (
          <label className={styles.field} style={{ marginBottom: 18 }}>
            New end date/time
            <input
              min={new Date().toISOString().slice(0, 16)}
              onChange={(event) => setReactivateEndsAt(event.target.value)}
              required
              type="datetime-local"
              value={reactivateEndsAt}
            />
          </label>
        ) : null}
        <div className={dialogStyles.dialogActions}>
          <button
            className={dialogStyles.secondaryButton}
            disabled={isLoading}
            onClick={onCancel}
            type="button"
          >
            Cancel
          </button>
          <button
            className={copy.danger ? dialogStyles.dangerButton : dialogStyles.primaryButton}
            disabled={isLoading || (needsEndsAt && !reactivateEndsAt)}
            onClick={handleConfirm}
            type="button"
          >
            {isLoading ? `${copy.verb}ing...` : copy.verb}
          </button>
        </div>
      </section>
    </div>
  );
}

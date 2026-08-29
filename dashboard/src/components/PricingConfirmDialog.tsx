"use client";

import type { CSSProperties } from "react";
import dialogStyles from "@/app/page.module.css";
import type { PricingChangeRow } from "./SettingsManager";

// Same disclaimer wording the Rates & Pricing panel shows.
const PRICING_DISCLAIMER =
  "Changes apply to new sessions and new purchases only. Running sessions, existing balances, and past transactions are unaffected.";

interface PricingConfirmDialogProps {
  changes: PricingChangeRow[];
  isLoading: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

const CELL: CSSProperties = { padding: "8px 10px", verticalAlign: "top", textAlign: "left" };
const HEAD_CELL: CSSProperties = { ...CELL, fontWeight: 600, opacity: 0.7, fontSize: 12 };

export function PricingConfirmDialog({
  changes,
  isLoading,
  onCancel,
  onConfirm
}: PricingConfirmDialogProps) {
  const hasChanges = changes.length > 0;

  return (
    <div className={dialogStyles.overlay} role="presentation">
      <section
        aria-label="Confirm pricing changes"
        aria-modal="true"
        className={dialogStyles.confirmDialog}
        role="dialog"
      >
        <h2>Confirm pricing changes</h2>

        {hasChanges ? (
          <>
            <p>
              These rows will be rewritten. Nothing is saved until you choose{" "}
              <strong>Apply pricing</strong>.
            </p>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                margin: "6px 0 14px"
              }}
            >
              <thead>
                <tr>
                  <th style={HEAD_CELL}>Row</th>
                  <th style={HEAD_CELL}>Current</th>
                  <th style={HEAD_CELL}>New</th>
                </tr>
              </thead>
              <tbody>
                {changes.map((row) => (
                  <tr key={row.key} style={{ borderTop: "1px solid rgba(148, 163, 184, 0.28)" }}>
                    <td style={CELL}>
                      <div style={{ fontWeight: 600 }}>{row.label}</div>
                      {row.sublabel ? (
                        <div style={{ opacity: 0.6, fontSize: 12, marginTop: 2 }}>{row.sublabel}</div>
                      ) : null}
                    </td>
                    <td style={{ ...CELL, opacity: 0.7 }}>{row.beforeText}</td>
                    <td style={{ ...CELL, fontWeight: 600 }}>{row.afterText}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p style={{ opacity: 0.7, fontSize: 12.5, margin: 0 }}>{PRICING_DISCLAIMER}</p>
          </>
        ) : (
          <p>No effective pricing changes.</p>
        )}

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
            className={dialogStyles.primaryButton}
            disabled={isLoading || !hasChanges}
            onClick={onConfirm}
            type="button"
          >
            {isLoading ? "Applying…" : "Apply pricing"}
          </button>
        </div>
      </section>
    </div>
  );
}

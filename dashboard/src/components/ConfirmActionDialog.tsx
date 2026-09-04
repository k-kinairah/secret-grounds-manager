import type { ReactNode } from "react";
import styles from "@/app/page.module.css";
import { CloseButton } from "./CloseButton";

type ConfirmTone = "danger" | "warning" | "info" | "locked";

interface ConfirmActionDialogProps {
  title: string;
  lede: ReactNode;
  confirmLabel: string;
  onCancel: () => void;
  onConfirm: () => void;
  tone?: ConfirmTone;
  facts?: { label: string; value: ReactNode }[];
  note?: ReactNode;
  isLoading?: boolean;
  /** Center the body text and enlarge the lede -- for short yes/no prompts. */
  centered?: boolean;
}

/**
 * A compact, premium confirmation dialog: header (title + X), a body with a
 * lede line, an optional key/value fact card, an optional helper note, and
 * a single centered confirm button. The `tone` prop recolours the whole
 * frame (rose / amber / cyan) via CSS custom properties.
 */
export function ConfirmActionDialog({
  title,
  lede,
  confirmLabel,
  onCancel,
  onConfirm,
  tone = "danger",
  facts,
  note,
  isLoading = false,
  centered = false
}: ConfirmActionDialogProps) {
  return (
    <div className={styles.overlay} role="presentation">
      <section
        className={`${styles.dialog} ${styles.confirmDialog}`}
        data-centered={centered ? "" : undefined}
        data-tone={tone}
        role="alertdialog"
        aria-modal="true"
        aria-label={title}
      >
        <header className={styles.confirmDialogHeader}>
          <h2>{title}</h2>
          <CloseButton disabled={isLoading} label={`Close ${title}`} onClick={onCancel} />
        </header>

        <div className={styles.confirmDialogBody}>
          <p className={styles.confirmDialogLede}>{lede}</p>

          {facts && facts.length > 0 ? (
            <dl className={styles.confirmDialogFacts}>
              {facts.map((fact) => (
                <div key={fact.label}>
                  <span>{fact.label}</span>
                  <strong>{fact.value}</strong>
                </div>
              ))}
            </dl>
          ) : null}

          {note ? <p className={styles.confirmDialogNote}>{note}</p> : null}
        </div>

        <footer className={styles.confirmDialogFooter}>
          <button
            className={styles.confirmDialogConfirm}
            disabled={isLoading}
            onClick={onConfirm}
            type="button"
          >
            {confirmLabel}
          </button>
        </footer>
      </section>
    </div>
  );
}

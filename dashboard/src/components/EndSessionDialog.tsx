import styles from "@/app/page.module.css";
import { formatMoney, formatTimer } from "@/lib/time";
import type { PcSummary } from "@/lib/types";
import { CloseButton } from "./CloseButton";

interface EndSessionDialogProps {
  pc: PcSummary;
  isLoading: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  recoveryMode?: boolean;
}

export function EndSessionDialog({
  pc,
  isLoading,
  onCancel,
  onConfirm,
  recoveryMode = false
}: EndSessionDialogProps) {
  const pcName = pc.display_name ?? pc.pc_name;
  const isOpenTime = pc.session_mode === "open_time";
  const title = recoveryMode ? "Resolve Session" : "End Session";
  const confirmLabel = isLoading
    ? "Ending..."
    : recoveryMode
      ? "Force End"
      : isOpenTime
        ? "End & Settle"
        : "End Session";

  return (
    <div className={styles.overlay} role="presentation">
      <section
        className={`${styles.dialog} ${styles.endSessionDialog}`}
        role="dialog"
        aria-modal="true"
        aria-label={`End ${pcName} session`}
      >
        <header className={styles.endSessionHeader}>
          <h2>{title}</h2>
          <CloseButton disabled={isLoading} label={`Close ${title}`} onClick={onCancel} />
        </header>

        <div className={styles.endSessionBody}>
          <p className={styles.endSessionLede}>
            {recoveryMode ? "Force-end the unresolved session on " : "End the current session on "}
            <strong>{pcName}</strong>?
          </p>

          <dl className={styles.endSessionFacts}>
            {isOpenTime ? (
              <>
                <div>
                  <span>Elapsed time</span>
                  <strong>{formatTimer(pc.actual_elapsed_seconds)}</strong>
                </div>
                <div>
                  <span>Estimated total</span>
                  <strong>{formatMoney(pc.estimated_total_amount)}</strong>
                </div>
              </>
            ) : (
              <div>
                <span>Remaining time</span>
                <strong>{formatTimer(pc.remaining_seconds)}</strong>
              </div>
            )}
          </dl>

          {recoveryMode ? (
            <p className={styles.endSessionNote}>
              The client reported no matching local session, so it will remain locked. The
              server will preserve history and calculate any final Open Time amount from
              server timestamps.
            </p>
          ) : isOpenTime ? (
            <p className={styles.endSessionNote}>
              The server will calculate the final amount after ending, then open the
              settlement dialog.
            </p>
          ) : (
            <p className={styles.endSessionNote}>
              The client will lock immediately after the command completes.
            </p>
          )}
        </div>

        <footer className={styles.endSessionFooter}>
          <button className={styles.dangerButton} disabled={isLoading} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </footer>
      </section>
    </div>
  );
}

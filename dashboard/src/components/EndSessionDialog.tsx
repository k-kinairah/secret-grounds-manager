import styles from "@/app/page.module.css";
import { formatMoney, formatTimer } from "@/lib/time";
import type { PcSummary } from "@/lib/types";

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
  return (
    <div className={styles.overlay} role="presentation">
      <section
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-label={`End ${pc.display_name ?? pc.pc_name} session`}
      >
        <h2>{recoveryMode ? "Resolve Session" : "End Session"}</h2>
        <p>
          {recoveryMode ? "Force-end the unresolved session on " : "End the current session on "}
          <strong>{pc.display_name ?? pc.pc_name}</strong>?
        </p>
        {recoveryMode ? (
          <p>
            The client reported no matching local session, so it will remain locked. The
            server will preserve history and calculate any final Open Time amount from
            server timestamps.
          </p>
        ) : null}
        {pc.session_mode === "open_time" ? (
          <>
            <p>Elapsed time: {formatTimer(pc.actual_elapsed_seconds)}</p>
            <p>Estimated total: {formatMoney(pc.estimated_total_amount)}</p>
            <p>
              The server will calculate the final amount after ending, then open the
              settlement dialog.
            </p>
          </>
        ) : (
          <>
            <p>Remaining time: {formatTimer(pc.remaining_seconds)}</p>
            <p>The client will lock immediately after the command completes.</p>
          </>
        )}
        <div className={styles.dialogActions}>
          <button className={styles.secondaryButton} disabled={isLoading} onClick={onCancel}>
            Cancel
          </button>
          <button className={styles.dangerButton} disabled={isLoading} onClick={onConfirm}>
            {isLoading
              ? "Ending..."
              : recoveryMode
                ? "Force End"
                : pc.session_mode === "open_time"
                  ? "End & Settle"
                  : "End Session"}
          </button>
        </div>
      </section>
    </div>
  );
}

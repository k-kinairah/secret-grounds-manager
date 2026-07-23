import styles from "@/app/page.module.css";
import { formatDuration } from "@/lib/time";
import type { PcSummary } from "@/lib/types";

interface EndSessionDialogProps {
  pc: PcSummary;
  isLoading: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function EndSessionDialog({
  pc,
  isLoading,
  onCancel,
  onConfirm
}: EndSessionDialogProps) {
  return (
    <div className={styles.overlay} role="presentation">
      <section
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-label={`End ${pc.pc_name} session`}
      >
        <h2>End Session</h2>
        <p>
          End the current session on <strong>{pc.pc_name}</strong>?
        </p>
        <p>Remaining time: {formatDuration(pc.remaining_seconds)}</p>
        <p>The client will lock immediately after the command completes.</p>
        <div className={styles.dialogActions}>
          <button className={styles.secondaryButton} disabled={isLoading} onClick={onCancel}>
            Cancel
          </button>
          <button className={styles.dangerButton} disabled={isLoading} onClick={onConfirm}>
            {isLoading ? "Ending..." : "End Session"}
          </button>
        </div>
      </section>
    </div>
  );
}

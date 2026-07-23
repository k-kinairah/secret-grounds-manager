import styles from "@/app/page.module.css";
import { formatDateTime, formatMoney, formatTimer } from "@/lib/time";
import type { PcSummary } from "@/lib/types";

interface PcDetailsPanelProps {
  pc: PcSummary;
  onClose: () => void;
}

export function PcDetailsPanel({ pc, onClose }: PcDetailsPanelProps) {
  return (
    <div className={styles.overlay} role="presentation">
      <aside className={styles.sidePanel} aria-label={`${pc.pc_name} details`}>
        <h2>{pc.pc_name}</h2>
        <dl className={styles.detailList}>
          <div className={styles.detailRow}>
            <span>Connection</span>
            <strong>{pc.connection_status}</strong>
          </div>
          <div className={styles.detailRow}>
            <span>Session status</span>
            <strong>{pc.session_status}</strong>
          </div>
          <div className={styles.detailRow}>
            <span>IP address</span>
            <strong>{pc.ip_address}</strong>
          </div>
          <div className={styles.detailRow}>
            <span>Last seen</span>
            <strong>{formatDateTime(pc.last_seen)}</strong>
          </div>
          <div className={styles.detailRow}>
            <span>Active session</span>
            <strong>{pc.active_session_id ?? "None"}</strong>
          </div>
          <div className={styles.detailRow}>
            <span>Scheduled end</span>
            <strong>{formatDateTime(pc.scheduled_end_at)}</strong>
          </div>
          <div className={styles.detailRow}>
            <span>Remaining</span>
            <strong>{formatTimer(pc.remaining_seconds)}</strong>
          </div>
          {pc.active_session_id ? (
            <>
              <div className={styles.detailRow}>
                <span>Rate</span>
                <strong>{pc.rate_name_snapshot ?? "Custom Time"}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Base amount</span>
                <strong>{formatMoney(pc.base_amount)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Additional</span>
                <strong>{formatMoney(pc.additional_amount)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Total</span>
                <strong>{formatMoney(pc.total_amount)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Paid</span>
                <strong>{formatMoney(pc.total_paid)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Balance due</span>
                <strong>{formatMoney(pc.balance_due)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Payment status</span>
                <strong>{pc.payment_status}</strong>
              </div>
            </>
          ) : null}
        </dl>
        <div className={styles.dialogActions}>
          <button className={styles.secondaryButton} onClick={onClose}>
            Close
          </button>
        </div>
      </aside>
    </div>
  );
}

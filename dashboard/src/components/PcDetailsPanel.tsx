import styles from "@/app/page.module.css";
import { formatDateTime, formatMoney, formatTimer } from "@/lib/time";
import type { PcSummary } from "@/lib/types";

interface PcDetailsPanelProps {
  pc: PcSummary;
  onClose: () => void;
}

export function PcDetailsPanel({ pc, onClose }: PcDetailsPanelProps) {
  const isSettlementPending = pc.session_status === "settlement_pending";
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
            <span>{pc.session_mode === "open_time" ? "Elapsed" : "Remaining"}</span>
            <strong>
              {formatTimer(
                pc.session_mode === "open_time"
                  ? pc.actual_elapsed_seconds
                  : pc.remaining_seconds
              )}
            </strong>
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
                <span>
                  {pc.session_mode === "open_time" && pc.session_status === "active"
                    ? "Estimated total"
                    : isSettlementPending
                      ? "Final amount"
                    : "Total"}
                </span>
                <strong>
                  {formatMoney(
                    pc.session_mode === "open_time" && pc.session_status === "active"
                      ? pc.estimated_total_amount
                      : pc.total_amount
                  )}
                </strong>
              </div>
              <div className={styles.detailRow}>
                <span>Paid</span>
                <strong>{formatMoney(pc.total_paid)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>
                  {pc.session_mode === "open_time" && pc.session_status === "active"
                    ? "Estimated balance"
                    : isSettlementPending
                      ? "Balance"
                    : "Balance due"}
                </span>
                <strong>
                  {formatMoney(
                    pc.session_mode === "open_time" && pc.session_status === "active"
                      ? pc.estimated_balance_due
                      : pc.balance_due
                  )}
                </strong>
              </div>
              <div className={styles.detailRow}>
                <span>Payment status</span>
                <strong>{pc.payment_status}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Session mode</span>
                <strong>{pc.session_mode}</strong>
              </div>
              {pc.session_mode === "open_time" ? (
                <>
                  <div className={styles.detailRow}>
                    <span>Billed time</span>
                    <strong>{formatTimer(pc.billed_seconds)}</strong>
                  </div>
                  <div className={styles.detailRow}>
                    <span>Final amount</span>
                    <strong>{formatMoney(pc.final_computed_amount)}</strong>
                  </div>
                </>
              ) : null}
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

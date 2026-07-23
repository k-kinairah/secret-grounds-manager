import styles from "@/app/page.module.css";
import { formatDateTime, formatMoney, formatTimer } from "@/lib/time";
import { getPcActionAvailability, getPcCardTone } from "@/lib/pcStatus";
import type { PcSummary } from "@/lib/types";

interface PcCardProps {
  pc: PcSummary;
  onStart: (pc: PcSummary) => void;
  onAddTime: (pc: PcSummary) => void;
  onEnd: (pc: PcSummary) => void;
  onDetails: (pc: PcSummary) => void;
  onPayment: (pc: PcSummary) => void;
}

const toneClassName = {
  active: styles.pcCardActive,
  available: styles.pcCardAvailable,
  offline: styles.pcCardOffline,
  warning: styles.pcCardWarning
};

const connectionBadgeClassName = {
  online: styles.badgeOnline,
  offline: styles.badgeOffline
};

export function PcCard({ pc, onStart, onAddTime, onEnd, onDetails, onPayment }: PcCardProps) {
  const actions = getPcActionAvailability(pc);
  const tone = getPcCardTone(pc);
  const connectionLabel = pc.connection_status === "online" ? "Online" : "Offline";
  const sessionLabel =
    pc.session_status === "active"
      ? "In Use"
      : pc.session_status === "unknown"
        ? "Needs Review"
        : "Available";
  const sessionBadgeClassName =
    pc.session_status === "active"
      ? styles.badgeActive
      : pc.session_status === "unknown"
        ? styles.badgeWarning
        : styles.badgeIdle;
  const isOpenTime = pc.session_mode === "open_time";

  return (
    <article className={`${styles.pcCard} ${toneClassName[tone]}`}>
      <button
        aria-label={`View ${pc.pc_name} details`}
        onClick={() => onDetails(pc)}
        className={styles.pcCardButton}
      >
        <div className={styles.pcHeader}>
          <div className={styles.pcName}>{pc.pc_name}</div>
          <div className={styles.badgeRow}>
            <span
              className={`${styles.statusPill} ${
                connectionBadgeClassName[pc.connection_status]
              }`}
            >
              {connectionLabel}
            </span>
            <span className={`${styles.sessionPill} ${sessionBadgeClassName}`}>
              {sessionLabel}
            </span>
          </div>
        </div>
        {pc.session_status === "active" ? (
          <div className={styles.timerBlock}>
            <div className={styles.timerLabel}>{isOpenTime ? "Elapsed" : "Remaining"}</div>
            <div className={styles.timerValue}>
              {formatTimer(isOpenTime ? pc.actual_elapsed_seconds : pc.remaining_seconds)}
            </div>
          </div>
        ) : (
          <div className={styles.availableBlock}>
            {pc.session_status === "unknown" ? "Recovery Review" : "Available"}
          </div>
        )}
        <dl className={styles.detailList}>
          <div className={styles.detailRow}>
            <span>{pc.connection_status === "offline" ? "Last seen" : "IP address"}</span>
            <strong>
              {pc.connection_status === "offline" ? formatDateTime(pc.last_seen) : pc.ip_address}
            </strong>
          </div>
          {pc.session_status === "active" ? (
            <>
              <div className={styles.detailRow}>
                <span>Current total</span>
                <strong>{formatMoney(pc.total_amount)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Paid</span>
                <strong>{formatMoney(pc.total_paid)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Balance</span>
                <strong>{formatMoney(pc.balance_due)}</strong>
              </div>
            </>
          ) : null}
        </dl>
      </button>
      <div className={styles.actions}>
        <button
          className={styles.primaryButton}
          disabled={!actions.canStart}
          onClick={() => onStart(pc)}
        >
          Start Session
        </button>
        <button
          className={styles.secondaryButton}
          disabled={!actions.canAddTime || isOpenTime}
          onClick={() => onAddTime(pc)}
        >
          Add Time
        </button>
        <button
          className={styles.secondaryButton}
          disabled={!actions.canAddTime}
          onClick={() => onPayment(pc)}
        >
          {isOpenTime && pc.settlement_pending ? "Settle" : "Payment"}
        </button>
        <button
          className={styles.dangerButton}
          disabled={!actions.canEnd}
          onClick={() => onEnd(pc)}
        >
          End Session
        </button>
      </div>
    </article>
  );
}

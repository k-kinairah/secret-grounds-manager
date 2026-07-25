import styles from "@/app/page.module.css";
import { formatDateTime, formatMoney, formatTimer } from "@/lib/time";
import { getPcActionAvailability, getPcCardTone } from "@/lib/pcStatus";
import type { PcSummary, UserRole } from "@/lib/types";

interface PcCardProps {
  pc: PcSummary;
  onStart: (pc: PcSummary) => void;
  onAddTime: (pc: PcSummary) => void;
  onEnd: (pc: PcSummary) => void;
  onResolve: (pc: PcSummary) => void;
  onDetails: (pc: PcSummary) => void;
  onPayment: (pc: PcSummary) => void;
  currentUserRole: UserRole | null;
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

export function PcCard({
  pc,
  onStart,
  onAddTime,
  onEnd,
  onResolve,
  onDetails,
  onPayment,
  currentUserRole
}: PcCardProps) {
  const actions = getPcActionAvailability(pc);
  const tone = getPcCardTone(pc);
  const connectionLabel = pc.connection_status === "online" ? "Online" : "Offline";
  const sessionLabel =
    pc.session_status === "active"
      ? "In Use"
      : pc.session_status === "ending"
        ? "Ending"
        : pc.session_status === "settlement_pending"
          ? "Settlement Pending"
      : pc.session_status === "unknown"
        ? "Needs Review"
        : "Available";
  const sessionBadgeClassName =
    pc.recovery_required
      ? styles.badgeWarning
      : pc.session_status === "active"
      ? styles.badgeActive
      : pc.session_status === "ending" || pc.session_status === "settlement_pending"
        ? styles.badgeWarning
      : pc.session_status === "unknown"
        ? styles.badgeWarning
        : styles.badgeIdle;
  const isOpenTime = pc.session_mode === "open_time";
  const isMemberSession = pc.session_mode === "member_balance";
  const isSettlementPending = pc.session_status === "settlement_pending";
  const canResolve =
    pc.recovery_required && (currentUserRole === "owner" || currentUserRole === "admin");

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
              {pc.recovery_required ? "Recovery Required" : sessionLabel}
            </span>
          </div>
        </div>
        {pc.session_status === "active" || pc.session_status === "ending" ? (
          <div className={styles.timerBlock}>
            <div className={styles.timerLabel}>
              {isOpenTime || isMemberSession ? "Elapsed" : "Remaining"}
            </div>
            <div className={styles.timerValue}>
              {formatTimer(
                isOpenTime || isMemberSession
                  ? pc.actual_elapsed_seconds
                  : pc.remaining_seconds
              )}
            </div>
          </div>
        ) : isSettlementPending ? (
          <div className={styles.availableBlock}>Settlement Pending</div>
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
          {pc.session_status === "active" || pc.session_status === "ending" || isSettlementPending ? (
            <>
              <div className={styles.detailRow}>
                <span>
                  {isSettlementPending
                    ? "Final amount"
                    : isOpenTime
                      ? "Estimated total"
                      : isMemberSession
                        ? "Funded by"
                      : "Current total"}
                </span>
                <strong>
                  {isMemberSession
                    ? pc.member_name_snapshot ?? pc.member_code_snapshot ?? "Member balance"
                    : formatMoney(
                        isSettlementPending
                          ? pc.total_amount
                          : isOpenTime
                            ? pc.estimated_total_amount
                            : pc.total_amount
                      )}
                </strong>
              </div>
              {isMemberSession ? (
                <div className={styles.detailRow}>
                  <span>Estimated balance</span>
                  <strong>{formatTimer(pc.member_balance_after_seconds)}</strong>
                </div>
              ) : null}
              <div className={styles.detailRow}>
                <span>Paid</span>
                <strong>{formatMoney(pc.total_paid)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>{isOpenTime && !isSettlementPending ? "Estimated balance" : "Balance"}</span>
                <strong>
                  {formatMoney(
                    isOpenTime && !isSettlementPending
                      ? pc.estimated_balance_due
                      : pc.balance_due
                  )}
                </strong>
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
          disabled={pc.recovery_required || (!actions.canAddTime && !actions.canCollectPayment)}
          onClick={() => (isOpenTime && !isSettlementPending ? onEnd(pc) : onPayment(pc))}
        >
          {isSettlementPending ? "Collect Payment" : isOpenTime ? "End & Settle" : "Payment"}
        </button>
        <button
          className={styles.dangerButton}
          disabled={!actions.canEnd || (pc.recovery_required && !canResolve)}
          onClick={() => (pc.recovery_required ? onResolve(pc) : onEnd(pc))}
        >
          {pc.recovery_required ? (canResolve ? "Resolve Session" : "Needs Admin") : "End Session"}
        </button>
      </div>
    </article>
  );
}

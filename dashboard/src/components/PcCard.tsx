import styles from "@/app/page.module.css";
import { formatMoney, formatTimer } from "@/lib/time";
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
  onRetryWake: (pc: PcSummary) => void;
  onCancelPrepared: (pc: PcSummary) => void;
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
  onRetryWake,
  onCancelPrepared,
  currentUserRole
}: PcCardProps) {
  const actions = getPcActionAvailability(pc);
  const tone = getPcCardTone(pc);
  const displayName = pc.display_name ?? pc.pc_name;
  const connectionLabel =
    pc.power_state === "waking"
      ? "Waking"
      : pc.power_state === "starting"
        ? "Starting"
        : pc.power_state === "shutting_down"
          ? "Shutting Down"
          : pc.power_state === "shutdown_failed"
            ? "Shutdown Failed"
        : pc.connection_status === "online"
          ? "Online"
          : "Powered Off";
  const sessionLabel =
    pc.session_status === "active"
      ? pc.customer_locked
        ? "Active · Customer Locked"
        : "In Use"
      : pc.session_status === "grace_period"
        ? "Grace Period"
      : pc.session_status === "shutting_down"
        ? "Shutting Down"
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
      : pc.session_status === "ending" ||
          pc.session_status === "grace_period" ||
          pc.session_status === "shutting_down" ||
          pc.session_status === "settlement_pending"
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
    <article className={`${styles.pcCard} ${styles.monitorCard} ${toneClassName[tone]}`}>
      <button
        aria-label={`View ${displayName} details`}
        onClick={() => onDetails(pc)}
        className={styles.pcCardButton}
      >
        <div className={styles.pcAreaRow}>
          <span>{pc.area_label ?? "Secret Grounds"}</span>
          {pc.is_vip ? (
            <strong>VIP</strong>
          ) : (
            <span className={styles.standardBadge}>Standard</span>
          )}
        </div>
        <div className={styles.pcHeader}>
          <div className={styles.pcName}>{displayName}</div>
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
        {pc.startup_status !== null ? (
          <div className={styles.timerBlock}>
            <div className={styles.timerLabel}>
              {pc.startup_elapsed_seconds !== null && pc.startup_elapsed_seconds >= 90
                ? "Startup warning"
                : "Waiting for client"}
            </div>
            <div className={styles.timerValue}>{formatTimer(pc.startup_elapsed_seconds)}</div>
            <p className={styles.panelText}>
              {pc.startup_elapsed_seconds !== null && pc.startup_elapsed_seconds >= 30
                ? `Still waiting for ${displayName} to connect.`
                : "Wake signal sent. Client will start automatically after Windows boots."}
            </p>
          </div>
        ) : pc.session_status === "active" || pc.session_status === "ending" ? (
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
            {pc.connection_status === "offline"
              ? pc.mac_address
                ? "Available for Wake-on-LAN"
                : "Powered off"
              : pc.session_status === "unknown"
                ? "Recovery Review"
                : "Available"}
          </div>
        )}
        <dl className={styles.detailList}>
          <div className={styles.detailRow}>
            <span>{pc.connection_status === "offline" ? "Power" : "State"}</span>
            <strong>
              {pc.connection_status === "offline" ? "Powered Off" : connectionLabel}
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
          {pc.connection_status === "offline" ? "Start & Wake" : "Start Session"}
        </button>
        {pc.startup_status !== null ? (
          <>
            <button className={styles.secondaryButton} onClick={() => onRetryWake(pc)}>
              Retry Wake
            </button>
            <button className={styles.dangerButton} onClick={() => onCancelPrepared(pc)}>
              Cancel
            </button>
          </>
        ) : (
          <>
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
          </>
        )}
      </div>
    </article>
  );
}

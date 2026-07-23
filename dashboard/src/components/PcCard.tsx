import styles from "@/app/page.module.css";
import { formatDateTime, formatDuration } from "@/lib/time";
import { getPcActionAvailability, getPcCardTone } from "@/lib/pcStatus";
import type { PcSummary } from "@/lib/types";

interface PcCardProps {
  pc: PcSummary;
  onStart: (pc: PcSummary) => void;
  onAddTime: (pc: PcSummary) => void;
  onEnd: (pc: PcSummary) => void;
  onDetails: (pc: PcSummary) => void;
}

const toneClassName = {
  active: styles.pcCardActive,
  available: styles.pcCardAvailable,
  offline: styles.pcCardOffline,
  warning: styles.pcCardWarning
};

export function PcCard({ pc, onStart, onAddTime, onEnd, onDetails }: PcCardProps) {
  const actions = getPcActionAvailability(pc);
  const tone = getPcCardTone(pc);
  const connectionLabel = pc.connection_status === "online" ? "Online" : "Offline";
  const sessionLabel =
    pc.session_status === "active"
      ? "In Use"
      : pc.session_status === "unknown"
        ? "Needs Review"
        : "Idle";

  return (
    <article className={`${styles.pcCard} ${toneClassName[tone]}`}>
      <button
        aria-label={`View ${pc.pc_name} details`}
        onClick={() => onDetails(pc)}
        style={{ all: "unset", cursor: "pointer", display: "block", width: "100%" }}
      >
        <div className={styles.pcHeader}>
          <div className={styles.pcName}>{pc.pc_name}</div>
          <span className={styles.statusPill}>{connectionLabel}</span>
        </div>
        <dl className={styles.detailList}>
          <div className={styles.detailRow}>
            <span>Session</span>
            <strong>{sessionLabel}</strong>
          </div>
          <div className={styles.detailRow}>
            <span>Remaining</span>
            <strong>{formatDuration(pc.remaining_seconds)}</strong>
          </div>
          <div className={styles.detailRow}>
            <span>{pc.connection_status === "offline" ? "Last seen" : "IP address"}</span>
            <strong>
              {pc.connection_status === "offline" ? formatDateTime(pc.last_seen) : pc.ip_address}
            </strong>
          </div>
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
          disabled={!actions.canAddTime}
          onClick={() => onAddTime(pc)}
        >
          Add Time
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

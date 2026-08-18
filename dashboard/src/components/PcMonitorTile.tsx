import { memo } from "react";
import styles from "@/app/page.module.css";
import { getPcMonitorPresentation } from "@/lib/pcStatus";
import { formatMoney, formatNaturalDuration, formatTimer } from "@/lib/time";
import type { PcSummary } from "@/lib/types";

interface PcMonitorTileProps {
  pc: PcSummary;
  isSelected: boolean;
  onSelect: (pc: PcSummary) => void;
}

function PcMonitorTileComponent({ pc, isSelected, onSelect }: PcMonitorTileProps) {
  const displayName = pc.display_name ?? pc.pc_name;
  const presentation = getPcMonitorPresentation(pc);
  const showCompactTime =
    (pc.session_status === "active" || pc.session_status === "grace_period") &&
    pc.session_mode !== "open_time" &&
    pc.remaining_seconds !== null;
  const customerLabel = pc.member_name_snapshot ?? pc.member_code_snapshot;
  const stateLabel =
    pc.session_status === "settlement_pending" && Number.parseFloat(pc.balance_due) > 0
      ? `Settlement ${formatMoney(pc.balance_due)}`
      : pc.session_status === "active" && pc.session_mode === "open_time"
        ? formatTimer(pc.actual_elapsed_seconds)
        : showCompactTime
          ? pc.remaining_seconds !== null && pc.remaining_seconds >= 3600
            ? formatNaturalDuration(pc.remaining_seconds)
            : formatTimer(pc.remaining_seconds)
          : pc.connection_status === "offline" && pc.mac_address
            ? "Wake Ready"
            : presentation.label;
  const secondaryLabel = customerLabel ?? stateLabel;
  const tertiaryLabel = customerLabel ? stateLabel : null;

  return (
    <button
      aria-label={`Open ${displayName} details`}
      aria-pressed={isSelected}
      className={`${styles.monitorTile} ${styles[presentation.className]} ${
        isSelected ? styles.monitorTileSelected : ""
      }`}
      onClick={() => onSelect(pc)}
      type="button"
    >
      <span className={styles.monitorUnit}>
        <span className={styles.monitorScreen}>
          {pc.recovery_required ? <span className={styles.monitorWarningBadge}>Recovery</span> : null}
          <span className={styles.monitorName}>{displayName}</span>
          {secondaryLabel ? <span className={styles.monitorSubLabel}>{secondaryLabel}</span> : null}
          {tertiaryLabel ? <span className={styles.monitorTimeLabel}>{tertiaryLabel}</span> : null}
        </span>
        <span className={styles.monitorStand} aria-hidden="true" />
        <span className={styles.monitorBase} aria-hidden="true" />
      </span>
      <span className={styles.monitorStatusText}>{presentation.label}</span>
    </button>
  );
}

export const PcMonitorTile = memo(PcMonitorTileComponent);

import styles from "@/app/page.module.css";
import { getPcMonitorPresentation } from "@/lib/pcStatus";
import { formatTimer } from "@/lib/time";
import type { PcSummary } from "@/lib/types";

interface PcMonitorTileProps {
  pc: PcSummary;
  isSelected: boolean;
  onSelect: (pc: PcSummary) => void;
}

export function PcMonitorTile({ pc, isSelected, onSelect }: PcMonitorTileProps) {
  const displayName = pc.display_name ?? pc.pc_name;
  const presentation = getPcMonitorPresentation(pc);
  const showCompactTime =
    (pc.session_status === "active" || pc.session_status === "grace_period") &&
    pc.session_mode !== "open_time" &&
    pc.remaining_seconds !== null;
  const secondaryLabel =
    pc.member_name_snapshot ?? pc.member_code_snapshot ?? (showCompactTime ? formatTimer(pc.remaining_seconds) : null);

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
      <span className={styles.monitorScreen}>
        {pc.is_vip ? <span className={styles.monitorVipBadge}>VIP</span> : null}
        {pc.recovery_required ? <span className={styles.monitorWarningBadge}>Recovery</span> : null}
        <span className={styles.monitorName}>{displayName}</span>
        {secondaryLabel ? <span className={styles.monitorSubLabel}>{secondaryLabel}</span> : null}
        <span className={styles.monitorStatusLight} aria-hidden="true" />
      </span>
      <span className={styles.monitorStand} aria-hidden="true" />
      <span className={styles.monitorBase} aria-hidden="true" />
      <span className={styles.monitorStatusText}>{presentation.label}</span>
    </button>
  );
}

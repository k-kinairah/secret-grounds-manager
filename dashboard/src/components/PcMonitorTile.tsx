import { memo } from "react";
import styles from "@/app/page.module.css";
import { getMonitorTileVisualState } from "@/lib/pcMonitor";
import { getPcMonitorPresentation } from "@/lib/pcStatus";
import { formatMoney, formatNaturalDuration, formatTimer } from "@/lib/time";
import type { PcSummary, ScreenMonitorStatus } from "@/lib/types";

interface PcMonitorTileProps {
  pc: PcSummary;
  isSelected: boolean;
  onSelect: (pc: PcSummary) => void;
  monitorStatus?: ScreenMonitorStatus;
  frameUrl?: string;
  frameHasError?: boolean;
}

const MONITOR_BADGE_LABEL: Record<string, string> = {
  live: "LIVE",
  stale: "STALE",
  error: "!"
};

function PcMonitorTileComponent({
  pc,
  isSelected,
  onSelect,
  monitorStatus,
  frameUrl,
  frameHasError = false
}: PcMonitorTileProps) {
  const displayName = pc.display_name ?? pc.pc_name;
  const presentation = getPcMonitorPresentation(pc);
  const monitorVisualState = getMonitorTileVisualState({ status: monitorStatus, hasFetchError: frameHasError });
  const showThumbnail = (monitorVisualState === "live" || monitorVisualState === "stale") && Boolean(frameUrl);
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
      <span className={styles.monitorScreen} data-monitor-state={monitorVisualState}>
        {showThumbnail ? (
          // eslint-disable-next-line @next/next/no-img-element -- an authenticated Blob URL, not a static asset next/image can optimize.
          <img
            alt=""
            aria-hidden="true"
            className={`${styles.monitorThumbnail} ${
              monitorVisualState === "stale" ? styles.monitorThumbnailStale : ""
            }`}
            src={frameUrl}
          />
        ) : null}
        {monitorVisualState === "stale" ? <span className={styles.monitorStaleOverlay}>STALE</span> : null}
        {monitorVisualState === "no_image" ? (
          <span className={styles.monitorNoImageHint}>Waiting for screen&hellip;</span>
        ) : null}
        {pc.is_vip ? (
          <span className={styles.monitorVipBadge}>VIP</span>
        ) : (
          <span className={styles.monitorStandardBadge}>Standard</span>
        )}
        {pc.recovery_required ? <span className={styles.monitorWarningBadge}>Recovery</span> : null}
        {monitorVisualState === "live" || monitorVisualState === "error" ? (
          <span
            className={`${styles.monitorFrameBadge} ${
              monitorVisualState === "live" ? styles.monitorFrameBadgeLive : styles.monitorFrameBadgeError
            }`}
          >
            {MONITOR_BADGE_LABEL[monitorVisualState]}
          </span>
        ) : null}
        <span className={styles.monitorName}>{displayName}</span>
        {secondaryLabel ? <span className={styles.monitorSubLabel}>{secondaryLabel}</span> : null}
        {tertiaryLabel ? <span className={styles.monitorTimeLabel}>{tertiaryLabel}</span> : null}
        <span className={styles.monitorStatusLight} aria-hidden="true" />
      </span>
      <span className={styles.monitorStand} aria-hidden="true" />
      <span className={styles.monitorBase} aria-hidden="true" />
      <span className={styles.monitorStatusText}>{presentation.label}</span>
    </button>
  );
}

export const PcMonitorTile = memo(PcMonitorTileComponent);

import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import { createPortal } from "react-dom";
import styles from "@/app/page.module.css";
import {
  clearCustomerLock,
  fetchSessionBalanceSource,
  getPollingIntervalMs,
  sendPcMessage,
  sendPowerCommand,
  updatePcMacAddress,
  wakePc
} from "@/lib/api";
import { getMonitorTileVisualState, type MonitorTileVisualState } from "@/lib/pcMonitor";
import { getPcActionAvailability } from "@/lib/pcStatus";
import { formatDateTime, formatMoney, formatNaturalDuration, formatTimer } from "@/lib/time";
import type { MessageSeverity, PcSummary, ScreenMonitorStatus, SessionBalanceSourceProjection, UserRole } from "@/lib/types";
import { CloseButton } from "./CloseButton";
import { ConfirmActionDialog } from "./ConfirmActionDialog";

const MONITOR_STATE_LABEL: Record<MonitorTileVisualState, string> = {
  live: "Live",
  stale: "Stale",
  offline: "Offline",
  no_image: "No screen available",
  error: "Preview unavailable"
};

type IconName =
  | "card"
  | "clockPlus"
  | "message"
  | "moon"
  | "play"
  | "power"
  | "refresh"
  | "rotate"
  | "save"
  | "stop"
  | "unlock"
  | "warning"
  | "x";

interface PcDetailsPanelProps {
  pc: PcSummary;
  currentUserRole: UserRole | null;
  /** Power Controls (Wake / Sleep / Restart / Shutdown) are reserved for
      the protected Admin account only. */
  currentUserIsProtected?: boolean;
  variant?: "modal" | "dock";
  onClose: () => void;
  onActionComplete: () => void;
  onStart: (pc: PcSummary) => void;
  onAddTime: (pc: PcSummary) => void;
  onEnd: (pc: PcSummary) => void;
  onResolve: (pc: PcSummary) => void;
  onPayment: (pc: PcSummary) => void;
  onRetryWake: (pc: PcSummary) => void;
  onCancelPrepared: (pc: PcSummary) => void;
  monitorStatus?: ScreenMonitorStatus;
  frameUrl?: string;
  frameHasError?: boolean;
}

function Icon({ name }: { name: IconName }) {
  const common = {
    fill: "none",
    stroke: "currentColor",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    strokeWidth: 2.15
  };
  const paths: Record<IconName, ReactNode> = {
    card: (
      <>
        <rect {...common} height="14" rx="2" width="18" x="3" y="5" />
        <path {...common} d="M3 10h18" />
      </>
    ),
    clockPlus: (
      <>
        <circle {...common} cx="9.5" cy="9.5" r="6.25" />
        <path {...common} d="M9.5 6v3.5l2.5 1.5" />
        <path {...common} d="M18 14.75v6.5" />
        <path {...common} d="M14.75 18h6.5" />
      </>
    ),
    message: (
      <>
        <path {...common} d="M4 5h16v11H8l-4 4V5z" />
        <path {...common} d="M8 9h8" />
        <path {...common} d="M8 13h5" />
      </>
    ),
    moon: <path {...common} d="M21 12.8A8.5 8.5 0 1 1 11.2 3 6.6 6.6 0 0 0 21 12.8z" />,
    play: <path {...common} d="M8 5v14l11-7-11-7z" />,
    power: (
      <>
        <path {...common} d="M12 2.8v9" />
        <path {...common} d="M18.4 6.6a9 9 0 1 1-12.8 0" />
      </>
    ),
    refresh: (
      <>
        <path {...common} d="M20 6v5h-5" />
        <path {...common} d="M4 18v-5h5" />
        <path {...common} d="M18 9a6.5 6.5 0 0 0-10.8-2.4L4 10" />
        <path {...common} d="M6 15a6.5 6.5 0 0 0 10.8 2.4L20 14" />
      </>
    ),
    rotate: (
      <>
        <path {...common} d="M21 3v6h-6" />
        <path {...common} d="M20 12a8 8 0 1 1-2.35-5.65L21 9" />
      </>
    ),
    save: (
      <>
        <path {...common} d="M5 3h12l2 2v16H5z" />
        <path {...common} d="M8 3v6h8V3" />
        <path {...common} d="M8 21v-7h8v7" />
      </>
    ),
    stop: <rect {...common} height="12" rx="2" width="12" x="6" y="6" />,
    unlock: (
      <>
        <rect {...common} height="10" rx="2" width="14" x="5" y="11" />
        <path {...common} d="M8 11V7a4 4 0 0 1 7.7-1.6" />
        <path {...common} d="M12 15.5v2.5" />
      </>
    ),
    warning: (
      <>
        <path {...common} d="M12 3 22 20H2L12 3z" />
        <path {...common} d="M12 9v4" />
        <path {...common} d="M12 17h.01" />
      </>
    ),
    x: (
      <>
        <path {...common} d="M18 6 6 18" />
        <path {...common} d="m6 6 12 12" />
      </>
    )
  };

  return (
    <svg aria-hidden="true" className={styles.buttonIcon} viewBox="0 0 24 24">
      {paths[name]}
    </svg>
  );
}

const SEVERITY_OPTIONS: { value: MessageSeverity; label: string; hint: string }[] = [
  { value: "info", label: "Info", hint: "General notice" },
  { value: "warning", label: "Warning", hint: "Needs attention" },
  { value: "urgent", label: "Urgent", hint: "Act now" }
];

function SeveritySelect({
  value,
  onChange,
  disabled = false
}: {
  value: MessageSeverity;
  onChange: (next: MessageSeverity) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(() =>
    Math.max(0, SEVERITY_OPTIONS.findIndex((option) => option.value === value))
  );
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const selected = SEVERITY_OPTIONS.find((option) => option.value === value) ?? SEVERITY_OPTIONS[0];

  useEffect(() => {
    if (!open) {
      return;
    }
    setActiveIndex(Math.max(0, SEVERITY_OPTIONS.findIndex((option) => option.value === value)));

    function onPointerDown(event: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open, value]);

  function commit(next: MessageSeverity) {
    onChange(next);
    setOpen(false);
    triggerRef.current?.focus();
  }

  function onTriggerKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setOpen(true);
    }
  }

  function onMenuKeyDown(event: ReactKeyboardEvent<HTMLUListElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => (index + 1) % SEVERITY_OPTIONS.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => (index - 1 + SEVERITY_OPTIONS.length) % SEVERITY_OPTIONS.length);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      commit(SEVERITY_OPTIONS[activeIndex].value);
    } else if (event.key === "Home") {
      event.preventDefault();
      setActiveIndex(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setActiveIndex(SEVERITY_OPTIONS.length - 1);
    }
  }

  return (
    <div className={styles.severitySelect} ref={rootRef}>
      <button
        aria-expanded={open}
        aria-haspopup="listbox"
        className={styles.severityTrigger}
        data-sev={value}
        disabled={disabled}
        onClick={() => setOpen((prev) => !prev)}
        onKeyDown={onTriggerKeyDown}
        ref={triggerRef}
        type="button"
      >
        <span className={styles.severityDot} aria-hidden="true" />
        <span className={styles.severityValue}>{selected.label}</span>
        <svg
          aria-hidden="true"
          className={styles.severityCaret}
          data-open={open}
          viewBox="0 0 12 8"
        >
          <path d="M1 1l5 5 5-5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
        </svg>
      </button>
      {open ? (
        // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- listbox key handling per WAI-ARIA pattern
        <ul
          className={styles.severityMenu}
          onKeyDown={onMenuKeyDown}
          ref={(node) => node?.focus()}
          role="listbox"
          tabIndex={-1}
        >
          {SEVERITY_OPTIONS.map((option, index) => (
            <li
              aria-selected={option.value === value}
              className={styles.severityOption}
              data-active={index === activeIndex}
              data-sev={option.value}
              key={option.value}
              onClick={() => commit(option.value)}
              onMouseEnter={() => setActiveIndex(index)}
              role="option"
            >
              <span className={styles.severityDot} aria-hidden="true" />
              <span className={styles.severityOptionLabel}>{option.label}</span>
              <span className={styles.severityOptionHint}>{option.hint}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function panelTone(pc: PcSummary): string {
  if (pc.recovery_required || pc.power_state === "shutdown_failed") {
    return "danger";
  }
  if (pc.session_status === "settlement_pending") {
    return "settlement";
  }
  if (pc.session_status === "grace_period") {
    return "grace";
  }
  if (pc.customer_locked) {
    return "locked";
  }
  if (pc.session_status === "active") {
    return pc.remaining_seconds !== null && pc.remaining_seconds <= 300 ? "lowtime" : "inuse";
  }
  if (pc.connection_status === "offline") {
    return "offline";
  }
  return "available";
}

function statusLabel(pc: PcSummary): string {
  if (pc.power_state === "shutdown_failed") {
    return "Shutdown Failed";
  }
  if (pc.power_state === "shutting_down") {
    return "Shutting Down";
  }
  if (pc.recovery_required) {
    return "Recovery Required";
  }
  if (pc.session_status === "active") {
    return pc.customer_locked ? "Active · Customer Locked" : "In Use";
  }
  if (pc.session_status === "grace_period") {
    return "Time Ended";
  }
  if (pc.session_status === "shutting_down") {
    return "Shutting Down";
  }
  if (pc.session_status === "settlement_pending") {
    return "Settlement Pending";
  }
  if (pc.session_status === "ending") {
    return "Ending";
  }
  if (pc.connection_status === "offline") {
    return "Powered Off";
  }
  return "Available";
}

export function PcDetailsPanel({
  pc,
  currentUserRole,
  currentUserIsProtected = false,
  variant = "modal",
  onClose,
  onActionComplete,
  onStart,
  onAddTime,
  onEnd,
  onResolve,
  onPayment,
  onRetryWake,
  onCancelPrepared,
  monitorStatus,
  frameUrl,
  frameHasError = false
}: PcDetailsPanelProps) {
  const modalRef = useRef<HTMLElement | null>(null);
  const isSettlementPending = pc.session_status === "settlement_pending";
  const actions = getPcActionAvailability(pc);
  const monitorVisualState = getMonitorTileVisualState({ status: monitorStatus, hasFetchError: frameHasError });
  const showMonitorThumbnail = (monitorVisualState === "live" || monitorVisualState === "stale") && Boolean(frameUrl);
  // Advanced / Technical Details (diagnostics + MAC address editor) is
  // locked to the protected Admin account, same as Power Controls.
  const canSeeTechnical = currentUserIsProtected === true;
  const customerLabel =
    pc.member_name_snapshot ?? pc.member_code_snapshot ?? (pc.active_session_id ? "Walk-in" : "No active customer");
  const [messageText, setMessageText] = useState("");
  const [messageSeverity, setMessageSeverity] = useState<MessageSeverity>("info");
  const [macAddress, setMacAddress] = useState(pc.mac_address ?? "");
  const [forceReason, setForceReason] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [isWorking, setIsWorking] = useState(false);
  // A successful send flashes the button label itself ("Message Sent")
  // instead of the shared full-row notice banner -- confirmed on the real
  // dashboard, that banner reads as a stray floating bar for something as
  // routine as a message send.
  const [messageJustSent, setMessageJustSent] = useState(false);
  const messageSentTimeoutRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (messageSentTimeoutRef.current !== null) {
        window.clearTimeout(messageSentTimeoutRef.current);
      }
    };
  }, []);
  const [confirmClearLock, setConfirmClearLock] = useState(false);
  // Power Controls are locked to the protected Admin account -- not a
  // role check. Regular operators (including owners) never see this section.
  const canPowerControl = currentUserIsProtected === true;
  const isActiveSession =
    pc.session_status === "active" || pc.session_status === "grace_period";
  const isOpenTime = pc.session_mode === "open_time";
  const canResolve =
    pc.recovery_required && (currentUserRole === "owner" || currentUserRole === "admin");
  const isVipMemberSession =
    pc.is_vip && pc.session_mode === "member_balance" && isActiveSession && pc.active_session_id !== null;
  const [balanceSource, setBalanceSource] = useState<SessionBalanceSourceProjection | null>(null);

  useEffect(() => {
    if (!isVipMemberSession || !pc.active_session_id) {
      // No setState here: balanceSource is only ever rendered behind
      // `isVipMemberSession ? ... : null` below, so a stale value from a
      // prior VIP session can never actually be shown once this becomes
      // false -- nothing to clear.
      return;
    }
    let isMounted = true;
    function refreshBalanceSource() {
      fetchSessionBalanceSource(pc.active_session_id as string)
        .then((projection) => {
          if (isMounted) {
            setBalanceSource(projection);
          }
        })
        .catch(() => {
          if (isMounted) {
            setBalanceSource(null);
          }
        });
    }
    refreshBalanceSource();
    const intervalId = window.setInterval(refreshBalanceSource, getPollingIntervalMs());
    return () => {
      isMounted = false;
      window.clearInterval(intervalId);
    };
  }, [isVipMemberSession, pc.active_session_id]);

  useEffect(() => {
    if (variant === "dock") {
      return;
    }

    const modal = modalRef.current;
    const previousActiveElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    modal?.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        if (!isWorking) {
          onClose();
        }
        return;
      }

      if (event.key !== "Tab" || modal === null) {
        return;
      }

      const focusable = Array.from(
        modal.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), details summary, [href], [tabindex]:not([tabindex="-1"])'
        )
      ).filter((element) => element.offsetParent !== null || element === document.activeElement);

      if (focusable.length === 0) {
        event.preventDefault();
        modal.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previousActiveElement?.focus();
    };
  }, [isWorking, onClose, variant]);

  async function runPanelAction(action: () => Promise<unknown>, success: string) {
    if (isWorking) {
      return;
    }
    setIsWorking(true);
    setNotice(null);
    try {
      await action();
      setNotice(success);
      onActionComplete();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Remote action failed.");
    } finally {
      setIsWorking(false);
    }
  }

  function sendMessage() {
    if (!messageText.trim()) {
      setNotice("Enter a message for the customer.");
      return;
    }
    if (isWorking) {
      return;
    }
    setIsWorking(true);
    setNotice(null);
    sendPcMessage(pc.pc_name, {
      message: messageText,
      severity: messageSeverity,
      require_acknowledgement: messageSeverity === "urgent"
    })
      .then(() => {
        onActionComplete();
        setMessageJustSent(true);
        if (messageSentTimeoutRef.current !== null) {
          window.clearTimeout(messageSentTimeoutRef.current);
        }
        messageSentTimeoutRef.current = window.setTimeout(() => {
          setMessageJustSent(false);
        }, 4000);
      })
      .catch((error) => {
        setNotice(error instanceof Error ? error.message : "Remote action failed.");
      })
      .finally(() => {
        setIsWorking(false);
      });
  }

  function power(command: "shutdown" | "restart" | "sleep") {
    const forced = isActiveSession;
    if (forced && !forceReason.trim()) {
      setNotice("Active sessions require a force reason.");
      return;
    }
    void runPanelAction(
      () =>
        sendPowerCommand(pc.pc_name, command, {
          force: forced,
          confirm: forced,
          reason: forced ? forceReason : undefined
        }),
      `${command} command sent.`
    );
  }

  const panelContent = (
    <section
      aria-label={`${pc.pc_name} controls`}
      aria-modal={variant === "modal" ? "true" : undefined}
      className={variant === "dock" ? styles.pcControlDock : styles.pcControlModal}
      data-tone={panelTone(pc)}
      ref={modalRef}
      role={variant === "modal" ? "dialog" : "region"}
      tabIndex={variant === "modal" ? -1 : undefined}
    >
      {PcControlPanelContent()}
    </section>
  );

  const clearLockDialog = confirmClearLock && typeof document !== "undefined" ? createPortal(
    <ConfirmActionDialog
      confirmLabel={isWorking ? "Clearing..." : "Clear Lock"}
      isLoading={isWorking}
      lede={
        <>
          Release the customer hold on <strong>{pc.display_name ?? pc.pc_name}</strong> and
          unlock the screen?
        </>
      }
      note="Use this only when the member has asked for help or left the station. Their session and timer keep running — you're just removing the lock they set."
      onCancel={() => setConfirmClearLock(false)}
      onConfirm={() => {
        if (!pc.active_session_id) {
          setConfirmClearLock(false);
          return;
        }
        void runPanelAction(
          () => clearCustomerLock(pc.active_session_id as string),
          "Customer lock cleared."
        ).finally(() => setConfirmClearLock(false));
      }}
      facts={
        pc.member_name_snapshot || pc.member_code_snapshot
          ? [{ label: "Member", value: pc.member_name_snapshot ?? pc.member_code_snapshot }]
          : undefined
      }
      title="Clear Customer Lock"
      tone="locked"
    />,
    document.body
  ) : null;

  if (variant === "dock") {
    return (
      <>
        {panelContent}
        {clearLockDialog}
      </>
    );
  }

  return (
    <div
      className={styles.overlay}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !isWorking) {
          onClose();
        }
      }}
      role="presentation"
    >
      {panelContent}
      {clearLockDialog}
    </div>
  );

  function PcControlPanelContent() {
    return (
      <>
        <header className={styles.pcControlHeader}>
          <div>
            <div className={styles.pcControlTitleRow}>
              <h2>{pc.display_name ?? pc.pc_name}</h2>
              <span className={styles.pcControlStatePill}>
                <span className={styles.pcControlStateDot} aria-hidden="true" />
                {statusLabel(pc)}
              </span>
              {pc.connection_status !== "online" ? (
                <span className={styles.pcControlOffline} title="This station is not reachable — it is powered off or has lost its connection.">
                  Offline
                </span>
              ) : null}
            </div>
            <p>{customerLabel}</p>
          </div>
          <CloseButton
            disabled={isWorking}
            label={`Close ${pc.display_name ?? pc.pc_name} control window`}
            onClick={onClose}
          />
        </header>

        {notice ? <div className={styles.statusMessage}>{notice}</div> : null}

        <div className={styles.pcControlGrid}>
          <div className={styles.pcControlColumn}>
            <section className={styles.controlSection} aria-label="Session summary">
              <h3>Session</h3>
              <dl className={styles.compactDetailList}>
                <div className={styles.detailRow}>
                  <span>Status</span>
                  <strong>{statusLabel(pc)}</strong>
                </div>
                <div className={styles.detailRow}>
                  <span>Customer</span>
                  <strong>{customerLabel}</strong>
                </div>
                <div className={styles.detailRow}>
                  <span>Started</span>
                  <strong>{pc.started_at ? formatDateTime(pc.started_at) : "Not started"}</strong>
                </div>
                <div className={styles.detailRow}>
                  <span>
                    {pc.session_mode === "open_time"
                      ? "Elapsed"
                      : pc.session_status === "grace_period"
                        ? "Shutdown In"
                        : "Remaining"}
                  </span>
                  <strong>
                    {formatTimer(
                      pc.session_mode === "open_time"
                        ? pc.actual_elapsed_seconds
                        : pc.remaining_seconds
                    )}
                  </strong>
                </div>
              </dl>
            </section>

            <section className={styles.controlSection} aria-label="Session actions">
              <h3>Session Actions</h3>
              {pc.customer_locked ? (
                <p className={styles.helperWarning}>
                  The member has locked this station; their session and timer continue
                  running underneath.
                </p>
              ) : null}
              <div className={styles.sessionActionStack}>
                {pc.customer_locked ? (
                  <button
                    className={styles.primaryButton}
                    disabled={!actions.canClearCustomerLock || isWorking}
                    onClick={() => setConfirmClearLock(true)}
                    type="button"
                  >
                    <Icon name="unlock" />
                    Clear Customer Lock
                  </button>
                ) : null}

                {actions.canStart ? (
                  <button
                    className={styles.primaryButton}
                    onClick={() => onStart(pc)}
                    type="button"
                  >
                    <Icon name={pc.connection_status === "offline" ? "power" : "play"} />
                    {pc.connection_status === "offline" ? "Start & Wake" : "Start Session"}
                  </button>
                ) : null}

                {pc.startup_status !== null ? (
                  <div className={styles.modalActionGrid}>
                    <button
                      className={styles.secondaryButton}
                      onClick={() => onRetryWake(pc)}
                      type="button"
                    >
                      <Icon name="refresh" />
                      Retry Wake
                    </button>
                    <button
                      className={styles.dangerButton}
                      onClick={() => onCancelPrepared(pc)}
                      type="button"
                    >
                      <Icon name="stop" />
                      Cancel Startup
                    </button>
                  </div>
                ) : actions.canAddTime || actions.canEnd || isSettlementPending || isOpenTime || pc.recovery_required ? (
                  <div className={styles.modalActionGrid}>
                    {actions.canAddTime && !isOpenTime ? (
                      <button
                        className={styles.secondaryButton}
                        disabled={isWorking}
                        onClick={() => onAddTime(pc)}
                        type="button"
                      >
                        <Icon name="clockPlus" />
                        Add Time
                      </button>
                    ) : null}
                    {/* Prepaid/member sessions are paid up front and topped up via Add Time,
                        so no mid-session Payment button. This only surfaces the legacy
                        settlement path for any lingering Open Time session. */}
                    {isSettlementPending || isOpenTime ? (
                      <button
                        className={styles.secondaryButton}
                        disabled={pc.recovery_required || (!actions.canAddTime && !actions.canCollectPayment)}
                        onClick={() => (isOpenTime && !isSettlementPending ? onEnd(pc) : onPayment(pc))}
                        type="button"
                      >
                        <Icon name="card" />
                        {isSettlementPending ? "Collect Payment" : "End & Settle"}
                      </button>
                    ) : null}
                    {actions.canEnd || pc.recovery_required ? (
                      <button
                        className={styles.dangerButton}
                        disabled={!actions.canEnd || (pc.recovery_required && !canResolve)}
                        onClick={() => (pc.recovery_required ? onResolve(pc) : onEnd(pc))}
                        title={pc.recovery_required && !canResolve ? "Owner/admin permission is required." : undefined}
                        type="button"
                      >
                        <Icon name={pc.recovery_required ? "warning" : "stop"} />
                        {pc.recovery_required
                          ? canResolve
                            ? "Resolve Session"
                            : "Needs Admin"
                          : "End Session"}
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </section>

            {isVipMemberSession ? (
              <section className={styles.controlSection} aria-label="VIP balance status">
                <h3>VIP Balance Status</h3>
                {balanceSource ? (
                  balanceSource.is_vip_station ? (
                    <>
                      <p
                        className={
                          balanceSource.balance_source === "standard"
                            ? styles.vipSourceStatusStandard
                            : styles.vipSourceStatusVip
                        }
                      >
                        {balanceSource.balance_source === "standard" ? "Using Standard Time" : "Using VIP Time"}
                      </p>
                      {balanceSource.balance_source === "standard" ? (
                        <p className={styles.panelText}>
                          Standard time is being used at the VIP conversion rate.
                        </p>
                      ) : null}
                      <dl className={styles.compactDetailList}>
                        <div className={styles.detailRow}>
                          <span>Reserved for this session · VIP</span>
                          <strong>{formatNaturalDuration(balanceSource.vip_reserved_seconds)}</strong>
                        </div>
                        {balanceSource.standard_reserved_seconds > 0 ? (
                          <div className={styles.detailRow}>
                            <span>Reserved for this session · Standard</span>
                            <strong>
                              {formatNaturalDuration(balanceSource.standard_reserved_seconds)} (
                              {formatNaturalDuration(balanceSource.standard_reserved_vip_equivalent_seconds)} VIP play)
                            </strong>
                          </div>
                        ) : null}
                      </dl>
                    </>
                  ) : (
                    // The PC is VIP now, but this session started while it was
                    // Standard -- its rate is locked to Standard 1:1 for its
                    // whole life. Show that plainly, with no VIP-rate framing
                    // and no "VIP play" equivalent (neither applies here).
                    <>
                      <p className={styles.vipSourceStatusStandard}>Running at Standard rate</p>
                      <p className={styles.panelText}>
                        This session started before this PC became VIP, so it keeps the Standard
                        1:1 rate. VIP pricing applies to the next session started here.
                      </p>
                      {balanceSource.standard_reserved_seconds > 0 ? (
                        <dl className={styles.compactDetailList}>
                          <div className={styles.detailRow}>
                            <span>Reserved for this session · Standard</span>
                            <strong>
                              {formatNaturalDuration(balanceSource.standard_reserved_seconds)}
                            </strong>
                          </div>
                        </dl>
                      ) : null}
                    </>
                  )
                ) : (
                  <p className={styles.panelText}>Loading balance status...</p>
                )}
              </section>
            ) : null}

            <section className={styles.controlSection} aria-label="Billing summary">
              <h3>Billing</h3>
              <dl className={styles.compactDetailList}>
                <div className={styles.detailRow}>
                  <span>
                    {pc.session_mode === "open_time" && pc.session_status === "active"
                      ? "Estimated total"
                      : isSettlementPending
                        ? "Final amount"
                        : "Amount due"}
                  </span>
                  <strong>
                    {formatMoney(
                      pc.session_mode === "open_time" && pc.session_status === "active"
                        ? pc.estimated_total_amount
                        : pc.balance_due !== "0.00"
                          ? pc.balance_due
                          : pc.total_amount
                    )}
                  </strong>
                </div>
                <div className={styles.detailRow}>
                  <span>Payment status</span>
                  <strong>{pc.payment_status}</strong>
                </div>
                <div className={styles.detailRow}>
                  <span>Paid</span>
                  <strong>{formatMoney(pc.total_paid)}</strong>
                </div>
              </dl>
            </section>
          </div>

          <div className={styles.pcControlColumn}>
            <section className={styles.controlSection} aria-label="Messaging controls">
              <h3>Messaging</h3>
              <p className={styles.panelText}>Sent to the customer as SECRET GROUNDS.</p>
              <label className={styles.field}>
                Message to customer
                <textarea
                  maxLength={500}
                  onChange={(event) => setMessageText(event.target.value)}
                  placeholder="Type a message for this customer…"
                  value={messageText}
                />
              </label>
              <div className={styles.field}>
                <span>Severity</span>
                <SeveritySelect
                  disabled={isWorking}
                  onChange={setMessageSeverity}
                  value={messageSeverity}
                />
              </div>
              <button
                className={styles.primaryButton}
                disabled={isWorking || pc.connection_status === "offline"}
                onClick={sendMessage}
                type="button"
              >
                <Icon name="message" />
                {messageJustSent ? "Message Sent" : "Send Message"}
              </button>
            </section>

            <section className={styles.controlSection} aria-label="Live screen">
              <h3>Live Screen</h3>
              <div
                className={styles.monitorPreviewFrame}
                data-monitor-state={monitorVisualState}
              >
                {showMonitorThumbnail ? (
                  // eslint-disable-next-line @next/next/no-img-element -- an authenticated Blob URL, not a static asset next/image can optimize.
                  <img
                    alt={`${pc.display_name ?? pc.pc_name} live screen preview`}
                    className={`${styles.monitorPreviewImage} ${
                      monitorVisualState === "stale" ? styles.monitorThumbnailStale : ""
                    }`}
                    src={frameUrl}
                  />
                ) : (
                  <div className={styles.monitorPreviewPlaceholder}>
                    {monitorVisualState === "offline" ? "Station offline" : MONITOR_STATE_LABEL[monitorVisualState]}
                  </div>
                )}
              </div>
              <dl className={styles.compactDetailList}>
                <div className={styles.detailRow}>
                  <span>Monitor status</span>
                  <strong>{MONITOR_STATE_LABEL[monitorVisualState]}</strong>
                </div>
                <div className={styles.detailRow}>
                  <span>Connection</span>
                  <strong>{pc.connection_status.toUpperCase()}</strong>
                </div>
                <div className={styles.detailRow}>
                  <span>Last frame received</span>
                  <strong>
                    {monitorStatus?.frame_received_at
                      ? `${formatNaturalDuration(Math.round(monitorStatus.frame_age_seconds ?? 0))} ago`
                      : "Never"}
                  </strong>
                </div>
              </dl>
            </section>

            {canPowerControl ? (
              <section className={styles.controlSection} aria-label="Power controls">
                <h3>Power Controls</h3>
                {!pc.mac_address ? (
                  <p className={styles.helperWarning}>
                    Wake-on-LAN is unavailable until a MAC address is saved.
                  </p>
                ) : null}
                {isActiveSession ? (
                  <label className={styles.field}>
                    Force reason for active session power control
                    <input
                      onChange={(event) => setForceReason(event.target.value)}
                      placeholder="Required before power control during an active session"
                      value={forceReason}
                    />
                  </label>
                ) : null}
                <div className={styles.modalActionGrid}>
                  <button
                    className={styles.secondaryButton}
                    disabled={isWorking || !pc.mac_address}
                    onClick={() =>
                      void runPanelAction(
                        () => wakePc(pc.pc_name),
                        "Wake signal sent. Waiting for PC to come online."
                      )
                    }
                    title={!pc.mac_address ? "Save a MAC address in Advanced details first." : undefined}
                    type="button"
                  >
                    <Icon name="power" />
                    Wake PC
                  </button>
                  <button
                    className={styles.secondaryButton}
                    disabled={isWorking || pc.connection_status === "offline"}
                    onClick={() => power("sleep")}
                    type="button"
                  >
                    <Icon name="moon" />
                    Sleep
                  </button>
                  <button
                    className={styles.secondaryButton}
                    disabled={isWorking || pc.connection_status === "offline"}
                    onClick={() => power("restart")}
                    type="button"
                  >
                    <Icon name="rotate" />
                    Restart
                  </button>
                  <button
                    className={styles.dangerButton}
                    disabled={isWorking || pc.connection_status === "offline"}
                    onClick={() => power("shutdown")}
                    type="button"
                  >
                    <Icon name="power" />
                    Shutdown
                  </button>
                </div>
              </section>
            ) : null}
          </div>
        </div>

        {canSeeTechnical ? (
          <details className={`${styles.controlSection} ${styles.advancedDetails}`}>
            <summary>Advanced / Technical Details</summary>
            <div className={styles.advancedGrid}>
              <dl className={styles.compactDetailList}>
                <div className={styles.detailRow}>
                  <span>IP address</span>
                  <strong>{pc.ip_address || "Unavailable"}</strong>
                </div>
                <div className={styles.detailRow}>
                  <span>Command connection</span>
                  <strong>{pc.has_command_connection ? "Connected" : "Not connected"}</strong>
                </div>
                <div className={styles.detailRow}>
                  <span>Last heartbeat</span>
                  <strong>{formatDateTime(pc.last_seen)}</strong>
                </div>
                <div className={styles.detailRow}>
                  <span>Session ID</span>
                  <strong>{pc.active_session_id ?? "None"}</strong>
                </div>
                <div className={styles.detailRow}>
                  <span>Client version</span>
                  <strong>Unavailable</strong>
                </div>
                <div className={styles.detailRow}>
                  <span>Recovery state</span>
                  <strong>{pc.recovery_reason ?? "None"}</strong>
                </div>
              </dl>
              <div>
                <label className={styles.field}>
                  MAC address
                  <input
                    onChange={(event) => setMacAddress(event.target.value)}
                    placeholder="AA:BB:CC:DD:EE:FF"
                    value={macAddress}
                  />
                </label>
                <button
                  className={styles.secondaryButton}
                  disabled={isWorking}
                  onClick={() =>
                    void runPanelAction(
                      () => updatePcMacAddress(pc.pc_name, macAddress.trim() || null),
                      "MAC address saved."
                    )
                  }
                  type="button"
                >
                  <Icon name="save" />
                  Save MAC
                </button>
              </div>
            </div>
          </details>
        ) : null}
      </>
    );
  }
}

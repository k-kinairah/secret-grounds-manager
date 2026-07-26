import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import styles from "@/app/page.module.css";
import { sendPcMessage, sendPowerCommand, updatePcMacAddress, wakePc } from "@/lib/api";
import { getPcActionAvailability } from "@/lib/pcStatus";
import { formatDateTime, formatMoney, formatTimer } from "@/lib/time";
import type { MessageSeverity, PcSummary, UserRole } from "@/lib/types";

type IconName =
  | "card"
  | "clockPlus"
  | "message"
  | "moon"
  | "play"
  | "power"
  | "powerOff"
  | "refresh"
  | "rotate"
  | "save"
  | "stop"
  | "warning"
  | "x";

interface PcDetailsPanelProps {
  pc: PcSummary;
  currentUserRole: UserRole | null;
  onClose: () => void;
  onActionComplete: () => void;
  onStart: (pc: PcSummary) => void;
  onAddTime: (pc: PcSummary) => void;
  onEnd: (pc: PcSummary) => void;
  onResolve: (pc: PcSummary) => void;
  onPayment: (pc: PcSummary) => void;
  onRetryWake: (pc: PcSummary) => void;
  onCancelPrepared: (pc: PcSummary) => void;
}

function Icon({ name }: { name: IconName }) {
  const common = {
    fill: "none",
    stroke: "currentColor",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    strokeWidth: 2
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
        <circle {...common} cx="12" cy="12" r="8" />
        <path {...common} d="M12 8v4l3 2" />
        <path {...common} d="M19 19l2 2" />
        <path {...common} d="M21 17v4" />
      </>
    ),
    message: (
      <>
        <path {...common} d="M4 5h16v11H8l-4 4V5z" />
        <path {...common} d="M8 9h8" />
        <path {...common} d="M8 13h5" />
      </>
    ),
    moon: <path {...common} d="M20 15.5A8 8 0 0 1 8.5 4 7 7 0 1 0 20 15.5z" />,
    play: <path {...common} d="M8 5v14l11-7-11-7z" />,
    power: (
      <>
        <path {...common} d="M12 3v8" />
        <path {...common} d="M17.5 6.5A8 8 0 1 1 6.5 6.5" />
      </>
    ),
    powerOff: (
      <>
        <path {...common} d="M12 3v5" />
        <path {...common} d="M18.4 6.6A8 8 0 0 1 7.2 18" />
        <path {...common} d="M4 4l16 16" />
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
        <path {...common} d="M20 6v5h-5" />
        <path {...common} d="M18 9a7 7 0 1 0 1 6" />
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

function statusLabel(pc: PcSummary): string {
  if (pc.recovery_required) {
    return "Recovery Required";
  }
  if (pc.session_status === "active") {
    return "In Use";
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
  onClose,
  onActionComplete,
  onStart,
  onAddTime,
  onEnd,
  onResolve,
  onPayment,
  onRetryWake,
  onCancelPrepared
}: PcDetailsPanelProps) {
  const modalRef = useRef<HTMLElement | null>(null);
  const isSettlementPending = pc.session_status === "settlement_pending";
  const actions = getPcActionAvailability(pc);
  const canSeeTechnical = currentUserRole === "owner" || currentUserRole === "admin";
  const planLabel =
    pc.session_mode === "member_balance"
      ? "Member Time"
      : pc.session_mode === "open_time"
        ? "Open Time"
        : pc.rate_name_snapshot ?? "Package";
  const customerLabel =
    pc.member_name_snapshot ?? pc.member_code_snapshot ?? (pc.active_session_id ? "Walk-in" : "No active customer");
  const [messageTitle, setMessageTitle] = useState("Secret Grounds");
  const [messageText, setMessageText] = useState("");
  const [messageSeverity, setMessageSeverity] = useState<MessageSeverity>("info");
  const [macAddress, setMacAddress] = useState(pc.mac_address ?? "");
  const [forceReason, setForceReason] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [isWorking, setIsWorking] = useState(false);
  const canPowerControl = currentUserRole === "owner" || currentUserRole === "admin";
  const isActiveSession = pc.session_status === "active";
  const isOpenTime = pc.session_mode === "open_time";
  const canResolve =
    pc.recovery_required && (currentUserRole === "owner" || currentUserRole === "admin");

  useEffect(() => {
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
  }, [isWorking, onClose]);

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
    void runPanelAction(
      () =>
        sendPcMessage(pc.pc_name, {
          title: messageTitle,
          message: messageText,
          severity: messageSeverity,
          require_acknowledgement: messageSeverity === "urgent"
        }),
      "Message sent."
    );
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
      <section
        aria-label={`${pc.pc_name} control window`}
        aria-modal="true"
        className={styles.pcControlModal}
        ref={modalRef}
        role="dialog"
        tabIndex={-1}
      >
        <header className={styles.pcControlHeader}>
          <div>
            <h2>{pc.display_name ?? pc.pc_name}</h2>
            <div className={styles.pcControlMeta}>
              <span className={styles.pcControlStatus}>{pc.connection_status.toUpperCase()}</span>
              <span>{statusLabel(pc).toUpperCase()}</span>
            </div>
            <p>{customerLabel} / {planLabel}</p>
          </div>
          <button
            aria-label={`Close ${pc.display_name ?? pc.pc_name} control window`}
            className={styles.iconButton}
            disabled={isWorking}
            onClick={onClose}
            type="button"
          >
            <Icon name="x" />
          </button>
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
                  <span>Plan</span>
                  <strong>{planLabel}</strong>
                </div>
                <div className={styles.detailRow}>
                  <span>Started</span>
                  <strong>{pc.started_at ? formatDateTime(pc.started_at) : "Not started"}</strong>
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
              </dl>
            </section>

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

            <section className={styles.controlSection} aria-label="Session actions">
              <h3>Session Actions</h3>
              <div className={styles.modalActionGrid}>
                <button
                  className={styles.primaryButton}
                  disabled={!actions.canStart}
                  onClick={() => onStart(pc)}
                  title={!actions.canStart ? "Start is available only when this PC is idle." : undefined}
                  type="button"
                >
                  <Icon name={pc.connection_status === "offline" ? "power" : "play"} />
                  {pc.connection_status === "offline" ? "Start & Wake" : "Start Session"}
                </button>
                {pc.startup_status !== null ? (
                  <>
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
                  </>
                ) : (
                  <>
                    <button
                      className={styles.secondaryButton}
                      disabled={!actions.canAddTime || isOpenTime}
                      onClick={() => onAddTime(pc)}
                      title={isOpenTime ? "Open Time sessions do not use Add Time." : undefined}
                      type="button"
                    >
                      <Icon name="clockPlus" />
                      Add Time
                    </button>
                    <button
                      className={styles.secondaryButton}
                      disabled={pc.recovery_required || (!actions.canAddTime && !actions.canCollectPayment)}
                      onClick={() => (isOpenTime && !isSettlementPending ? onEnd(pc) : onPayment(pc))}
                      type="button"
                    >
                      <Icon name="card" />
                      {isSettlementPending ? "Collect Payment" : isOpenTime ? "End & Settle" : "Payment"}
                    </button>
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
                  </>
                )}
              </div>
            </section>
          </div>

          <div className={styles.pcControlColumn}>
            <section className={styles.controlSection} aria-label="Messaging controls">
              <h3>Messaging</h3>
              <label className={styles.field}>
                Message title
                <input
                  maxLength={80}
                  onChange={(event) => setMessageTitle(event.target.value)}
                  value={messageTitle}
                />
              </label>
              <label className={styles.field}>
                Customer message
                <input
                  maxLength={500}
                  onChange={(event) => setMessageText(event.target.value)}
                  placeholder="Please return to the cashier."
                  value={messageText}
                />
              </label>
              <label className={styles.field}>
                Severity
                <select
                  onChange={(event) => setMessageSeverity(event.target.value as MessageSeverity)}
                  value={messageSeverity}
                >
                  <option value="info">Info</option>
                  <option value="warning">Warning</option>
                  <option value="urgent">Urgent</option>
                </select>
              </label>
              <button
                className={styles.primaryButton}
                disabled={isWorking || pc.connection_status === "offline"}
                onClick={sendMessage}
                type="button"
              >
                <Icon name="message" />
                Send Message
              </button>
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
                    <Icon name="powerOff" />
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
      </section>
    </div>
  );
}

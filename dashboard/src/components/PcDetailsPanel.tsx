import { useState } from "react";
import styles from "@/app/page.module.css";
import { sendPcMessage, sendPowerCommand, updatePcMacAddress, wakePc } from "@/lib/api";
import { formatDateTime, formatMoney, formatTimer } from "@/lib/time";
import type { MessageSeverity, PcSummary, UserRole } from "@/lib/types";

interface PcDetailsPanelProps {
  pc: PcSummary;
  currentUserRole: UserRole | null;
  onClose: () => void;
  onActionComplete: () => void;
}

export function PcDetailsPanel({ pc, currentUserRole, onClose, onActionComplete }: PcDetailsPanelProps) {
  const isSettlementPending = pc.session_status === "settlement_pending";
  const [messageTitle, setMessageTitle] = useState("Secret Grounds");
  const [messageText, setMessageText] = useState("");
  const [messageSeverity, setMessageSeverity] = useState<MessageSeverity>("info");
  const [macAddress, setMacAddress] = useState(pc.mac_address ?? "");
  const [forceReason, setForceReason] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [isWorking, setIsWorking] = useState(false);
  const canPowerControl = currentUserRole === "owner" || currentUserRole === "admin";
  const isActiveSession = pc.session_status === "active";

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
    <div className={styles.overlay} role="presentation">
      <aside className={styles.sidePanel} aria-label={`${pc.pc_name} details`}>
        <h2>{pc.pc_name}</h2>
        <dl className={styles.detailList}>
          <div className={styles.detailRow}>
            <span>Connection</span>
            <strong>{pc.connection_status}</strong>
          </div>
          <div className={styles.detailRow}>
            <span>Command connection</span>
            <strong>{pc.has_command_connection ? "Connected" : "Not connected"}</strong>
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
            <span>MAC address</span>
            <strong>{pc.mac_address ?? "Not saved"}</strong>
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
        <section className={styles.paymentBox} aria-label="Remote controls">
          <div className={styles.sectionHeader}>
            <div>
              <h2>Remote Controls</h2>
              <p className={styles.panelText}>Fixed allowlisted controls only. No arbitrary commands.</p>
            </div>
          </div>
          {notice ? <div className={styles.statusMessage}>{notice}</div> : null}
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
          <button className={styles.primaryButton} disabled={isWorking || pc.connection_status === "offline"} onClick={sendMessage} type="button">
            Send Message
          </button>
          {canPowerControl ? (
            <>
              <label className={styles.field}>
                Saved MAC address
                <input
                  onChange={(event) => setMacAddress(event.target.value)}
                  placeholder="AA:BB:CC:DD:EE:FF"
                  value={macAddress}
                />
              </label>
              <div className={styles.actions}>
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
                  Save MAC
                </button>
                <button
                  className={styles.secondaryButton}
                  disabled={isWorking || !pc.mac_address}
                  onClick={() =>
                    void runPanelAction(
                      () => wakePc(pc.pc_name),
                      "Wake signal sent. Waiting for PC to come online."
                    )
                  }
                  type="button"
                >
                  Wake PC
                </button>
              </div>
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
              <div className={styles.actions}>
                <button className={styles.secondaryButton} disabled={isWorking || pc.connection_status === "offline"} onClick={() => power("sleep")} type="button">
                  Sleep
                </button>
                <button className={styles.secondaryButton} disabled={isWorking || pc.connection_status === "offline"} onClick={() => power("restart")} type="button">
                  Restart
                </button>
                <button className={styles.dangerButton} disabled={isWorking || pc.connection_status === "offline"} onClick={() => power("shutdown")} type="button">
                  Shutdown
                </button>
              </div>
            </>
          ) : null}
        </section>
        <div className={styles.dialogActions}>
          <button className={styles.secondaryButton} onClick={onClose}>
            Close
          </button>
        </div>
      </aside>
    </div>
  );
}

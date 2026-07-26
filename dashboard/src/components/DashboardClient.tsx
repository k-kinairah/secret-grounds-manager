"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "@/app/page.module.css";
import {
  broadcastAnnouncement,
  endSession,
  fetchPcs,
  fetchCurrentUser,
  fetchRates,
  forceEndSession,
  getPollingIntervalMs,
  recordPayment,
  startMemberSession,
  startOpenTimeSession,
  startPrepaidSession,
  addMemberTime,
  addPrepaidTime
} from "@/lib/api";
import { getPcActionAvailability, summarizePcs } from "@/lib/pcStatus";
import { calculateOpenTimeEstimate, secondsFromScheduledEnd } from "@/lib/time";
import type { CurrentUser, MessageSeverity, PcSummary, RateProfile } from "@/lib/types";
import { DashboardSummary } from "./DashboardSummary";
import { EndSessionDialog } from "./EndSessionDialog";
import { PcCard } from "./PcCard";
import { PcDetailsPanel } from "./PcDetailsPanel";
import { PaymentDialog } from "./PaymentDialog";
import { SessionDialog } from "./SessionDialog";

type DialogState =
  | { type: "start"; pc: PcSummary }
  | { type: "add"; pc: PcSummary }
  | { type: "end"; pc: PcSummary }
  | { type: "resolve"; pc: PcSummary }
  | { type: "details"; pc: PcSummary }
  | { type: "payment"; pc: PcSummary }
  | { type: "announcement" }
  | null;

export function DashboardClient() {
  const [pcs, setPcs] = useState<PcSummary[]>([]);
  const [rates, setRates] = useState<RateProfile[]>([]);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogState>(null);
  const [isMutating, setIsMutating] = useState(false);
  const [now, setNow] = useState<Date | null>(null);
  const isPollingRef = useRef(false);
  const pollIntervalMs = getPollingIntervalMs();

  const refresh = useCallback(async () => {
    if (isPollingRef.current) {
      return;
    }

    isPollingRef.current = true;
    try {
      const [nextPcs, nextRates, nextUser] = await Promise.all([
        fetchPcs(),
        fetchRates(),
        fetchCurrentUser()
      ]);
      setPcs(nextPcs);
      setRates(nextRates);
      setCurrentUser(nextUser);
      setIsConnected(true);
      setHasLoadedOnce(true);
      setMessage(null);
    } catch (error) {
      setIsConnected(false);
      setHasLoadedOnce(true);
      setMessage(
        error instanceof Error
          ? error.message
          : "Server unavailable. Showing the most recent successful data."
      );
    } finally {
      isPollingRef.current = false;
    }
  }, []);

  useEffect(() => {
    const initialFetchId = window.setTimeout(() => void refresh(), 0);
    const intervalId = window.setInterval(() => void refresh(), pollIntervalMs);
    return () => {
      window.clearTimeout(initialFetchId);
      window.clearInterval(intervalId);
    };
  }, [pollIntervalMs, refresh]);

  useEffect(() => {
    const updateClock = () => setNow(new Date());
    const initialClockId = window.setTimeout(updateClock, 0);
    const intervalId = window.setInterval(updateClock, 1000);
    return () => {
      window.clearTimeout(initialClockId);
      window.clearInterval(intervalId);
    };
  }, []);

  const livePcs = useMemo(
    () =>
      pcs.map((pc) => {
        const nowMs = now?.getTime();
        if (pc.session_status !== "active" || nowMs === undefined) {
          return pc;
        }
        if (pc.session_mode === "open_time") {
          const estimate = calculateOpenTimeEstimate(
            pc.started_at,
            nowMs,
            pc.metered_rate_duration_seconds_snapshot,
            pc.metered_rate_price_snapshot,
            pc.total_paid
          );
          if (estimate === null) {
            return pc;
          }

          return {
            ...pc,
            actual_elapsed_seconds: estimate.actualElapsedSeconds,
            billed_seconds: estimate.billedSeconds,
            estimated_total_amount: estimate.estimatedTotalAmount,
            estimated_balance_due: estimate.estimatedBalanceDue,
            total_amount: estimate.estimatedTotalAmount,
            balance_due: estimate.estimatedBalanceDue
          };
        }

        return {
          ...pc,
          remaining_seconds: secondsFromScheduledEnd(pc.scheduled_end_at, nowMs)
        };
      }),
    [pcs, now]
  );
  const summary = summarizePcs(livePcs);
  const meteredRate = useMemo(
    () => rates.find((rate) => rate.rate_type === "metered" && rate.is_active) ?? null,
    [rates]
  );
  const packageRates = useMemo(
    () => rates.filter((rate) => rate.rate_type === "package" && rate.is_active),
    [rates]
  );

  async function runAction(action: () => Promise<unknown>, successMessage: string) {
    setIsMutating(true);
    try {
      await action();
      setMessage(successMessage);
      setDialog(null);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Action failed. Please try again.");
    } finally {
      setIsMutating(false);
    }
  }

  async function endSessionAction(pc: PcSummary) {
    setIsMutating(true);
    try {
      const response = await endSession(pc.active_session_id as string);
      if (pc.session_mode === "open_time") {
        const settlementPc: PcSummary = {
          ...pc,
          active_session_id: response.session.session_id,
          session_status: response.session.status === "settlement_pending" ? "settlement_pending" : "active",
          total_amount: response.session.total_amount,
          total_paid: response.session.total_paid,
          balance_due: response.session.balance_due,
          payment_status: response.session.payment_status,
          billed_seconds: response.session.billed_seconds,
          final_computed_amount: response.session.final_computed_amount,
          actual_elapsed_seconds: response.session.actual_elapsed_seconds,
          settlement_pending: response.session.settlement_pending
        };
        setMessage(`${pc.pc_name} open-time session ended. Settlement is pending.`);
        setDialog({ type: "payment", pc: settlementPc });
      } else {
        setMessage(`${pc.pc_name} session ended.`);
        setDialog(null);
      }
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Action failed. Please try again.");
    } finally {
      setIsMutating(false);
    }
  }

  async function forceEndSessionAction(pc: PcSummary) {
    if (!pc.active_session_id) {
      setMessage("This PC no longer has an active session.");
      setDialog(null);
      return;
    }

    setIsMutating(true);
    try {
      const response = await forceEndSession(pc.active_session_id);
      if (response.session.session_mode === "open_time" && response.session.settlement_pending) {
        const settlementPc: PcSummary = {
          ...pc,
          active_session_id: response.session.session_id,
          session_status: response.session.status === "settlement_pending" ? "settlement_pending" : "active",
          total_amount: response.session.total_amount,
          total_paid: response.session.total_paid,
          balance_due: response.session.balance_due,
          payment_status: response.session.payment_status,
          billed_seconds: response.session.billed_seconds,
          final_computed_amount: response.session.final_computed_amount,
          actual_elapsed_seconds: response.session.actual_elapsed_seconds,
          settlement_pending: response.session.settlement_pending,
          recovery_required: false,
          recovery_reason: null
        };
        setMessage(`${pc.pc_name} recovery session force-ended. Settlement is pending.`);
        setDialog({ type: "payment", pc: settlementPc });
      } else {
        setMessage(`${pc.pc_name} recovery session resolved.`);
        setDialog(null);
      }
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Recovery action failed.");
    } finally {
      setIsMutating(false);
    }
  }

  function findCurrentPc(pc: PcSummary): PcSummary {
    const currentPc = livePcs.find((item) => item.pc_name === pc.pc_name);
    if (pc.active_session_id && !currentPc?.active_session_id) {
      return pc;
    }

    return currentPc ?? pc;
  }

  const currentTime = now === null ? "Loading time..." : now.toLocaleString();

  return (
    <main className={styles.page}>
      <header className={styles.topBar}>
        <div className={styles.brandBlock}>
          <h1>Secret Grounds</h1>
          <p>Cashier session dashboard</p>
        </div>
        <div className={styles.serverState}>
          <div>{currentTime}</div>
          <div
            className={`${styles.indicator} ${
              isConnected ? styles.connected : styles.disconnected
            }`}
          >
            Server {isConnected ? "Connected" : "Disconnected"}
          </div>
          <button className={styles.secondaryButton} onClick={() => setDialog({ type: "announcement" })} type="button">
            Announcement
          </button>
        </div>
      </header>

      <DashboardSummary summary={summary} />

      {message ? <div className={styles.statusMessage}>{message}</div> : null}

      <section className={styles.pcGrid} aria-label="PC grid">
        {!hasLoadedOnce
          ? Array.from({ length: 4 }, (_, index) => (
              <div className={styles.loadingCard} key={index}>
                Loading PC status...
              </div>
            ))
          : null}
        {hasLoadedOnce && livePcs.length === 0 ? (
          <div className={styles.emptyState}>No registered PCs.</div>
        ) : null}
        {livePcs.map((pc) => (
          <PcCard
            key={pc.pc_name}
            pc={pc}
            onAddTime={(selectedPc) => setDialog({ type: "add", pc: selectedPc })}
            onDetails={(selectedPc) => setDialog({ type: "details", pc: selectedPc })}
            onEnd={(selectedPc) => setDialog({ type: "end", pc: selectedPc })}
            onResolve={(selectedPc) => setDialog({ type: "resolve", pc: selectedPc })}
            onStart={(selectedPc) => setDialog({ type: "start", pc: selectedPc })}
            onPayment={(selectedPc) => setDialog({ type: "payment", pc: selectedPc })}
            currentUserRole={currentUser?.role ?? null}
          />
        ))}
      </section>

      {dialog?.type === "start" ? (
        <SessionDialog
          isLoading={isMutating}
          mode="start"
          onCancel={() => setDialog(null)}
          onConfirm={(selection) =>
            void runAction(
              () =>
                "sessionMode" in selection
                  ? selection.sessionMode === "open_time"
                    ? startOpenTimeSession(dialog.pc.pc_name)
                    : startMemberSession(dialog.pc.pc_name, selection)
                  : startPrepaidSession(dialog.pc.pc_name, selection),
              `${dialog.pc.pc_name} session started.`
            )
          }
          pc={dialog.pc}
          meteredRate={meteredRate}
          packageRates={packageRates}
        />
      ) : null}

      {dialog?.type === "add" ? (
        <SessionDialog
          isLoading={isMutating}
          mode="add"
          onCancel={() => setDialog(null)}
          onConfirm={(selection) => {
            const currentPc = findCurrentPc(dialog.pc);
            if (!currentPc.active_session_id) {
              setMessage("This PC no longer has an active session.");
              setDialog(null);
              return;
            }
            void runAction(
              () =>
                "sessionMode" in selection
                  ? selection.sessionMode === "member_balance"
                    ? addMemberTime(currentPc.active_session_id as string, selection)
                    : Promise.reject(new Error("Open-time sessions do not support add time."))
                  : addPrepaidTime(currentPc.active_session_id as string, selection),
              `${currentPc.pc_name} time updated.`
            );
          }}
          pc={dialog.pc}
          meteredRate={meteredRate}
          packageRates={packageRates}
        />
      ) : null}

      {dialog?.type === "end" ? (
        <EndSessionDialog
          isLoading={isMutating}
          onCancel={() => setDialog(null)}
          onConfirm={() => {
            const currentPc = findCurrentPc(dialog.pc);
            const actions = getPcActionAvailability(currentPc);
            if (!actions.canEnd || !currentPc.active_session_id) {
              setMessage("This session is no longer available to end.");
              setDialog(null);
              return;
            }
            void endSessionAction(currentPc);
          }}
          pc={findCurrentPc(dialog.pc)}
        />
      ) : null}

      {dialog?.type === "resolve" ? (
        <EndSessionDialog
          isLoading={isMutating}
          onCancel={() => setDialog(null)}
          onConfirm={() => void forceEndSessionAction(findCurrentPc(dialog.pc))}
          pc={findCurrentPc(dialog.pc)}
          recoveryMode
        />
      ) : null}

      {dialog?.type === "details" ? (
        <PcDetailsPanel
          currentUserRole={currentUser?.role ?? null}
          onActionComplete={() => void refresh()}
          pc={findCurrentPc(dialog.pc)}
          onClose={() => setDialog(null)}
        />
      ) : null}

      {dialog?.type === "payment" ? (
        <PaymentDialog
          isLoading={isMutating}
          onCancel={() => setDialog(null)}
          onConfirm={(input) => {
            const currentPc = findCurrentPc(dialog.pc);
            if (!currentPc.active_session_id) {
              setMessage("This PC no longer has an active session.");
              setDialog(null);
              return;
            }
            if (currentPc.session_mode === "open_time" && currentPc.session_status === "active") {
              setDialog({ type: "end", pc: currentPc });
              setMessage("End the Open Time session first so the final amount can be calculated.");
              return;
            }
            void runAction(
              () => recordPayment(currentPc.active_session_id as string, input),
              `${currentPc.pc_name} payment recorded.`
            );
          }}
          pc={findCurrentPc(dialog.pc)}
        />
      ) : null}

      {dialog?.type === "announcement" ? (
        <AnnouncementDialog
          isLoading={isMutating}
          onCancel={() => setDialog(null)}
          onConfirm={(input) =>
            void runAction(
              () => broadcastAnnouncement(input),
              "Announcement sent."
            )
          }
        />
      ) : null}
    </main>
  );
}

function AnnouncementDialog({
  isLoading,
  onCancel,
  onConfirm
}: {
  isLoading: boolean;
  onCancel: () => void;
  onConfirm: (input: {
    title: string;
    message: string;
    severity: MessageSeverity;
    duration_seconds?: number;
    require_acknowledgement: boolean;
  }) => void;
}) {
  const [title, setTitle] = useState("Secret Grounds");
  const [message, setMessage] = useState("");
  const [severity, setSeverity] = useState<MessageSeverity>("info");
  const [duration, setDuration] = useState("30");

  return (
    <div className={styles.overlay} role="presentation">
      <section className={styles.dialog} role="dialog" aria-modal="true" aria-label="Broadcast announcement">
        <h2>Broadcast Announcement</h2>
        <div className={styles.amountPreview}>
          <span>Targets</span>
          <strong>All online PCs with active command connections.</strong>
        </div>
        <label className={styles.field}>
          Title
          <input maxLength={80} onChange={(event) => setTitle(event.target.value)} value={title} />
        </label>
        <label className={styles.field}>
          Message
          <input
            maxLength={500}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="Please save your work. The shop is closing soon."
            value={message}
          />
        </label>
        <label className={styles.field}>
          Severity
          <select onChange={(event) => setSeverity(event.target.value as MessageSeverity)} value={severity}>
            <option value="info">Info</option>
            <option value="warning">Warning</option>
            <option value="urgent">Urgent</option>
          </select>
        </label>
        <label className={styles.field}>
          Display seconds
          <input inputMode="numeric" onChange={(event) => setDuration(event.target.value)} value={duration} />
        </label>
        <div className={styles.dialogActions}>
          <button className={styles.secondaryButton} disabled={isLoading} onClick={onCancel} type="button">
            Cancel
          </button>
          <button
            className={styles.primaryButton}
            disabled={isLoading || !message.trim()}
            onClick={() =>
              onConfirm({
                title,
                message,
                severity,
                duration_seconds: Number(duration) || undefined,
                require_acknowledgement: severity === "urgent"
              })
            }
            type="button"
          >
            {isLoading ? "Sending..." : "Send Announcement"}
          </button>
        </div>
      </section>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "@/app/page.module.css";
import {
  addTime,
  endSession,
  fetchPcs,
  fetchRates,
  getPollingIntervalMs,
  startSession
} from "@/lib/api";
import { getPcActionAvailability, summarizePcs } from "@/lib/pcStatus";
import { secondsFromScheduledEnd } from "@/lib/time";
import type { PcSummary, RateProfile } from "@/lib/types";
import { DashboardSummary } from "./DashboardSummary";
import { EndSessionDialog } from "./EndSessionDialog";
import { PcCard } from "./PcCard";
import { PcDetailsPanel } from "./PcDetailsPanel";
import { SessionDialog } from "./SessionDialog";

type DialogState =
  | { type: "start"; pc: PcSummary }
  | { type: "add"; pc: PcSummary }
  | { type: "end"; pc: PcSummary }
  | { type: "details"; pc: PcSummary }
  | null;

export function DashboardClient() {
  const [pcs, setPcs] = useState<PcSummary[]>([]);
  const [rates, setRates] = useState<RateProfile[]>([]);
  const [isConnected, setIsConnected] = useState(false);
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
      const [nextPcs, nextRates] = await Promise.all([fetchPcs(), fetchRates()]);
      setPcs(nextPcs);
      setRates(nextRates);
      setIsConnected(true);
      setMessage(null);
    } catch (error) {
      setIsConnected(false);
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
      pcs.map((pc) => ({
        ...pc,
        remaining_seconds:
          pc.session_status === "active"
            ? secondsFromScheduledEnd(pc.scheduled_end_at, now?.getTime())
            : pc.remaining_seconds
      })),
    [pcs, now]
  );
  const summary = summarizePcs(livePcs);

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

  function findCurrentPc(pc: PcSummary): PcSummary {
    return livePcs.find((item) => item.pc_name === pc.pc_name) ?? pc;
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
        </div>
      </header>

      <DashboardSummary summary={summary} />

      {message ? <div className={styles.statusMessage}>{message}</div> : null}

      <section className={styles.pcGrid} aria-label="PC grid">
        {livePcs.map((pc) => (
          <PcCard
            key={pc.pc_name}
            pc={pc}
            onAddTime={(selectedPc) => setDialog({ type: "add", pc: selectedPc })}
            onDetails={(selectedPc) => setDialog({ type: "details", pc: selectedPc })}
            onEnd={(selectedPc) => setDialog({ type: "end", pc: selectedPc })}
            onStart={(selectedPc) => setDialog({ type: "start", pc: selectedPc })}
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
              () => startSession(dialog.pc.pc_name, selection),
              `${dialog.pc.pc_name} session started.`
            )
          }
          pc={dialog.pc}
          rates={rates}
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
              () => addTime(currentPc.active_session_id as string, selection),
              `${currentPc.pc_name} time updated.`
            );
          }}
          pc={dialog.pc}
          rates={rates}
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
            void runAction(
              () => endSession(currentPc.active_session_id as string),
              `${currentPc.pc_name} session ended.`
            );
          }}
          pc={findCurrentPc(dialog.pc)}
        />
      ) : null}

      {dialog?.type === "details" ? (
        <PcDetailsPanel pc={findCurrentPc(dialog.pc)} onClose={() => setDialog(null)} />
      ) : null}
    </main>
  );
}

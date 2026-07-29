"use client";

import { useEffect, useState } from "react";
import styles from "@/app/page.module.css";
import { fetchShift, fetchShifts } from "@/lib/api";
import { formatDateTime, formatMoney } from "@/lib/time";
import type { CashierShiftDetail, CashierShiftRecord } from "@/lib/types";
import { PageTitleIcon } from "./PageTitleIcon";

function formatDuration(openedAt: string, closedAt: string | null): string {
  const endMs = closedAt ? new Date(closedAt).getTime() : Date.now();
  const seconds = Math.max(0, Math.floor((endMs - new Date(openedAt).getTime()) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

export function ShiftsManager() {
  const [shifts, setShifts] = useState<CashierShiftRecord[]>([]);
  const [selectedShift, setSelectedShift] = useState<CashierShiftDetail | null>(null);
  const [message, setMessage] = useState<string | null>("Loading shifts...");

  useEffect(() => {
    let isMounted = true;
    fetchShifts()
      .then((records) => {
        if (!isMounted) {
          return;
        }
        setShifts(records);
        setMessage(null);
      })
      .catch((error) => {
        if (isMounted) {
          setMessage(error instanceof Error ? error.message : "Could not load shifts.");
        }
      });
    return () => {
      isMounted = false;
    };
  }, []);

  async function handleSelect(shiftId: string) {
    setMessage("Loading shift detail...");
    try {
      setSelectedShift(await fetchShift(shiftId));
      setMessage(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load shift detail.");
    }
  }

  return (
    <main className={styles.page}>
      <div className={styles.topBar}>
        <div className={`${styles.brandBlock} pageTitleLockup`}>
          <PageTitleIcon name="receipt" />
          <div>
            <h1>Shifts</h1>
            <p>Cash drawer ownership, payment totals, and closing snapshots.</p>
          </div>
        </div>
      </div>

      {message ? <div className={styles.statusMessage}>{message}</div> : null}
      {shifts.length === 0 && !message ? (
        <div className={styles.emptyState}>No cashier shifts yet.</div>
      ) : (
        <div className={styles.pcGrid}>
          {shifts.map((shift) => (
            <article className={styles.pcCard} key={shift.shift_id}>
              <div className={styles.pcHeader}>
                <div>
                  <div className={styles.pcName}>{shift.cashier_name ?? "Cashier"}</div>
                  <p>{formatDateTime(shift.opened_at)}</p>
                </div>
                <span
                  className={`${styles.statusPill} ${
                    shift.status === "open" ? styles.badgeOnline : styles.badgeOffline
                  }`}
                >
                  {shift.status}
                </span>
              </div>
              <dl className={styles.detailList}>
                <div className={styles.detailRow}>
                  <span>Duration</span>
                  <strong>{formatDuration(shift.opened_at, shift.closed_at)}</strong>
                </div>
                <div className={styles.detailRow}>
                  <span>Opening cash</span>
                  <strong>{formatMoney(shift.opening_cash)}</strong>
                </div>
                <div className={styles.detailRow}>
                  <span>Expected</span>
                  <strong>{formatMoney(shift.expected_cash ?? "0.00")}</strong>
                </div>
                <div className={styles.detailRow}>
                  <span>Actual</span>
                  <strong>{formatMoney(shift.actual_cash)}</strong>
                </div>
                <div className={styles.detailRow}>
                  <span>Difference</span>
                  <strong>{formatMoney(shift.difference)}</strong>
                </div>
              </dl>
              <button
                className={styles.secondaryButton}
                onClick={() => void handleSelect(shift.shift_id)}
                type="button"
              >
                View Detail
              </button>
            </article>
          ))}
        </div>
      )}

      {selectedShift ? (
        <div className={styles.overlay} role="presentation">
          <section
            aria-label="Shift detail"
            className={styles.dialog}
            role="dialog"
          >
            <h2>{selectedShift.shift.cashier_name ?? "Cashier"} Shift</h2>
            <dl className={styles.detailList}>
              <div className={styles.detailRow}>
                <span>Expected drawer</span>
                <strong>{formatMoney(selectedShift.totals.expected_cash)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Gross sales</span>
                <strong>{formatMoney(selectedShift.totals.gross_session_sales)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Cash</span>
                <strong>{formatMoney(selectedShift.totals.cash_payments)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>GCash</span>
                <strong>{formatMoney(selectedShift.totals.gcash_payments)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Maya</span>
                <strong>{formatMoney(selectedShift.totals.maya_payments)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Other</span>
                <strong>{formatMoney(selectedShift.totals.other_payments)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Voided</span>
                <strong>{formatMoney(selectedShift.totals.voided_payments)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Payments</span>
                <strong>{selectedShift.totals.payment_count}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Sessions handled</span>
                <strong>{selectedShift.totals.sessions_handled}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Unresolved settlements</span>
                <strong>{selectedShift.totals.settlement_pending_sessions}</strong>
              </div>
            </dl>
            {selectedShift.shift.closing_notes ? <p>{selectedShift.shift.closing_notes}</p> : null}
            <div className={styles.dialogActions}>
              <button
                className={styles.secondaryButton}
                onClick={() => setSelectedShift(null)}
                type="button"
              >
                Close
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}

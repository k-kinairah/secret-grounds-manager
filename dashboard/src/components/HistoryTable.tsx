"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchBilling, fetchHistory } from "@/lib/api";
import { formatDateTime, formatDuration, formatMoney } from "@/lib/time";
import type { BillingSummary, SessionRecord, SessionStatus } from "@/lib/types";
import styles from "@/app/history/history.module.css";

const statuses: Array<SessionStatus | "all"> = [
  "all",
  "active",
  "ending",
  "settlement_pending",
  "completed",
  "ended_manually",
  "cancelled",
  "failed"
];

const statusClassName: Record<SessionStatus, string> = {
  active: styles.statusActive,
  ending: styles.statusEnded,
  settlement_pending: styles.statusEnded,
  completed: styles.statusCompleted,
  ended_manually: styles.statusEnded,
  cancelled: styles.statusCancelled,
  failed: styles.statusFailed
};

const paymentStatusLabel = {
  unpaid: "Unpaid",
  partially_paid: "Partial",
  paid: "Paid",
  overpaid: "Overpaid"
};

const paymentStatusClassName = {
  unpaid: styles.paymentUnpaid,
  partially_paid: styles.paymentPartial,
  paid: styles.paymentPaid,
  overpaid: styles.paymentOverpaid
};

export function HistoryTable() {
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [pcFilter, setPcFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<SessionStatus | "all">("all");
  const [dateFilter, setDateFilter] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [billing, setBilling] = useState<BillingSummary | null>(null);

  useEffect(() => {
    fetchHistory()
      .then((history) => {
        setSessions(history);
        setMessage(null);
      })
      .catch((error) => {
        setMessage(
          error instanceof Error ? error.message : "Could not load session history."
        );
      });
  }, []);

  const filteredSessions = useMemo(
    () =>
      sessions.filter((session) => {
        const matchesPc = session.pc_name.toLowerCase().includes(pcFilter.toLowerCase());
        const matchesStatus =
          statusFilter === "all" ? true : session.status === statusFilter;
        const matchesDate = dateFilter
          ? session.started_at.slice(0, 10) === dateFilter
          : true;
        return matchesPc && matchesStatus && matchesDate;
      }),
    [dateFilter, pcFilter, sessions, statusFilter]
  );

  async function openBilling(sessionId: string) {
    try {
      setBilling(await fetchBilling(sessionId));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load billing details.");
    }
  }

  return (
    <>
      <section className={styles.filters} aria-label="History filters">
        <label className={styles.field}>
          PC name
          <input
            onChange={(event) => setPcFilter(event.target.value)}
            placeholder="PC-01"
            type="search"
            value={pcFilter}
          />
        </label>
        <label className={styles.field}>
          Status
          <select
            onChange={(event) => setStatusFilter(event.target.value as SessionStatus | "all")}
            value={statusFilter}
          >
            {statuses.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          Date
          <input onChange={(event) => setDateFilter(event.target.value)} type="date" />
        </label>
      </section>

      {message ? <div className={styles.message}>{message}</div> : null}

      <section className={styles.tableWrap}>
        {filteredSessions.length === 0 ? (
          <div className={styles.emptyState}>No session history yet.</div>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>PC</th>
                <th>Started</th>
                <th>Ended</th>
                <th>Original</th>
                <th>Added</th>
                <th>Total</th>
                <th>Rate</th>
                <th>Base Amount</th>
                <th>Added Amount</th>
                <th>Total Amount</th>
                <th>Total Paid</th>
                <th>Balance</th>
                <th>Payment</th>
                <th>Status</th>
                <th>Ended Reason</th>
                <th>Billing</th>
              </tr>
            </thead>
            <tbody>
              {filteredSessions.map((session) => (
                <tr key={session.session_id}>
                  <td>
                    <strong>{session.pc_name}</strong>
                  </td>
                  <td>{formatDateTime(session.started_at)}</td>
                  <td>{formatDateTime(session.ended_at)}</td>
                  <td>{formatDuration(session.original_seconds)}</td>
                  <td>{formatDuration(session.added_seconds)}</td>
                  <td>{formatDuration(session.total_seconds)}</td>
                  <td>{session.rate_name_snapshot ?? "Custom Time"}</td>
                  <td>{formatMoney(session.base_amount)}</td>
                  <td>{formatMoney(session.additional_amount)}</td>
                  <td>{formatMoney(session.total_amount)}</td>
                  <td>{formatMoney(session.total_paid)}</td>
                  <td>{formatMoney(session.balance_due)}</td>
                  <td>
                    <span
                      className={`${styles.statusBadge} ${
                        paymentStatusClassName[session.payment_status]
                      }`}
                    >
                      {paymentStatusLabel[session.payment_status]}
                    </span>
                  </td>
                  <td>
                    <span className={`${styles.statusBadge} ${statusClassName[session.status]}`}>
                      {session.status}
                    </span>
                  </td>
                  <td>{session.ended_reason ?? "None"}</td>
                  <td>
                    <button
                      className={styles.tableButton}
                      onClick={() => void openBilling(session.session_id)}
                      type="button"
                    >
                      View
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      {billing ? (
        <div className={styles.overlay} role="presentation">
          <section className={styles.dialog} role="dialog" aria-modal="true" aria-label="Billing details">
            <h2>Billing Details</h2>
            <dl className={styles.billingList}>
              <div>
                <span>Total</span>
                <strong>{formatMoney(billing.total_amount)}</strong>
              </div>
              <div>
                <span>Paid</span>
                <strong>{formatMoney(billing.total_paid)}</strong>
              </div>
              <div>
                <span>Balance</span>
                <strong>{formatMoney(billing.balance_due)}</strong>
              </div>
              <div>
                <span>Status</span>
                <strong>{paymentStatusLabel[billing.payment_status]}</strong>
              </div>
            </dl>
            {billing.payments.length === 0 ? (
              <div className={styles.emptyState}>No payments recorded.</div>
            ) : (
              <div className={styles.paymentList}>
                {billing.payments.map((payment) => (
                  <div className={styles.paymentRow} key={payment.payment_id}>
                    <strong>{formatMoney(payment.amount)}</strong>
                    <span>{payment.payment_method}</span>
                    <span>{payment.status}</span>
                    <span>{formatDateTime(payment.created_at)}</span>
                  </div>
                ))}
              </div>
            )}
            <div className={styles.dialogActions}>
              <button className={styles.tableButton} onClick={() => setBilling(null)} type="button">
                Close
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}

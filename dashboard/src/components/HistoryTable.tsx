"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchHistory } from "@/lib/api";
import { formatDateTime, formatDuration, formatMoney } from "@/lib/time";
import type { SessionRecord, SessionStatus } from "@/lib/types";
import styles from "@/app/history/history.module.css";

const statuses: Array<SessionStatus | "all"> = [
  "all",
  "active",
  "completed",
  "ended_manually",
  "cancelled",
  "failed"
];

export function HistoryTable() {
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [pcFilter, setPcFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<SessionStatus | "all">("all");
  const [dateFilter, setDateFilter] = useState("");
  const [message, setMessage] = useState<string | null>(null);

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
              <th>Status</th>
              <th>Ended Reason</th>
            </tr>
          </thead>
          <tbody>
            {filteredSessions.map((session) => (
              <tr key={session.session_id}>
                <td>{session.pc_name}</td>
                <td>{formatDateTime(session.started_at)}</td>
                <td>{formatDateTime(session.ended_at)}</td>
                <td>{formatDuration(session.original_seconds)}</td>
                <td>{formatDuration(session.added_seconds)}</td>
                <td>{formatDuration(session.total_seconds)}</td>
                <td>{session.rate_name_snapshot ?? "Custom Time"}</td>
                <td>{formatMoney(session.base_amount)}</td>
                <td>{formatMoney(session.additional_amount)}</td>
                <td>{formatMoney(session.total_amount)}</td>
                <td>{session.status}</td>
                <td>{session.ended_reason ?? "None"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

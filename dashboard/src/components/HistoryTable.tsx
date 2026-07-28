"use client";

import { memo, useEffect, useMemo, useState } from "react";
import { fetchBilling, fetchHistory } from "@/lib/api";
import { formatDateTime, formatMoney } from "@/lib/time";
import type { BillingSummary, SessionRecord, SessionStatus } from "@/lib/types";
import styles from "@/app/history/history.module.css";

const pageSize = 25;
const statuses: Array<SessionStatus | "all"> = [
  "all",
  "active",
  "grace_period",
  "settlement_pending",
  "completed",
  "ended_manually",
  "cancelled",
  "failed"
];
const dateRanges = ["all", "today", "yesterday", "this_week", "this_month"] as const;
type DateRange = (typeof dateRanges)[number];

const statusClassName: Record<SessionStatus, string> = {
  active: styles.statusActive,
  grace_period: styles.statusWarning,
  shutting_down: styles.statusWarning,
  ending: styles.statusWarning,
  settlement_pending: styles.statusWarning,
  completed: styles.statusCompleted,
  ended_manually: styles.statusCompleted,
  cancelled: styles.statusCancelled,
  failed: styles.statusFailed
};

function readableStatus(status: SessionStatus): string {
  const labels: Record<SessionStatus, string> = {
    active: "Active",
    grace_period: "Grace Period",
    shutting_down: "Shutdown Pending",
    ending: "Ending",
    settlement_pending: "Settlement Due",
    completed: "Completed",
    ended_manually: "Completed",
    cancelled: "Cancelled",
    failed: "Shutdown Failed"
  };
  return labels[status];
}

function readableDuration(seconds: number | null, active = false): string {
  if (seconds === null || seconds <= 0) {
    return active ? "Live" : "-";
  }
  if (seconds < 60) {
    return seconds < 30 ? "Under 1 min" : `${seconds} sec`;
  }
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  if (hours > 0) {
    return minutes > 0 ? `${hours} hr ${minutes} min` : `${hours} hr`;
  }
  return remainder > 0 && minutes < 10 ? `${minutes} min ${remainder} sec` : `${minutes} min`;
}

function customerName(session: SessionRecord): string {
  return session.member_name_snapshot ?? session.member_code_snapshot ?? "Walk-in";
}

function sessionType(session: SessionRecord): string {
  if (session.session_mode === "member_balance") {
    return "Member Time";
  }
  if (session.session_mode === "open_time") {
    return "Open Time";
  }
  return session.rate_name_snapshot ? "Prepaid" : "Custom Time";
}

function isInDateRange(session: SessionRecord, range: DateRange): boolean {
  if (range === "all") {
    return true;
  }
  const started = new Date(session.started_at);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const start = new Date(today);
  const end = new Date(today);
  if (range === "today") {
    end.setDate(end.getDate() + 1);
  } else if (range === "yesterday") {
    start.setDate(start.getDate() - 1);
  } else if (range === "this_week") {
    start.setDate(start.getDate() - start.getDay());
    end.setDate(end.getDate() + 1);
  } else if (range === "this_month") {
    start.setDate(1);
    end.setDate(end.getDate() + 1);
  }
  return started >= start && started < end;
}

const HistoryRow = memo(function HistoryRow({
  onDetails,
  session
}: {
  onDetails: (session: SessionRecord) => void;
  session: SessionRecord;
}) {
  const active = session.status === "active";
  const balance = Number(session.balance_due);
  return (
    <tr>
      <td><strong>{session.pc_name}</strong></td>
      <td>
        <strong>{customerName(session)}</strong>
        <span>{session.member_name_snapshot ? "Member" : "Walk-in"}</span>
      </td>
      <td>
        <strong>{sessionType(session)}</strong>
        <span>{session.rate_name_snapshot ?? session.member_name_snapshot ?? "Regular"}</span>
      </td>
      <td>{formatDateTime(session.started_at)}</td>
      <td>{readableDuration(session.actual_elapsed_seconds ?? session.total_seconds, active)}</td>
      <td>{formatMoney(session.total_amount)}</td>
      <td>{formatMoney(session.total_paid)}</td>
      <td className={balance > 0 ? styles.balanceDue : ""}>{balance > 0 ? formatMoney(session.balance_due) : "Paid"}</td>
      <td><span className={`${styles.statusBadge} ${statusClassName[session.status]}`}>{readableStatus(session.status)}</span></td>
      <td><button className={styles.tableButton} onClick={() => onDetails(session)} type="button">View Details</button></td>
    </tr>
  );
});

export function HistoryTable() {
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<SessionStatus | "all">("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [dateRange, setDateRange] = useState<DateRange>("today");
  const [page, setPage] = useState(1);
  const [message, setMessage] = useState<string | null>(null);
  const [selectedSession, setSelectedSession] = useState<SessionRecord | null>(null);
  const [billing, setBilling] = useState<BillingSummary | null>(null);

  useEffect(() => {
    fetchHistory()
      .then((history) => {
        setSessions(history);
        setMessage(null);
      })
      .catch((error) => {
        setMessage(error instanceof Error ? error.message : "Could not load session history.");
      });
  }, []);

  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedSearch(search), 220);
    return () => window.clearTimeout(id);
  }, [search]);

  const filteredSessions = useMemo(() => {
    const query = debouncedSearch.trim().toLowerCase();
    return sessions.filter((session) => {
      const matchesSearch =
        !query ||
        [session.pc_name, customerName(session), session.rate_name_snapshot ?? "", session.session_mode]
          .join(" ")
          .toLowerCase()
          .includes(query);
      const matchesStatus = statusFilter === "all" || session.status === statusFilter;
      const matchesType = typeFilter === "all" || session.session_mode === typeFilter;
      return matchesSearch && matchesStatus && matchesType && isInDateRange(session, dateRange);
    });
  }, [dateRange, debouncedSearch, sessions, statusFilter, typeFilter]);

  const summary = useMemo(() => {
    return filteredSessions.reduce(
      (totals, session) => ({
        charged: totals.charged + Number(session.total_amount),
        paid: totals.paid + Number(session.total_paid),
        seconds: totals.seconds + (session.actual_elapsed_seconds ?? session.total_seconds ?? 0),
        outstanding: totals.outstanding + Number(session.balance_due)
      }),
      { charged: 0, paid: 0, seconds: 0, outstanding: 0 }
    );
  }, [filteredSessions]);
  const totalPages = Math.max(1, Math.ceil(filteredSessions.length / pageSize));
  const pageSessions = filteredSessions.slice((page - 1) * pageSize, page * pageSize);

  async function openDetails(session: SessionRecord) {
    setSelectedSession(session);
    setBilling(null);
    try {
      setBilling(await fetchBilling(session.session_id));
    } catch {
      setBilling(null);
    }
  }

  function clearFilters() {
    setSearch("");
    setStatusFilter("all");
    setTypeFilter("all");
    setDateRange("today");
    setPage(1);
  }

  return (
    <>
      <section className={styles.summaryStrip} aria-label="Filtered history summary">
        <div><span>Sessions</span><strong>{filteredSessions.length}</strong></div>
        <div><span>Total Usage</span><strong>{readableDuration(summary.seconds)}</strong></div>
        <div><span>Total Charged</span><strong>{formatMoney(summary.charged)}</strong></div>
        <div><span>Total Paid</span><strong>{formatMoney(summary.paid)}</strong></div>
        <div><span>Outstanding</span><strong className={summary.outstanding > 0 ? styles.balanceDue : ""}>{formatMoney(summary.outstanding)}</strong></div>
      </section>

      <section className={styles.filters} aria-label="History filters">
        <label className={styles.field}>
          Search
          <input
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
            placeholder="PC, customer, member, or operator"
            type="search"
            value={search}
          />
        </label>
        <label className={styles.field}>
          Status
          <select onChange={(event) => setStatusFilter(event.target.value as SessionStatus | "all")} value={statusFilter}>
            {statuses.map((status) => <option key={status} value={status}>{status === "all" ? "All statuses" : readableStatus(status)}</option>)}
          </select>
        </label>
        <label className={styles.field}>
          Session Type
          <select onChange={(event) => setTypeFilter(event.target.value)} value={typeFilter}>
            <option value="all">All types</option>
            <option value="prepaid">Prepaid</option>
            <option value="open_time">Open Time</option>
            <option value="member_balance">Member Time</option>
          </select>
        </label>
        <label className={styles.field}>
          Date Range
          <select onChange={(event) => setDateRange(event.target.value as DateRange)} value={dateRange}>
            <option value="today">Today</option>
            <option value="yesterday">Yesterday</option>
            <option value="this_week">This Week</option>
            <option value="this_month">This Month</option>
            <option value="all">All dates</option>
          </select>
        </label>
        <button className={styles.tableButton} onClick={clearFilters} type="button">Clear Filters</button>
      </section>

      {message ? <div className={styles.message}>{message}</div> : null}

      <section className={styles.tableWrap}>
        {filteredSessions.length === 0 ? (
          <div className={styles.emptyState}>
            <strong>No sessions found</strong>
            <span>Try changing the date range or clearing your filters.</span>
          </div>
        ) : (
          <>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>PC</th>
                  <th>Customer</th>
                  <th>Session</th>
                  <th>Started</th>
                  <th>Duration</th>
                  <th>Charged</th>
                  <th>Paid</th>
                  <th>Balance</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {pageSessions.map((session) => <HistoryRow key={session.session_id} onDetails={openDetails} session={session} />)}
              </tbody>
            </table>
            <div className={styles.historyCards}>
              {pageSessions.map((session) => (
                <article className={styles.historyCard} key={session.session_id}>
                  <div>
                    <strong>{session.pc_name}</strong>
                    <span className={`${styles.statusBadge} ${statusClassName[session.status]}`}>{readableStatus(session.status)}</span>
                  </div>
                  <p>{customerName(session)} / {sessionType(session)}</p>
                  <p>{readableDuration(session.actual_elapsed_seconds ?? session.total_seconds, session.status === "active")} / {formatMoney(session.total_amount)}</p>
                  <button className={styles.tableButton} onClick={() => void openDetails(session)} type="button">View Details</button>
                </article>
              ))}
            </div>
          </>
        )}
      </section>

      <div className={styles.pagination}>
        <span>{filteredSessions.length} results / page {page} of {totalPages}</span>
        <button className={styles.tableButton} disabled={page === 1} onClick={() => setPage((current) => Math.max(1, current - 1))} type="button">Previous</button>
        <button className={styles.tableButton} disabled={page === totalPages} onClick={() => setPage((current) => Math.min(totalPages, current + 1))} type="button">Next</button>
      </div>

      {selectedSession ? (
        <div className={styles.overlay} role="presentation" onClick={() => setSelectedSession(null)}>
          <section className={styles.dialog} role="dialog" aria-modal="true" aria-label="Session details" onClick={(event) => event.stopPropagation()}>
            <div className={styles.dialogHeader}>
              <div>
                <h2>{selectedSession.pc_name} Session Details</h2>
                <p>{customerName(selectedSession)} / {sessionType(selectedSession)}</p>
              </div>
              <button className={styles.tableButton} onClick={() => setSelectedSession(null)} type="button">Close</button>
            </div>
            <div className={styles.detailsGrid}>
              <section>
                <h3>Session</h3>
                <dl className={styles.billingList}>
                  <div><span>Started</span><strong>{formatDateTime(selectedSession.started_at)}</strong></div>
                  <div><span>Ended</span><strong>{selectedSession.ended_at ? formatDateTime(selectedSession.ended_at) : "In progress"}</strong></div>
                  <div><span>Duration</span><strong>{readableDuration(selectedSession.actual_elapsed_seconds ?? selectedSession.total_seconds, selectedSession.status === "active")}</strong></div>
                  <div><span>Added Time</span><strong>{readableDuration(selectedSession.added_seconds)}</strong></div>
                </dl>
              </section>
              <section>
                <h3>Billing</h3>
                <dl className={styles.billingList}>
                  <div><span>Base</span><strong>{formatMoney(selectedSession.base_amount)}</strong></div>
                  <div><span>Added</span><strong>{formatMoney(selectedSession.additional_amount)}</strong></div>
                  <div><span>Total</span><strong>{formatMoney(selectedSession.total_amount)}</strong></div>
                  <div><span>Paid</span><strong>{formatMoney(selectedSession.total_paid)}</strong></div>
                  <div><span>Balance</span><strong className={Number(selectedSession.balance_due) > 0 ? styles.balanceDue : ""}>{formatMoney(selectedSession.balance_due)}</strong></div>
                </dl>
              </section>
            </div>
            <section>
              <h3>Payments</h3>
              {billing && billing.payments.length > 0 ? (
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
              ) : <div className={styles.emptyState}>No payments recorded.</div>}
            </section>
          </section>
        </div>
      ) : null}
    </>
  );
}

"use client";

import { memo, useEffect, useMemo, useState } from "react";
import { fetchBilling, fetchHistory } from "@/lib/api";
import { formatDateTime, formatMoney, formatNaturalDuration } from "@/lib/time";
import type { BillingSummary, SessionRecord, SessionStatus } from "@/lib/types";
import styles from "@/app/history/history.module.css";
import { CloseButton } from "./CloseButton";
import { SecretSelect, SelectGlyph, type SecretSelectOption } from "./SecretSelect";

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
const dateRangeLabels: Record<DateRange, string> = {
  all: "All dates",
  today: "Today",
  yesterday: "Yesterday",
  this_week: "This Week",
  this_month: "This Month"
};
const sessionTypeOptions: SecretSelectOption[] = [
  { value: "all", label: "All types" },
  { value: "prepaid", label: "Prepaid" },
  { value: "open_time", label: "Open Time" },
  { value: "member_balance", label: "Member Time" }
];

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

const statusFilterOptions: SecretSelectOption[] = statuses.map((status) => ({
  value: status,
  label: status === "all" ? "All statuses" : readableStatus(status)
}));
// Deliberately not derived from the `dateRanges` array above -- that array's
// order only matters for the DateRange type union. This preserves the exact
// option order the native <select> previously rendered.
const dateRangeOptions: SecretSelectOption[] = (["today", "yesterday", "this_week", "this_month", "all"] as const).map(
  (range) => ({ value: range, label: dateRangeLabels[range] })
);

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
        <strong>
          {sessionType(session)}
          {session.station_type === "vip" ? <span className={styles.vipHistoryBadge}>VIP</span> : null}
        </strong>
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
      <section className={`${styles.summaryCards} ${styles.summaryCardsFive}`} aria-label="Filtered history summary">
        <article className={styles.summaryCard}><span>Sessions</span><strong>{filteredSessions.length}</strong></article>
        <article className={styles.summaryCard}><span>Total Usage</span><strong>{readableDuration(summary.seconds)}</strong></article>
        <article className={styles.summaryCard}><span>Total Charged</span><strong>{formatMoney(summary.charged)}</strong></article>
        <article className={styles.summaryCard}><span>Total Paid</span><strong>{formatMoney(summary.paid)}</strong></article>
        <article className={styles.summaryCard}><span>Outstanding</span><strong className={summary.outstanding > 0 ? styles.balanceDue : ""}>{formatMoney(summary.outstanding)}</strong></article>
      </section>

      <section className={styles.filterToolbar} aria-label="History filters">
        <div className={styles.filterFields}>
          <label className={styles.filterSearchField}>
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
          <SecretSelect
            active={statusFilter !== "all"}
            label="Status"
            leadingIcon={<SelectGlyph name="badge" />}
            onChange={(nextValue) => setStatusFilter(nextValue as SessionStatus | "all")}
            options={statusFilterOptions}
            value={statusFilter}
          />
          <SecretSelect
            active={typeFilter !== "all"}
            label="Session Type"
            leadingIcon={<SelectGlyph name="list-filter" />}
            onChange={setTypeFilter}
            options={sessionTypeOptions}
            value={typeFilter}
          />
          <SecretSelect
            active={dateRange !== "today"}
            label="Date Range"
            leadingIcon={<SelectGlyph name="calendar" />}
            onChange={(nextValue) => setDateRange(nextValue as DateRange)}
            options={dateRangeOptions}
            value={dateRange}
          />
        </div>
        <div className={styles.filterActionCell}>
          <span aria-hidden="true" className={styles.filterActionSpacer}>Action</span>
          <div className={styles.filterActionRow}>
            <button className={styles.filterClearButton} onClick={clearFilters} type="button">Clear Filters</button>
          </div>
        </div>
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
                  <p>
                    {customerName(session)} / {sessionType(session)}
                    {session.station_type === "vip" ? <span className={styles.vipHistoryBadge}>VIP</span> : null}
                  </p>
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
        <div className={`${styles.overlay} ${styles.overlayCentered}`} role="presentation" onClick={() => setSelectedSession(null)}>
          <section className={`${styles.dialog} ${styles.sessionDialog}`} role="dialog" aria-modal="true" aria-label="Session details" onClick={(event) => event.stopPropagation()}>
            <div className={styles.dialogHeader}>
              <div>
                <h2>{selectedSession.pc_name} Session Details</h2>
                <p>{customerName(selectedSession)} / {sessionType(selectedSession)}</p>
              </div>
              <CloseButton label="Close session details" onClick={() => setSelectedSession(null)} />
            </div>
            <div className={styles.detailsGrid}>
              <section>
                <h3>Session</h3>
                <dl className={styles.billingList}>
                  <div><span>Started</span><strong>{formatDateTime(selectedSession.started_at)}</strong></div>
                  <div><span>Ended</span><strong>{selectedSession.ended_at ? formatDateTime(selectedSession.ended_at) : "In progress"}</strong></div>
                  <div><span>Duration</span><strong>{readableDuration(selectedSession.actual_elapsed_seconds ?? selectedSession.total_seconds, selectedSession.status === "active")}</strong></div>
                  <div><span>Added Time</span><strong>{readableDuration(selectedSession.added_seconds)}</strong></div>
                  {selectedSession.station_type === "vip" &&
                  selectedSession.vip_seconds_consumed !== null &&
                  selectedSession.standard_seconds_consumed_at_vip_rate !== null ? (
                    <>
                      <div>
                        <span>VIP Time Used</span>
                        <strong>{readableDuration(selectedSession.vip_seconds_consumed)}</strong>
                      </div>
                      <div>
                        <span>Standard Time Used</span>
                        <strong>{readableDuration(selectedSession.standard_seconds_consumed_at_vip_rate)}</strong>
                      </div>
                    </>
                  ) : null}
                </dl>
                {selectedSession.station_type === "vip" && selectedSession.conversion_applied ? (
                  <div className={styles.vipUsageBreakdown}>
                    <p>
                      {formatNaturalDuration(selectedSession.standard_seconds_consumed_at_vip_rate)} Standard provided{" "}
                      {formatNaturalDuration(
                        Math.floor((selectedSession.standard_seconds_consumed_at_vip_rate ?? 0) / 2)
                      )}{" "}
                      of VIP play.
                    </p>
                  </div>
                ) : null}
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

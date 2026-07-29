"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "@/app/history/history.module.css";
import { PageTitleIcon } from "@/components/PageTitleIcon";
import { fetchRemoteCommands } from "@/lib/api";
import { formatDateTime } from "@/lib/time";
import type { RemoteCommandRecord, RemoteCommandStatus, RemoteCommandType } from "@/lib/types";

const pageSize = 25;
const commandTypes: Array<RemoteCommandType | "all"> = [
  "all",
  "wake_on_lan",
  "show_message",
  "shutdown",
  "restart",
  "sleep",
  "show_announcement"
];
const statuses: Array<RemoteCommandStatus | "all"> = [
  "all",
  "pending",
  "received",
  "completed",
  "failed",
  "timed_out",
  "rejected"
];
const dateRanges = ["today", "yesterday", "this_week", "this_month", "custom", "all"] as const;
type DateRange = (typeof dateRanges)[number];

function commandName(type: RemoteCommandType): string {
  const labels: Record<RemoteCommandType, string> = {
    wake_on_lan: "Wake PC",
    show_message: "Send Message",
    shutdown: "Shut Down",
    restart: "Restart",
    sleep: "Sleep",
    show_announcement: "Announcement"
  };
  return labels[type];
}

function statusName(status: RemoteCommandStatus): string {
  const labels: Record<RemoteCommandStatus, string> = {
    pending: "Pending",
    received: "Received",
    completed: "Completed",
    failed: "Failed",
    timed_out: "Expired",
    rejected: "Cancelled"
  };
  return labels[status];
}

function statusClass(status: RemoteCommandStatus): string {
  if (status === "completed" || status === "received") {
    return styles.statusCompleted;
  }
  if (status === "failed" || status === "rejected" || status === "timed_out") {
    return styles.statusFailed;
  }
  if (status === "pending") {
    return styles.statusWarning;
  }
  return styles.statusActive;
}

function commandResult(command: RemoteCommandRecord): string {
  if (command.status === "failed" || command.status === "rejected" || command.status === "timed_out") {
    return command.failure_reason ? "Command failed" : "Client did not respond";
  }
  const response = command.client_response;
  if (response === "packet_sent") {
    return "Wake packet sent";
  }
  if (response === "displayed") {
    return "Message displayed";
  }
  if (response === "accepted") {
    return "Command accepted";
  }
  if (command.status === "completed") {
    return "Completed";
  }
  if (command.status === "received") {
    return "Client received";
  }
  return "Not completed";
}

function operatorName(command: RemoteCommandRecord): string {
  return command.requested_by_display_name ?? command.requested_by_user_id ?? "System";
}

function safeTime(value: string | null): string {
  return value ? formatDateTime(value) : "-";
}

function inDateRange(command: RemoteCommandRecord, range: DateRange): boolean {
  if (range === "all" || range === "custom") {
    return true;
  }
  const requested = new Date(command.requested_at);
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
  return requested >= start && requested < end;
}

export default function RemoteCommandsPage() {
  const [commands, setCommands] = useState<RemoteCommandRecord[]>([]);
  const [message, setMessage] = useState<string | null>("Loading remote command history...");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<RemoteCommandType | "all">("all");
  const [statusFilter, setStatusFilter] = useState<RemoteCommandStatus | "all">("all");
  const [dateRange, setDateRange] = useState<DateRange>("today");
  const [page, setPage] = useState(1);
  const [selectedCommand, setSelectedCommand] = useState<RemoteCommandRecord | null>(null);

  useEffect(() => {
    fetchRemoteCommands()
      .then((records) => {
        setCommands(records);
        setMessage(null);
      })
      .catch((error) => {
        setMessage(error instanceof Error ? error.message : "Could not load remote command history.");
      });
  }, []);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => setDebouncedSearch(search), 220);
    return () => window.clearTimeout(timeoutId);
  }, [search]);

  const filteredCommands = useMemo(() => {
    const query = debouncedSearch.trim().toLowerCase();
    return commands.filter((command) => {
      const payload = command.payload_json ?? "";
      const matchesSearch =
        !query ||
        [commandName(command.command_type), command.pc_name, operatorName(command), command.client_response ?? "", command.force_reason ?? "", payload]
          .join(" ")
          .toLowerCase()
          .includes(query);
      const matchesType = typeFilter === "all" || command.command_type === typeFilter;
      const matchesStatus = statusFilter === "all" || command.status === statusFilter;
      return matchesSearch && matchesType && matchesStatus && inDateRange(command, dateRange);
    });
  }, [commands, dateRange, debouncedSearch, statusFilter, typeFilter]);

  const summary = useMemo(
    () => ({
      total: filteredCommands.length,
      completed: filteredCommands.filter((command) => command.status === "completed").length,
      failed: filteredCommands.filter((command) => ["failed", "timed_out", "rejected"].includes(command.status)).length,
      pending: filteredCommands.filter((command) => ["pending", "received"].includes(command.status)).length,
      forced: filteredCommands.filter((command) => command.forced).length
    }),
    [filteredCommands]
  );
  const totalPages = Math.max(1, Math.ceil(filteredCommands.length / pageSize));
  const pageCommands = filteredCommands.slice((page - 1) * pageSize, page * pageSize);

  function clearFilters() {
    setSearch("");
    setTypeFilter("all");
    setStatusFilter("all");
    setDateRange("today");
    setPage(1);
  }

  return (
    <main className={styles.page}>
      <header className={styles.topBar}>
        <div className="pageTitleLockup">
          <PageTitleIcon name="terminal" />
          <div>
            <h1>Remote Commands</h1>
            <p>Review power, Wake-on-LAN, message, and announcement commands sent to shop PCs.</p>
          </div>
        </div>
      </header>

      <section className={styles.summaryStrip} aria-label="Remote command summary">
        <div><span>Total Commands</span><strong>{summary.total}</strong></div>
        <div><span>Completed</span><strong>{summary.completed}</strong></div>
        <div><span>Failed</span><strong>{summary.failed}</strong></div>
        <div><span>Pending</span><strong>{summary.pending}</strong></div>
        <div><span>Forced</span><strong>{summary.forced}</strong></div>
      </section>

      <section className={styles.filters} aria-label="Remote command filters">
        <label className={styles.field}>
          Search
          <input
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
            placeholder="PC, operator, command, or message"
            type="search"
            value={search}
          />
        </label>
        <label className={styles.field}>
          Command Type
          <select onChange={(event) => setTypeFilter(event.target.value as RemoteCommandType | "all")} value={typeFilter}>
            {commandTypes.map((type) => (
              <option key={type} value={type}>{type === "all" ? "All commands" : commandName(type)}</option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          Status
          <select onChange={(event) => setStatusFilter(event.target.value as RemoteCommandStatus | "all")} value={statusFilter}>
            {statuses.map((status) => (
              <option key={status} value={status}>{status === "all" ? "All statuses" : statusName(status)}</option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          Date Range
          <select onChange={(event) => setDateRange(event.target.value as DateRange)} value={dateRange}>
            <option value="today">Today</option>
            <option value="yesterday">Yesterday</option>
            <option value="this_week">This Week</option>
            <option value="this_month">This Month</option>
            <option value="custom">Custom</option>
            <option value="all">All dates</option>
          </select>
        </label>
        <button className={styles.tableButton} onClick={clearFilters} type="button">Clear Filters</button>
      </section>

      {message ? <div className={styles.message}>{message}</div> : null}
      <section className={styles.tableWrap}>
        {filteredCommands.length === 0 ? (
          <div className={styles.emptyState}>
            <strong>No remote commands found</strong>
            <span>Try another date range, command type, or status.</span>
          </div>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Command</th>
                <th>PC</th>
                <th>Operator</th>
                <th>Requested</th>
                <th>Status</th>
                <th>Result</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {pageCommands.map((command) => (
                <tr key={command.command_id}>
                  <td><strong>{commandName(command.command_type)}</strong></td>
                  <td>{command.pc_name}</td>
                  <td>{operatorName(command)}</td>
                  <td>{formatDateTime(command.requested_at)}</td>
                  <td><span className={`${styles.statusBadge} ${statusClass(command.status)}`}>{statusName(command.status)}</span></td>
                  <td>{commandResult(command)}</td>
                  <td><button className={styles.tableButton} onClick={() => setSelectedCommand(command)} type="button">View Details</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <div className={styles.pagination}>
        <span>{filteredCommands.length} results / page {page} of {totalPages}</span>
        <button className={styles.tableButton} disabled={page === 1} onClick={() => setPage((current) => Math.max(1, current - 1))} type="button">Previous</button>
        <button className={styles.tableButton} disabled={page === totalPages} onClick={() => setPage((current) => Math.min(totalPages, current + 1))} type="button">Next</button>
      </div>

      {selectedCommand ? (
        <div className={styles.overlay} role="presentation" onClick={() => setSelectedCommand(null)}>
          <section className={styles.dialog} role="dialog" aria-modal="true" aria-label="Command details" onClick={(event) => event.stopPropagation()}>
            <div className={styles.dialogHeader}>
              <div>
                <h2>{commandName(selectedCommand.command_type)}</h2>
                <p>{selectedCommand.pc_name} / {operatorName(selectedCommand)}</p>
              </div>
              <button className={styles.tableButton} onClick={() => setSelectedCommand(null)} type="button">Close</button>
            </div>
            <div className={styles.detailsGrid}>
              <section>
                <h3>Command</h3>
                <dl className={styles.billingList}>
                  <div><span>Status</span><strong>{statusName(selectedCommand.status)}</strong></div>
                  <div><span>Requested</span><strong>{formatDateTime(selectedCommand.requested_at)}</strong></div>
                  <div><span>Forced</span><strong>{selectedCommand.forced ? "Yes" : "No"}</strong></div>
                  <div><span>Reason</span><strong>{selectedCommand.force_reason ?? "Not applicable"}</strong></div>
                </dl>
              </section>
              <section>
                <h3>Execution</h3>
                <dl className={styles.billingList}>
                  <div><span>Received</span><strong>{safeTime(selectedCommand.received_at)}</strong></div>
                  <div><span>Completed</span><strong>{safeTime(selectedCommand.completed_at)}</strong></div>
                  <div><span>Failed</span><strong>{safeTime(selectedCommand.failed_at)}</strong></div>
                  <div><span>Acknowledgement</span><strong>{selectedCommand.client_response ? commandResult(selectedCommand) : "Not received"}</strong></div>
                  <div><span>Failure reason</span><strong>{selectedCommand.status === "failed" ? selectedCommand.failure_reason ?? "Command failed" : "Not applicable"}</strong></div>
                </dl>
              </section>
            </div>
            <section>
              <h3>Context</h3>
              <dl className={styles.billingList}>
                <div><span>Message</span><strong>{selectedCommand.payload_json ?? "Not applicable"}</strong></div>
                <div><span>Operator note</span><strong>{selectedCommand.force_reason ?? "Not applicable"}</strong></div>
              </dl>
            </section>
            <details>
              <summary>Technical details</summary>
              <dl className={styles.billingList}>
                <div><span>Raw type</span><strong>{selectedCommand.command_type}</strong></div>
                <div><span>Command ID</span><strong>{selectedCommand.command_id}</strong></div>
              </dl>
            </details>
          </section>
        </div>
      ) : null}
    </main>
  );
}

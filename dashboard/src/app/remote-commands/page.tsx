"use client";

import { useEffect, useState } from "react";
import styles from "@/app/history/history.module.css";
import { fetchRemoteCommands } from "@/lib/api";
import { formatDateTime } from "@/lib/time";
import type { RemoteCommandRecord } from "@/lib/types";

export default function RemoteCommandsPage() {
  const [commands, setCommands] = useState<RemoteCommandRecord[]>([]);
  const [message, setMessage] = useState<string | null>("Loading remote command history...");

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

  return (
    <main className={styles.page}>
      <header className={styles.topBar}>
        <div>
          <h1>Remote Commands</h1>
          <p>Audit trail for power controls, Wake-on-LAN, messages, and announcements.</p>
        </div>
      </header>
      {message ? <div className={styles.message}>{message}</div> : null}
      <section className={styles.tableWrap}>
        {commands.length === 0 ? (
          <div className={styles.emptyState}>No remote commands yet.</div>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Command</th>
                <th>PC</th>
                <th>Actor</th>
                <th>Requested</th>
                <th>Status</th>
                <th>Received</th>
                <th>Completed/Failed</th>
                <th>Failure</th>
                <th>Forced</th>
              </tr>
            </thead>
            <tbody>
              {commands.map((command) => (
                <tr key={command.command_id}>
                  <td>{command.command_type}</td>
                  <td>
                    <strong>{command.pc_name}</strong>
                  </td>
                  <td>{command.requested_by_display_name ?? command.requested_by_user_id ?? "System"}</td>
                  <td>{formatDateTime(command.requested_at)}</td>
                  <td>
                    <span className={`${styles.statusBadge} ${statusClass(command.status)}`}>
                      {command.status}
                    </span>
                  </td>
                  <td>{formatDateTime(command.received_at)}</td>
                  <td>{formatDateTime(command.completed_at ?? command.failed_at)}</td>
                  <td>{command.failure_reason ?? command.client_response ?? "None"}</td>
                  <td>{command.forced ? command.force_reason ?? "Forced" : "No"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}

function statusClass(status: RemoteCommandRecord["status"]): string {
  if (status === "completed" || status === "received") {
    return styles.statusCompleted;
  }
  if (status === "failed" || status === "rejected" || status === "timed_out") {
    return styles.statusFailed;
  }
  return styles.statusActive;
}

"use client";

import { useEffect, useMemo, useState } from "react";
import {
  createSystemBackup,
  deleteSystemBackup,
  fetchSystemBackups,
  fetchSystemClients,
  fetchSystemDatabase,
  fetchSystemHealth,
  fetchSystemInfo,
  fetchSystemLogs,
  fetchSystemStorage,
  getSystemBackupDownloadUrl,
  restoreSystemBackup
} from "@/lib/api";
import { formatDateTime, formatDuration } from "@/lib/time";
import type {
  SystemBackupRecord,
  SystemClientHealth,
  SystemDatabaseHealth,
  SystemHealth,
  SystemInfo,
  SystemLogEntry,
  SystemStatus,
  SystemStorage
} from "@/lib/types";
import styles from "@/app/system/system.module.css";
import { ActivityDrawerTrigger, ActivityFeed } from "./ActivityFeed";
import { StatusCard } from "./StatusCard";
import { useToast } from "./ToastProvider";

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function StatusBadge({ status }: { status: SystemStatus | string }) {
  const normalized = ["Healthy", "Warning", "Offline", "Error"].includes(status) ? status : "Warning";
  return <span className={`${styles.badge} ${styles[`badge${normalized}`]}`}>{status}</span>;
}

function humanizeEvent(value: string): string {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function cleanLogMessage(message: string): string {
  return message
    .replace(/operator_admin/g, "Admin")
    .replace(/operator_rogene/g, "Rogene")
    .replace(/operator_mama/g, "Mama")
    .replace(/operator_neil/g, "Neil")
    .replace(/operator_tyrone/g, "Tyrone")
    .replace(/operator_rojan/g, "Rojan")
    .replace(/operator_bel/g, "Bel")
    .replace(/operator_karen/g, "Karen")
    .replace(/_/g, " ");
}

function clientSessionState(client: SystemClientHealth): string {
  if (!client.registered) {
    return "Not installed";
  }
  if (client.current_state === "disconnected") {
    return "No active session";
  }
  if (!client.connected && client.current_state !== "disconnected") {
    return `Last known: ${humanizeEvent(client.current_state)}`;
  }
  return humanizeEvent(client.current_state);
}

function Metric({
  label,
  value,
  status
}: {
  label: string;
  value: string | number;
  status?: SystemStatus | string;
}) {
  return (
    <div className={styles.metric}>
      <span>{label}</span>
      <strong>{value}</strong>
      {status ? <StatusBadge status={status} /> : null}
    </div>
  );
}

export function SystemManager() {
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [database, setDatabase] = useState<SystemDatabaseHealth | null>(null);
  const [backups, setBackups] = useState<SystemBackupRecord[]>([]);
  const [storage, setStorage] = useState<SystemStorage | null>(null);
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [logs, setLogs] = useState<SystemLogEntry[]>([]);
  const [clients, setClients] = useState<SystemClientHealth[]>([]);
  const [message, setMessage] = useState<{ kind: "success" | "error" | "info"; text: string } | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isWorking, setIsWorking] = useState(false);
  const { pushToast } = useToast();

  async function loadSystem() {
    setIsLoading(true);
    try {
      const [nextHealth, nextDatabase, nextBackups, nextStorage, nextInfo, nextLogs, nextClients] =
        await Promise.all([
          fetchSystemHealth(),
          fetchSystemDatabase(),
          fetchSystemBackups(),
          fetchSystemStorage(),
          fetchSystemInfo(),
          fetchSystemLogs(),
          fetchSystemClients()
        ]);
      setHealth(nextHealth);
      setDatabase(nextDatabase);
      setBackups(nextBackups);
      setStorage(nextStorage);
      setInfo(nextInfo);
      setLogs(nextLogs);
      setClients(nextClients);
    } catch (error) {
      setMessage({ kind: "error", text: error instanceof Error ? error.message : "Could not load system health." });
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void loadSystem();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, []);

  const logText = useMemo(
    () => logs.map((entry) => `[${entry.timestamp}] ${entry.level}: ${entry.message}`).join("\n"),
    [logs]
  );

  async function runAction(action: () => Promise<{ message: string }>, fallback: string) {
    if (isWorking) {
      return;
    }
    setIsWorking(true);
    setMessage(null);
    try {
      const result = await action();
      setMessage({ kind: "success", text: result.message || fallback });
      pushToast(result.message || fallback, "success");
      await loadSystem();
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : fallback;
      setMessage({ kind: "error", text: errorMessage });
      pushToast(errorMessage, "error");
    } finally {
      setIsWorking(false);
    }
  }

  async function handleCreateBackup() {
    await runAction(createSystemBackup, "Backup created.");
  }

  async function handleRestore(filename: string) {
    if (!window.confirm("Restoring will replace the current database. Continue?")) {
      return;
    }
    await runAction(() => restoreSystemBackup(filename), "Restore completed.");
  }

  async function handleDelete(filename: string) {
    if (!window.confirm(`Delete backup ${filename}?`)) {
      return;
    }
    await runAction(() => deleteSystemBackup(filename), "Backup deleted.");
  }

  async function handleCopyLogs() {
    await navigator.clipboard.writeText(logText);
    setMessage({ kind: "info", text: "Recent logs copied." });
    pushToast("Recent logs copied.", "info");
  }

  function handleDownloadLogs() {
    const blob = new Blob([logText], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `secretgrounds-system-logs-${new Date().toISOString().slice(0, 10)}.txt`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  if (isLoading && health === null) {
    return <section className={styles.panel}>Loading system health...</section>;
  }

  return (
    <div className={styles.layout}>
      {message ? (
        <div className={`${styles.message} ${styles[`${message.kind}Message`]}`} role={message.kind === "error" ? "alert" : "status"}>
          {message.text}
        </div>
      ) : null}

      <section className={styles.toolbar}>
        <div>
          <strong>Operations Snapshot</strong>
          <span>Manual backup default: daily at 2:00 AM with 30 verified backups retained.</span>
        </div>
        <div className={styles.toolbarActions}>
          <button className={styles.secondaryButton} disabled={isLoading || isWorking} onClick={loadSystem} type="button">
            Refresh
          </button>
          <button className={styles.primaryButton} disabled={isWorking} onClick={handleCreateBackup} type="button">
            {isWorking ? "Working..." : "Create Backup"}
          </button>
        </div>
      </section>

      {health ? (
        <section className={styles.metricsGrid} aria-label="System health">
          <StatusCard detail={formatDuration(health.system_uptime_seconds)} icon="SRV" title="Server" tone={health.server_status === "Healthy" ? "success" : "warning"} value={health.server_status} />
          <StatusCard detail={formatBytes(health.database_size_bytes)} icon="DB" title="Database" tone={health.database_status === "Healthy" ? "success" : "danger"} value={health.database_status} />
          <StatusCard detail={`${health.connected_clients} connected`} icon="WS" title="WebSocket" tone={health.websocket_status === "Healthy" ? "success" : "warning"} value={health.websocket_status} />
          <StatusCard detail={`${health.offline_clients} offline`} icon="PC" title="Clients" tone={health.offline_clients > 0 ? "warning" : "success"} value={String(health.connected_clients)} />
          <StatusCard detail="Current release" icon="VER" title="Version" value={health.current_version} />
          <StatusCard detail={health.last_backup ? formatDateTime(health.last_backup.created_at) : "No backup yet"} icon="BKP" title="Last Backup" tone={health.last_backup ? "success" : "warning"} value={health.last_backup ? "Verified" : "None"} />
        </section>
      ) : null}

      <div className={styles.systemActivity}>
        <ActivityFeed compact limit={10} title="System Activity" />
        <div className={styles.systemActivityAction}>
          <ActivityDrawerTrigger label="View all activity" />
        </div>
      </div>

      <div className={styles.twoColumn}>
        {database ? (
          <section className={styles.panel}>
            <h2>Database Health</h2>
            <div className={styles.detailGrid}>
              <Metric label="SQLite File Size" value={formatBytes(database.file_size_bytes)} />
              <Metric label="Last Modified" value={database.last_modified_at ? formatDateTime(database.last_modified_at) : "Unknown"} />
              <Metric label="Integrity Check" status={database.status} value={database.integrity} />
              <Metric label="Schema Version" value={database.database_version === 0 ? "Not versioned" : database.database_version} />
            </div>
            <div className={styles.tableWrap}>
              <table>
                <thead>
                  <tr>
                    <th>Core table</th>
                    <th>Estimated rows</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(database.estimated_row_counts).map(([table, count]) => (
                    <tr key={table}>
                      <td>{table}</td>
                      <td>{count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ) : null}

        {storage ? (
          <section className={styles.panel}>
            <h2>Storage</h2>
            <div className={styles.detailGrid}>
              <Metric label="Available Disk Space" status={storage.status} value={formatBytes(storage.available_bytes)} />
              <Metric label="Backup Folder Size" value={formatBytes(storage.backup_folder_size_bytes)} />
              <Metric label="Largest Backup" value={storage.largest_backup ? formatBytes(storage.largest_backup.size_bytes) : "None"} />
              <Metric label="Write Permission" status={storage.writable ? "Healthy" : "Warning"} value={storage.writable ? "Writable" : "Check folder"} />
            </div>
            <p className={styles.note}>{storage.backup_folder}</p>
          </section>
        ) : null}
      </div>

      <section className={styles.panel}>
        <h2>Backup History</h2>
        {backups.length > 0 ? (
          <div className={styles.tableWrap}>
            <table>
              <thead>
                <tr>
                  <th>Filename</th>
                  <th>Created</th>
                  <th>Size</th>
                  <th>Age</th>
                  <th>Verified</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {backups.map((backup) => (
                  <tr key={backup.filename}>
                    <td>{backup.filename}</td>
                    <td>{formatDateTime(backup.created_at)}</td>
                    <td>{formatBytes(backup.size_bytes)}</td>
                    <td>{formatDuration(backup.age_seconds)}</td>
                    <td><StatusBadge status={backup.verified ? "Healthy" : "Warning"} /></td>
                    <td className={styles.actionsCell}>
                      <a className={styles.linkButton} href={getSystemBackupDownloadUrl(backup.filename)}>Download</a>
                      <button className={styles.secondaryButton} disabled={isWorking || !backup.verified} onClick={() => handleRestore(backup.filename)} type="button">
                        Restore
                      </button>
                      <button className={styles.dangerButton} disabled={isWorking} onClick={() => handleDelete(backup.filename)} type="button">
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className={styles.emptyBackupState}>
            <strong>No backups yet</strong>
            <span>Create your first verified database backup to protect shop data.</span>
            <button className={styles.primaryButton} disabled={isWorking} onClick={handleCreateBackup} type="button">
              Create Backup
            </button>
          </div>
        )}
      </section>

      <div className={styles.twoColumn}>
        {info ? (
          <section className={styles.panel}>
            <h2>System Information</h2>
            <div className={styles.detailGrid}>
              <Metric label="Secret Grounds Version" value={info.secret_grounds_version} />
              <Metric label="Python Version" value={info.python_version} />
              <Metric label="Operating System" value={info.operating_system} />
              <Metric label="Schema Version" value={info.database_version === 0 ? "Not versioned" : info.database_version} />
              <Metric label="Server Start Time" value={formatDateTime(info.server_start_time)} />
              <Metric label="Application Mode" status={info.application_mode === "TEST MODE" ? "Warning" : "Healthy"} value={info.application_mode} />
            </div>
          </section>
        ) : null}

        <section className={styles.panel}>
          <h2>Client Health</h2>
          <div className={styles.tableWrap}>
            <table>
              <thead>
                <tr>
                  <th>PC</th>
                  <th>Connection</th>
                  <th>Last Seen</th>
                  <th>Client Version</th>
                  <th>Session State</th>
                </tr>
              </thead>
              <tbody>
                {clients.map((client) => (
                  <tr key={client.pc_name}>
                    <td>{client.display_name || client.pc_name}</td>
                    <td><StatusBadge status={client.connected ? "Healthy" : "Offline"} /></td>
                    <td>{client.last_heartbeat ? formatDateTime(client.last_heartbeat) : "Never connected"}</td>
                    <td>{client.version ?? "Not reported"}</td>
                    <td>{clientSessionState(client)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <section className={`${styles.panel} ${styles.logsPanel}`}>
        <details>
          <summary className={styles.panelTitleRow}>
            <h2>Recent Logs</h2>
            <span>Compact diagnostics</span>
          </summary>
          <div className={styles.toolbarActions}>
            <button className={styles.secondaryButton} disabled={!logText} onClick={handleCopyLogs} type="button">Copy</button>
            <button className={styles.secondaryButton} disabled={!logText} onClick={handleDownloadLogs} type="button">Download</button>
          </div>
          <div className={styles.logList}>
            {logs.map((entry) => (
              <div className={styles.logEntry} key={`${entry.timestamp}-${entry.event}`}>
                <StatusBadge status={entry.level === "Error" ? "Error" : entry.level === "Warning" ? "Warning" : "Healthy"} />
                <strong>{humanizeEvent(entry.event)}</strong>
                <span>{cleanLogMessage(entry.message)}</span>
                <time>{formatDateTime(entry.timestamp)}</time>
              </div>
            ))}
            {logs.length === 0 ? <p className={styles.note}>No recent system events.</p> : null}
          </div>
        </details>
      </section>
    </div>
  );
}

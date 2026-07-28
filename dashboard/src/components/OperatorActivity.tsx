"use client";

import { memo, useEffect, useMemo, useState } from "react";
import { fetchAdminOperators, fetchOperatorActivity } from "@/lib/api";
import { formatDateTime, formatMoney } from "@/lib/time";
import type { OperatorActivityRecord, OperatorManagementRecord } from "@/lib/types";
import styles from "@/app/history/history.module.css";

const pageSize = 25;
const categories = [
  ["", "Important Actions"],
  ["session", "Sessions"],
  ["payment", "Payments"],
  ["remote", "Remote Controls"],
  ["operator", "Operator Changes"],
  ["recovery", "Recovery"]
] as const;

function humanizeAction(action: string): string {
  return action
    .replace(/_/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function activityContext(item: OperatorActivityRecord): string {
  if (item.category === "operator") {
    return item.customer ? `${item.customer} selected` : "Operator selected";
  }
  if (item.category === "payment") {
    return [item.pc_name, item.customer].filter(Boolean).join(" / ") || "Payment";
  }
  if (item.category === "remote") {
    return item.pc_name ? `${item.pc_name} / Remote command` : "Remote command";
  }
  if (item.category === "recovery") {
    return "Database backup";
  }
  if (item.pc_name || item.customer) {
    return [item.pc_name, item.customer].filter(Boolean).join(" / ");
  }
  return "General";
}

function activityAmount(item: OperatorActivityRecord): string {
  return item.amount ? `${formatMoney(item.amount)} ${item.payment_method ?? ""}`.trim() : "-";
}

function activityResult(item: OperatorActivityRecord): string {
  const result = item.result.toLowerCase();
  if (item.category === "operator") {
    return "Operator selected";
  }
  if (item.category === "payment") {
    return "Payment collected";
  }
  if (item.action.toLowerCase().includes("session") && result === "active") {
    return "Active session";
  }
  if (result.includes("fail") || result.includes("error")) {
    return "Failed";
  }
  if (result.includes("applied")) {
    return "Setting applied";
  }
  return result ? humanizeAction(result) : "Completed";
}

const ActivityRow = memo(function ActivityRow({
  item,
  onDetails
}: {
  item: OperatorActivityRecord;
  onDetails: (item: OperatorActivityRecord) => void;
}) {
  return (
    <tr>
      <td>{formatDateTime(item.timestamp)}</td>
      <td>{item.operator_name}</td>
      <td><strong>{humanizeAction(item.action)}</strong></td>
      <td>{activityContext(item)}</td>
      <td>{activityAmount(item)}</td>
      <td>{activityResult(item)}</td>
      <td><button className={styles.tableButton} onClick={() => onDetails(item)} type="button">View Details</button></td>
    </tr>
  );
});

export function OperatorActivity() {
  const [activity, setActivity] = useState<OperatorActivityRecord[]>([]);
  const [operators, setOperators] = useState<OperatorManagementRecord[]>([]);
  const [dateScope, setDateScope] = useState<"today" | "all">("today");
  const [operatorId, setOperatorId] = useState("");
  const [category, setCategory] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [selectedActivity, setSelectedActivity] = useState<OperatorActivityRecord | null>(null);

  useEffect(() => {
    let isMounted = true;
    fetchAdminOperators()
      .then((records) => {
        if (isMounted) {
          setOperators(records);
        }
      })
      .catch(() => {
        if (isMounted) {
          setOperators([]);
        }
      });
    return () => {
      isMounted = false;
    };
  }, []);

  useEffect(() => {
    let isMounted = true;
    fetchOperatorActivity({
      dateScope,
      operatorId: operatorId || undefined,
      category: category || undefined
    })
      .then((records) => {
        if (!isMounted) {
          return;
        }
        setActivity(records);
        setErrorMessage(null);
        setPage(1);
      })
      .catch((error) => {
        if (isMounted) {
          setErrorMessage(error instanceof Error ? error.message : "Could not load activity.");
        }
      })
      .finally(() => {
        if (isMounted) {
          setIsLoading(false);
        }
      });
    return () => {
      isMounted = false;
    };
  }, [category, dateScope, operatorId]);

  const operatorOptions = useMemo(
    () => operators.filter((operator) => operator.is_active),
    [operators]
  );
  const summary = useMemo(() => {
    const activeOperators = new Set(activity.map((item) => item.operator_id ?? item.operator_name));
    return {
      events: activity.length,
      operatorsActive: activeOperators.size,
      payments: activity.filter((item) => item.category === "payment").length,
      alerts: activity.filter((item) => item.result.toLowerCase().includes("fail") || item.category === "recovery").length
    };
  }, [activity]);
  const totalPages = Math.max(1, Math.ceil(activity.length / pageSize));
  const pageActivity = activity.slice((page - 1) * pageSize, page * pageSize);

  function clearFilters() {
    setDateScope("today");
    setOperatorId("");
    setCategory("");
    setPage(1);
  }

  return (
    <section className={styles.activityPanel}>
      <section className={styles.summaryStrip} aria-label="Operator activity summary">
        <div><span>Events</span><strong>{summary.events}</strong></div>
        <div><span>Operators Active</span><strong>{summary.operatorsActive}</strong></div>
        <div><span>Payments Processed</span><strong>{summary.payments}</strong></div>
        <div><span>Important Alerts</span><strong>{summary.alerts}</strong></div>
      </section>

      <div className={styles.filters} aria-label="Operator activity filters">
        <label className={styles.field}>
          Date
          <select onChange={(event) => setDateScope(event.target.value as "today" | "all")} value={dateScope}>
            <option value="today">Today</option>
            <option value="all">All Dates</option>
          </select>
        </label>
        <label className={styles.field}>
          Operator
          <select onChange={(event) => setOperatorId(event.target.value)} value={operatorId}>
            <option value="">All Operators</option>
            {operatorOptions.map((operator) => (
              <option key={operator.operator_id} value={operator.operator_id}>
                {operator.display_name}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          Action
          <select onChange={(event) => setCategory(event.target.value)} value={category}>
            {categories.map(([value, label]) => (
              <option key={label} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <button className={styles.tableButton} onClick={clearFilters} type="button">Clear</button>
      </div>

      {errorMessage ? <div className={styles.message}>{errorMessage}</div> : null}

      {isLoading ? (
        <div className={styles.activityLoading} aria-live="polite">
          Loading operator activity...
        </div>
      ) : activity.length === 0 && errorMessage === null ? (
        <div className={styles.emptyState}>
          <strong>No operator activity found</strong>
          <span>Try another date range or action filter.</span>
        </div>
      ) : activity.length > 0 ? (
        <>
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Operator</th>
                  <th>Action</th>
                  <th>Context</th>
                  <th>Amount</th>
                  <th>Result</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {pageActivity.map((item, index) => (
                  <ActivityRow item={item} key={`${item.timestamp}-${item.action}-${index}`} onDetails={setSelectedActivity} />
                ))}
              </tbody>
            </table>
          </div>
          <div className={styles.pagination}>
            <span>{activity.length} results / page {page} of {totalPages}</span>
            <button className={styles.tableButton} disabled={page === 1} onClick={() => setPage((current) => Math.max(1, current - 1))} type="button">Previous</button>
            <button className={styles.tableButton} disabled={page === totalPages} onClick={() => setPage((current) => Math.min(totalPages, current + 1))} type="button">Next</button>
          </div>
        </>
      ) : null}

      {selectedActivity ? (
        <div className={styles.overlay} role="presentation" onClick={() => setSelectedActivity(null)}>
          <section className={styles.dialog} role="dialog" aria-modal="true" aria-label="Operator activity details" onClick={(event) => event.stopPropagation()}>
            <div className={styles.dialogHeader}>
              <div>
                <h2>{humanizeAction(selectedActivity.action)}</h2>
                <p>{selectedActivity.operator_name} / {formatDateTime(selectedActivity.timestamp)}</p>
              </div>
              <button className={styles.tableButton} onClick={() => setSelectedActivity(null)} type="button">Close</button>
            </div>
            <dl className={styles.billingList}>
              <div><span>Context</span><strong>{activityContext(selectedActivity)}</strong></div>
              <div><span>Amount</span><strong>{activityAmount(selectedActivity)}</strong></div>
              <div><span>Result</span><strong>{activityResult(selectedActivity)}</strong></div>
              <div><span>Category</span><strong>{humanizeAction(selectedActivity.category)}</strong></div>
            </dl>
          </section>
        </div>
      ) : null}
    </section>
  );
}

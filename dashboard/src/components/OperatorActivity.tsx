"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchAdminOperators, fetchOperatorActivity } from "@/lib/api";
import { formatDateTime, formatMoney } from "@/lib/time";
import type { OperatorActivityRecord, OperatorManagementRecord } from "@/lib/types";
import styles from "@/app/history/history.module.css";

const categories = [
  ["", "Important Actions"],
  ["session", "Sessions"],
  ["payment", "Payments"],
  ["remote", "Remote Controls"],
  ["operator", "Operator Changes"],
  ["recovery", "Recovery"]
] as const;

export function OperatorActivity() {
  const [activity, setActivity] = useState<OperatorActivityRecord[]>([]);
  const [operators, setOperators] = useState<OperatorManagementRecord[]>([]);
  const [dateScope, setDateScope] = useState<"today" | "all">("today");
  const [operatorId, setOperatorId] = useState("");
  const [category, setCategory] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

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

  return (
    <section className={styles.activityPanel}>
      <div className={styles.compactFilters} aria-label="Operator activity filters">
        <label className={styles.filterChip}>
          <span>Date</span>
          <select onChange={(event) => setDateScope(event.target.value as "today" | "all")} value={dateScope}>
            <option value="today">Today</option>
            <option value="all">All Dates</option>
          </select>
        </label>
        <label className={styles.filterChip}>
          <span>Operator</span>
          <select onChange={(event) => setOperatorId(event.target.value)} value={operatorId}>
            <option value="">All Operators</option>
            {operatorOptions.map((operator) => (
              <option key={operator.operator_id} value={operator.operator_id}>
                {operator.display_name}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.filterChip}>
          <span>Action</span>
          <select onChange={(event) => setCategory(event.target.value)} value={category}>
            {categories.map(([value, label]) => (
              <option key={label} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {errorMessage ? <div className={styles.message}>{errorMessage}</div> : null}

      {isLoading ? (
        <div className={styles.activityLoading} aria-live="polite">
          Loading operator activity...
        </div>
      ) : activity.length === 0 && errorMessage === null ? (
        <div className={styles.activityEmpty}>
          <span className={styles.activityEmptyIcon} aria-hidden="true" />
          <h2>No activity yet today.</h2>
          <p>Important cashier and admin actions will appear here.</p>
          <ul>
            <li>Session started</li>
            <li>Payment collected</li>
            <li>Time extended</li>
            <li>Member created</li>
            <li>Remote command sent</li>
          </ul>
        </div>
      ) : activity.length > 0 ? (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Time</th>
                <th>Operator</th>
                <th>Action</th>
                <th>PC / Customer</th>
                <th>Amount</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {activity.map((item, index) => (
                <tr key={`${item.timestamp}-${item.action}-${index}`}>
                  <td>{formatDateTime(item.timestamp)}</td>
                  <td>{item.operator_name}</td>
                  <td>{item.action}</td>
                  <td>
                    {[item.pc_name, item.customer].filter(Boolean).join(" - ") || "General"}
                  </td>
                  <td>
                    {item.amount ? `${formatMoney(item.amount)} ${item.payment_method ?? ""}`.trim() : "None"}
                  </td>
                  <td>{item.result}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

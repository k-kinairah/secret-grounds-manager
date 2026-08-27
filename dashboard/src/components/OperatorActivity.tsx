"use client";

import { memo, useEffect, useMemo, useState } from "react";
import { fetchAdminOperators, fetchOperatorActivity } from "@/lib/api";
import { formatDateTime, formatMoney } from "@/lib/time";
import type { OperatorActivityRecord, OperatorManagementRecord } from "@/lib/types";
import styles from "@/app/history/history.module.css";
import { CloseButton } from "./CloseButton";
import { SecretSelect, SelectGlyph, type SecretSelectOption } from "./SecretSelect";

const pageSize = 25;
const categories = [
  ["", "Important Actions"],
  ["session", "Sessions"],
  ["payment", "Payments"],
  ["remote", "Remote Controls"],
  ["operator", "Operator Changes"],
  ["recovery", "Recovery"]
] as const;
const categoryOptions: SecretSelectOption[] = categories.map(([value, label]) => ({ value, label }));
const dateScopeOptions: SecretSelectOption[] = [
  { value: "today", label: "Today" },
  { value: "all", label: "All Dates" }
];

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

function activityBadgeLabel(item: OperatorActivityRecord): string {
  if (item.result.toLowerCase().includes("fail") || item.result.toLowerCase().includes("error") || item.category === "recovery") {
    return "Alert";
  }
  if (item.category === "operator" || item.category === "payment") {
    return "Important Action";
  }
  return humanizeAction(item.category || "Activity");
}

function activitySummary(item: OperatorActivityRecord): string {
  const actor = item.operator_name;
  switch (item.category) {
    case "operator": {
      const target = item.customer ?? "another operator";
      return `${actor} changed the active operator to ${target}.`;
    }
    case "payment": {
      const amount = item.amount ? formatMoney(item.amount) : "a payment";
      const method = item.payment_method ? ` via ${item.payment_method}` : "";
      const target = item.pc_name ? ` on ${item.pc_name}` : "";
      return `${actor} collected ${amount}${method}${target}.`;
    }
    case "remote": {
      const target = item.pc_name ? ` to ${item.pc_name}` : "";
      return `${actor} sent a remote command${target}.`;
    }
    case "recovery":
      return `${actor} triggered a database backup.`;
    case "session": {
      const target = item.pc_name ? ` for ${item.pc_name}` : "";
      return `${actor} recorded ${humanizeAction(item.action).toLowerCase()}${target}.`;
    }
    default:
      return `${actor} recorded ${humanizeAction(item.action).toLowerCase()}.`;
  }
}

function activityAboutText(category: string): string {
  switch (category) {
    case "operator":
      return "This action is logged whenever the active operator changes for cashier or admin operations. It helps maintain accountability and track who was signed in at a given time.";
    case "payment":
      return "This action is logged whenever a payment is collected from a member or guest. It helps reconcile cash and digital payments against recorded sessions.";
    case "remote":
      return "This action is logged whenever a remote power, wake, message, or announcement command is sent to a shop PC.";
    case "recovery":
      return "This action is logged whenever a database backup or recovery step runs, so the shop has a record of when its data was last protected.";
    case "session":
      return "This action is logged whenever a cashier session is started, extended, or ended on a station.";
    default:
      return "This action is logged as part of Secret Grounds' operator activity trail for accountability and troubleshooting.";
  }
}

function findPreviousOperatorEvent(
  activity: OperatorActivityRecord[],
  selected: OperatorActivityRecord
): OperatorActivityRecord | null {
  const selectedTime = new Date(selected.timestamp).getTime();
  let closest: OperatorActivityRecord | null = null;
  let closestTime = -Infinity;
  for (const item of activity) {
    if (item.category !== "operator" || item === selected) {
      continue;
    }
    const time = new Date(item.timestamp).getTime();
    if (time < selectedTime && time > closestTime) {
      closest = item;
      closestTime = time;
    }
  }
  return closest;
}

function formatEventDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value));
}

function formatEventTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { timeStyle: "short" }).format(new Date(value));
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
      <td><button className={styles.activityViewDetailsButton} onClick={() => onDetails(item)} type="button">View Details</button></td>
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
  const operatorFilterOptions: SecretSelectOption[] = useMemo(
    () => [
      { value: "", label: "All Operators" },
      ...operatorOptions.map((operator) => ({ value: operator.operator_id, label: operator.display_name }))
    ],
    [operatorOptions]
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
  const selectedOperatorRole = useMemo(() => {
    if (!selectedActivity?.operator_id) {
      return null;
    }
    const match = operators.find((operator) => operator.operator_id === selectedActivity.operator_id);
    return match ? humanizeAction(match.role) : null;
  }, [operators, selectedActivity]);
  const previousOperatorEvent = useMemo(() => {
    if (!selectedActivity || selectedActivity.category !== "operator") {
      return null;
    }
    return findPreviousOperatorEvent(activity, selectedActivity);
  }, [activity, selectedActivity]);

  function clearFilters() {
    setDateScope("today");
    setOperatorId("");
    setCategory("");
    setPage(1);
  }

  return (
    <section className={styles.activityPanel}>
      <section className={styles.summaryCards} aria-label="Operator activity summary">
        <article className={styles.summaryCard}><span>Events</span><strong>{summary.events}</strong></article>
        <article className={styles.summaryCard}><span>Operators Active</span><strong>{summary.operatorsActive}</strong></article>
        <article className={styles.summaryCard}><span>Payments Processed</span><strong>{summary.payments}</strong></article>
        <article className={styles.summaryCard}><span>Important Alerts</span><strong>{summary.alerts}</strong></article>
      </section>

      <div className={styles.filterToolbar} aria-label="Operator activity filters">
        <div className={`${styles.filterFields} ${styles.filterFieldsThree}`}>
          <SecretSelect
            active={dateScope !== "today"}
            label="Date"
            leadingIcon={<SelectGlyph name="calendar" />}
            onChange={(nextValue) => setDateScope(nextValue as "today" | "all")}
            options={dateScopeOptions}
            value={dateScope}
          />
          <SecretSelect
            active={operatorId !== ""}
            label="Operator"
            leadingIcon={<SelectGlyph name="users" />}
            onChange={setOperatorId}
            options={operatorFilterOptions}
            placeholder="All Operators"
            searchable
            searchPlaceholder="Search operators..."
            value={operatorId}
          />
          <SecretSelect
            active={category !== ""}
            label="Action"
            leadingIcon={<SelectGlyph name="list-filter" />}
            onChange={setCategory}
            options={categoryOptions}
            value={category}
          />
        </div>
        <div className={styles.filterActionCell}>
          <span aria-hidden="true" className={styles.filterActionSpacer}>Action</span>
          <div className={styles.filterActionRow}>
            <button className={styles.filterClearButton} onClick={clearFilters} type="button">Clear</button>
          </div>
        </div>
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
            <table className={styles.activityTable}>
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
          <div className={styles.activityPagination}>
            <span>{activity.length} results / page {page} of {totalPages}</span>
            <div className={styles.activityPaginationActions}>
              <button className={styles.activityPageButton} disabled={page === 1} onClick={() => setPage((current) => Math.max(1, current - 1))} type="button">Previous</button>
              <button className={styles.activityPageButton} disabled={page === totalPages} onClick={() => setPage((current) => Math.min(totalPages, current + 1))} type="button">Next</button>
            </div>
          </div>
        </>
      ) : null}

      {selectedActivity ? (
        <div className={styles.overlay} role="presentation" onClick={() => setSelectedActivity(null)}>
          <section
            aria-label="Operator activity details"
            aria-modal="true"
            className={styles.activityDrawer}
            onClick={(event) => event.stopPropagation()}
            role="dialog"
          >
            <div className={styles.activityDrawerHeader}>
              <div className={styles.activityDrawerHeaderTop}>
                <span className={styles.activityBadge}>{activityBadgeLabel(selectedActivity)}</span>
                <CloseButton label="Close operator activity details" onClick={() => setSelectedActivity(null)} />
              </div>
              <h2>{humanizeAction(selectedActivity.action)}</h2>
              <div className={styles.activityOperatorRow}>
                <div className={styles.activityOperatorIdentity}>
                  <span aria-hidden="true" className={styles.activityAvatar}>
                    {selectedActivity.operator_name.slice(0, 1)}
                  </span>
                  <div>
                    <strong>{selectedActivity.operator_name}</strong>
                    {selectedOperatorRole ? <span>{selectedOperatorRole}</span> : null}
                  </div>
                </div>
                <div className={styles.activityDrawerTimestamp}>
                  <span>{formatEventDate(selectedActivity.timestamp)}</span>
                  <span>{formatEventTime(selectedActivity.timestamp)}</span>
                </div>
              </div>
            </div>

            <div className={styles.activitySummaryCard}>
              <span className={styles.activitySummaryLabel}>Summary</span>
              <p>{activitySummary(selectedActivity)}</p>
            </div>

            <div className={styles.activityDetailsCard}>
              <span className={styles.activityDetailsLabel}>Details</span>
              <div className={styles.activityDetailsGrid}>
                <div className={styles.activityDetailRow}>
                  <span>Context</span>
                  <strong>{activityContext(selectedActivity)}</strong>
                </div>
                <div className={styles.activityDetailRow}>
                  <span>Category</span>
                  <strong>{humanizeAction(selectedActivity.category)}</strong>
                </div>
                <div className={styles.activityDetailRow}>
                  <span>Result</span>
                  <strong>{activityResult(selectedActivity)}</strong>
                </div>
                <div className={styles.activityDetailRow}>
                  <span>Amount</span>
                  <strong>{activityAmount(selectedActivity)}</strong>
                </div>
              </div>
            </div>

            {previousOperatorEvent ? (
              <div className={styles.activityTimelineCard}>
                <span className={styles.activityTimelineLabel}>Change Timeline</span>
                <ol className={styles.activityTimeline}>
                  <li className={styles.activityTimelineItem}>
                    <span className={styles.activityTimelineDot} data-current="true" />
                    <div>
                      <div className={styles.activityTimelineItemHead}>
                        <strong>{humanizeAction(selectedActivity.action)}</strong>
                        <time>{formatEventTime(selectedActivity.timestamp)}</time>
                      </div>
                      <p>{activityContext(selectedActivity)}</p>
                    </div>
                  </li>
                  <li className={styles.activityTimelineItem}>
                    <span className={styles.activityTimelineDot} />
                    <div>
                      <div className={styles.activityTimelineItemHead}>
                        <strong>Previous Operator</strong>
                        <time>{formatEventTime(previousOperatorEvent.timestamp)}</time>
                      </div>
                      <p>{activityContext(previousOperatorEvent)}</p>
                    </div>
                  </li>
                </ol>
              </div>
            ) : null}

            <div className={styles.activityAbout}>
              <span className={styles.activityAboutLabel}>About This Action</span>
              <p>{activityAboutText(selectedActivity.category)}</p>
            </div>
          </section>
        </div>
      ) : null}
    </section>
  );
}

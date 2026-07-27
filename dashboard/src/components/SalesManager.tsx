"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchSalesReport, getSalesExportUrl, type SalesReportFilters } from "@/lib/api";
import { formatDateTime, formatMoney, formatNaturalDuration } from "@/lib/time";
import type { SalesPeriod, SalesReport, SalesType } from "@/lib/types";
import styles from "@/app/sales/sales.module.css";

const PERIODS: Array<{ label: string; value: SalesPeriod }> = [
  { label: "Today", value: "today" },
  { label: "Yesterday", value: "yesterday" },
  { label: "This Week", value: "this_week" },
  { label: "This Month", value: "this_month" },
  { label: "Custom Range", value: "custom" }
];

const SALES_TYPE_LABELS: Record<SalesType, string> = {
  member_purchase: "Member Purchase",
  other: "Other",
  pc_session: "PC Session"
};

function todayInputValue(): string {
  return new Date().toISOString().slice(0, 10);
}

function displayMethod(method: string): string {
  return method === "gcash" ? "GCash" : method === "maya" ? "Maya" : method[0].toUpperCase() + method.slice(1);
}

export function SalesManager() {
  const [period, setPeriod] = useState<SalesPeriod>("today");
  const [startDate, setStartDate] = useState(todayInputValue);
  const [endDate, setEndDate] = useState(todayInputValue);
  const [report, setReport] = useState<SalesReport | null>(null);
  const [message, setMessage] = useState<string | null>("Loading sales report...");
  const [isLoading, setIsLoading] = useState(true);

  const filters: SalesReportFilters = useMemo(
    () => ({ endDate, period, startDate }),
    [endDate, period, startDate]
  );
  const chartMax = useMemo(
    () =>
      Math.max(
        1,
        ...(report?.timeseries.map((point) => Number(point.amount)) ?? [0])
      ),
    [report]
  );

  useEffect(() => {
    let isMounted = true;
    fetchSalesReport(filters)
      .then((nextReport) => {
        if (!isMounted) {
          return;
        }
        setReport(nextReport);
        setMessage(null);
      })
      .catch((error) => {
        if (isMounted) {
          setMessage(error instanceof Error ? error.message : "Could not load sales report.");
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
  }, [filters]);

  const hasPayments = (report?.summary.payment_count ?? 0) > 0;
  const exportUrl = getSalesExportUrl(filters);

  function changePeriod(nextPeriod: SalesPeriod) {
    setIsLoading(true);
    setPeriod(nextPeriod);
  }

  function changeStartDate(nextStartDate: string) {
    setIsLoading(true);
    setStartDate(nextStartDate);
  }

  function changeEndDate(nextEndDate: string) {
    setIsLoading(true);
    setEndDate(nextEndDate);
  }

  return (
    <>
      <header className={styles.header}>
        <div>
          <h1>Sales</h1>
          <p>Collected payments, settlements, member purchases, and operator totals.</p>
        </div>
        <div className={styles.headerActions}>
          <div className={styles.periodGroup} aria-label="Sales date period">
            {PERIODS.map((option) => (
              <button
                className={period === option.value ? styles.periodActive : ""}
                key={option.value}
                onClick={() => changePeriod(option.value)}
                type="button"
              >
                {option.label}
              </button>
            ))}
          </div>
          <a className={styles.exportButton} href={exportUrl}>
            Export CSV
          </a>
        </div>
      </header>

      {period === "custom" ? (
        <div className={styles.customRange}>
          <label>
            Start date
            <input onChange={(event) => changeStartDate(event.target.value)} type="date" value={startDate} />
          </label>
          <label>
            End date
            <input onChange={(event) => changeEndDate(event.target.value)} type="date" value={endDate} />
          </label>
        </div>
      ) : null}

      {message ? <div className={styles.message}>{message}</div> : null}

      {isLoading ? (
        <div className={styles.loadingGrid} aria-live="polite">
          <span />
          <span />
          <span />
          <span />
        </div>
      ) : report === null ? null : (
        <>
          <section className={styles.summaryGrid} aria-label="Sales summary">
            <MetricCard label="Total Sales" value={formatMoney(report.summary.total_sales)} tone="primary" />
            <MetricCard label="Cash" value={formatMoney(report.summary.cash_total)} detail={`${report.payment_methods.find((item) => item.payment_method === "cash")?.payment_count ?? 0} payments`} />
            <MetricCard label="GCash" value={formatMoney(report.summary.gcash_total)} detail={`${report.payment_methods.find((item) => item.payment_method === "gcash")?.payment_count ?? 0} payments`} />
            <MetricCard label="Maya" value={formatMoney(report.summary.maya_total)} detail={`${report.payment_methods.find((item) => item.payment_method === "maya")?.payment_count ?? 0} payments`} />
            <MetricCard label="Payments" value={String(report.summary.payment_count)} detail={`Average ${formatMoney(report.summary.average_transaction_value)}`} />
            <MetricCard label="Outstanding" value={formatMoney(report.summary.outstanding_amount)} tone={Number(report.summary.outstanding_amount) > 0 ? "warning" : "default"} />
          </section>

          {!hasPayments ? (
            <section className={styles.emptyState}>
              <h2>No sales recorded for this period.</h2>
              <p>Collected payments will appear here.</p>
            </section>
          ) : null}

          <section className={styles.contentGrid}>
            <div className={styles.mainColumn}>
              <section className={styles.panel}>
                <div className={styles.panelHeader}>
                  <div>
                    <h2>{report.grouping === "hour" ? "Sales by Hour" : "Sales by Day"}</h2>
                    <p>Uses payment collection time, not session start time.</p>
                  </div>
                </div>
                <div className={styles.chart} aria-label="Sales chart">
                  {report.timeseries.length === 0 ? (
                    <div className={styles.miniEmpty}>No chart data for this period.</div>
                  ) : (
                    report.timeseries.map((point) => {
                      const percent = Math.max(4, (Number(point.amount) / chartMax) * 100);
                      return (
                        <div className={styles.chartRow} key={point.start_at}>
                          <span>{point.label}</span>
                          <div>
                            <i style={{ width: `${percent}%` }} />
                          </div>
                          <strong>{formatMoney(point.amount)}</strong>
                        </div>
                      );
                    })
                  )}
                </div>
              </section>

              <section className={styles.panel}>
                <div className={styles.panelHeader}>
                  <div>
                    <h2>Recent Payments</h2>
                    <p>Newest collected payments first.</p>
                  </div>
                </div>
                <div className={styles.tableWrap}>
                  <table className={styles.table}>
                    <thead>
                      <tr>
                        <th>Time</th>
                        <th>Operator</th>
                        <th>Customer / Member</th>
                        <th>PC</th>
                        <th>Type</th>
                        <th>Method</th>
                        <th>Amount</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.recent_payments.map((payment) => (
                        <tr key={payment.payment_id}>
                          <td>{formatDateTime(payment.timestamp)}</td>
                          <td>{payment.operator_name}</td>
                          <td>{payment.customer ?? "Walk-in"}</td>
                          <td>{payment.pc_name ?? "No PC"}</td>
                          <td>{SALES_TYPE_LABELS[payment.sales_type]}</td>
                          <td>{displayMethod(payment.payment_method)}</td>
                          <td>{formatMoney(payment.amount)}</td>
                          <td>{payment.status === "completed" ? "Completed" : "Voided"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </div>

            <aside className={styles.sideColumn}>
              <BreakdownPanel
                title="Sales by Type"
                rows={report.sales_types.map((item) => ({
                  amount: item.amount,
                  count: item.payment_count,
                  label: item.label
                }))}
              />
              <BreakdownPanel
                title="Payment Methods"
                rows={report.payment_methods.map((item) => ({
                  amount: item.amount,
                  count: item.payment_count,
                  label: displayMethod(item.payment_method)
                }))}
              />
              <section className={styles.panel}>
                <h2>Sales by Operator</h2>
                <div className={styles.stackList}>
                  {report.operators.length === 0 ? <div className={styles.miniEmpty}>No operator sales yet.</div> : null}
                  {report.operators.map((operator) => (
                    <article key={operator.operator_id ?? operator.operator_name}>
                      <strong>{operator.operator_name}</strong>
                      <span>{formatMoney(operator.total_amount)} collected</span>
                      <small>{operator.payment_count} payments / Cash {formatMoney(operator.cash_total)} / GCash {formatMoney(operator.gcash_total)} / Maya {formatMoney(operator.maya_total)}</small>
                    </article>
                  ))}
                </div>
              </section>
              <section className={styles.panel}>
                <h2>Sales by PC</h2>
                <div className={styles.stackList}>
                  {report.pcs.length === 0 ? <div className={styles.miniEmpty}>No station-linked sales yet.</div> : null}
                  {report.pcs.map((pc) => (
                    <article key={pc.pc_name}>
                      <strong>{pc.pc_name}</strong>
                      <span>{formatMoney(pc.total_amount)}</span>
                      <small>
                        {pc.paid_session_count} paid sessions
                        {pc.billable_seconds ? ` / ${formatNaturalDuration(pc.billable_seconds)} billable` : ""}
                      </small>
                    </article>
                  ))}
                </div>
              </section>
            </aside>
          </section>

          {report.outstanding_settlements.length > 0 ? (
            <section className={styles.panel}>
              <div className={styles.panelHeader}>
                <div>
                  <h2>Outstanding Settlements</h2>
                  <p>Unpaid balances are not counted as collected sales.</p>
                </div>
              </div>
              <div className={styles.outstandingGrid}>
                {report.outstanding_settlements.map((settlement) => (
                  <article key={settlement.session_id}>
                    <strong>{settlement.pc_name}</strong>
                    <span>{settlement.customer ?? "Walk-in"}</span>
                    <b>{formatMoney(settlement.amount_due)} due</b>
                    <small>
                      {settlement.session_end_time ? formatDateTime(settlement.session_end_time) : "Session not ended"} /{" "}
                      {settlement.payment_status.replaceAll("_", " ")}
                    </small>
                  </article>
                ))}
              </div>
            </section>
          ) : null}
        </>
      )}
    </>
  );
}

function MetricCard({
  detail,
  label,
  tone = "default",
  value
}: {
  detail?: string;
  label: string;
  tone?: "default" | "primary" | "warning";
  value: string;
}) {
  return (
    <article className={`${styles.metricCard} ${tone === "primary" ? styles.metricPrimary : ""} ${tone === "warning" ? styles.metricWarning : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {detail ? <small>{detail}</small> : null}
    </article>
  );
}

function BreakdownPanel({
  rows,
  title
}: {
  rows: Array<{ amount: string; count: number; label: string }>;
  title: string;
}) {
  return (
    <section className={styles.panel}>
      <h2>{title}</h2>
      <div className={styles.breakdownList}>
        {rows.map((row) => (
          <article key={row.label}>
            <span>{row.label}</span>
            <strong>{formatMoney(row.amount)}</strong>
            <small>{row.count} payments</small>
          </article>
        ))}
      </div>
    </section>
  );
}

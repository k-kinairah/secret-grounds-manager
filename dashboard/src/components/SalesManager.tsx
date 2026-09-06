"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { fetchCurrentUser, fetchSalesReport, getSalesExportUrl, type SalesReportFilters } from "@/lib/api";
import { formatDateTime, formatMoney, formatNaturalDuration } from "@/lib/time";
import type { PaymentMethod, PaymentStatus, SalesOperatorBreakdown, SalesOperatorCollection, SalesOperatorTransaction, SalesPeriod, SalesReport, SalesSummary, SalesType, UserRole } from "@/lib/types";
import styles from "@/app/sales/sales.module.css";
import pageStyles from "@/app/page.module.css";
import { CloseButton } from "./CloseButton";
import { NetIncomeModal } from "./NetIncomeModal";
import { PageTitleIcon } from "./PageTitleIcon";
import { SecretSelect, SelectGlyph, type SecretSelectOption } from "./SecretSelect";

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

function salesTypeLabel(salesType: SalesType, balanceType?: "standard" | "vip" | null): string {
  if (salesType === "member_purchase") {
    return balanceType === "vip" ? "VIP Promo" : "Standard Top-up";
  }
  return SALES_TYPE_LABELS[salesType];
}

const METHOD_FILTER_OPTIONS: SecretSelectOption[] = [
  { label: "All methods", value: "all" },
  { label: "Cash", value: "cash" },
  { label: "GCash", value: "gcash" },
  { label: "Other", value: "other" }
];

const TYPE_FILTER_OPTIONS: SecretSelectOption[] = [
  { label: "All types", value: "all" },
  { label: "PC Session", value: "pc_session" },
  { label: "Member Purchase", value: "member_purchase" },
  { label: "Other", value: "other" }
];

const STATUS_FILTER_OPTIONS: SecretSelectOption[] = [
  { label: "All statuses", value: "all" },
  { label: "Completed", value: "completed" },
  { label: "Voided / Refunded", value: "voided" }
];

const OPERATOR_TRANSACTION_PAGE_SIZE = 25;

const SORT_FILTER_OPTIONS: SecretSelectOption[] = [
  { label: "Newest first", value: "newest" },
  { label: "Oldest first", value: "oldest" },
  { label: "Highest amount", value: "highest" },
  { label: "Lowest amount", value: "lowest" }
];

// Cyan-blue accent used for plain breakdown labels (Sales by Type,
// Payment Methods, Pending Attribution).
const ACCENT_LABEL = "#7dd3fc";

// Per-owner display label + colour for the Income by Group breakdown,
// keyed by the group's stored name.
const INCOME_GROUP_STYLE: Record<string, { label?: string; color: string }> = {
  Gene: { color: "#f472b6" }, // pink
  "Mama/Lala": { color: "#da70d6" }, // orchid
  Tyrone: { color: "#60a5fa" }, // blue
  United: { label: "Shop", color: "#22d3ee" } // cyan
};

function todayInputValue(): string {
  return new Date().toISOString().slice(0, 10);
}

function displayMethod(method: string): string {
  return method === "gcash" ? "GCash" : method === "maya" ? "Maya" : method[0].toUpperCase() + method.slice(1);
}

type OperatorCollectionView = SalesOperatorCollection & {
  cash_refunds: string;
};

function moneyNumber(value: string | null | undefined): number {
  const amount = Number(value ?? 0);
  return Number.isFinite(amount) ? amount : 0;
}

function moneyValue(value: number): string {
  return value.toFixed(2);
}

function summarizeOperatorCollection(
  operator: SalesOperatorCollection,
  transactions: SalesOperatorTransaction[],
  includeOutstanding: boolean
): OperatorCollectionView {
  const grossSales = transactions.reduce((sum, transaction) => sum + moneyNumber(transaction.amount_due), 0);
  const refundsVoids = transactions
    .filter((transaction) => transaction.status === "voided")
    .reduce((sum, transaction) => sum + moneyNumber(transaction.amount_due), 0);
  const netSales = transactions
    .filter((transaction) => transaction.status === "completed")
    .reduce((sum, transaction) => sum + moneyNumber(transaction.amount_due), 0);
  const cashReceived = transactions
    .filter((transaction) => transaction.status === "completed" && transaction.payment_method === "cash")
    .reduce((sum, transaction) => sum + moneyNumber(transaction.cash_received), 0);
  const changeGiven = transactions
    .filter((transaction) => transaction.status === "completed" && transaction.payment_method === "cash")
    .reduce((sum, transaction) => sum + moneyNumber(transaction.change_given), 0);
  const cashRefunds = transactions
    .filter((transaction) => transaction.status === "voided" && transaction.payment_method === "cash")
    .reduce((sum, transaction) => sum + moneyNumber(transaction.amount_due), 0);
  const nonCashCollected = transactions
    .filter((transaction) => transaction.status === "completed" && transaction.payment_method !== "cash")
    .reduce((sum, transaction) => sum + moneyNumber(transaction.amount_due), 0);

  return {
    ...operator,
    cash_received: moneyValue(cashReceived),
    cash_refunds: moneyValue(cashRefunds),
    change_given: moneyValue(changeGiven),
    expected_cash: moneyValue(cashReceived - changeGiven - cashRefunds),
    gross_sales: moneyValue(grossSales),
    net_sales: moneyValue(netSales),
    non_cash_collected: moneyValue(nonCashCollected),
    outstanding: includeOutstanding ? operator.outstanding : "0.00",
    refunds_voids: moneyValue(refundsVoids),
    transaction_count: transactions.length,
    transactions
  };
}

function statusLabel(transaction: SalesOperatorTransaction): string {
  if (transaction.status === "completed") {
    return "Completed";
  }
  return transaction.voided_by_operator ? "Refunded" : "Voided";
}

function transactionSourceLabel(transaction: SalesOperatorTransaction): string {
  if (transaction.sales_type === "member_purchase") {
    return "Not yet used on a PC";
  }
  return transaction.pc_name ?? "No PC assigned";
}

function compactReference(reference: string | null): string | null {
  if (!reference) {
    return null;
  }
  if (reference.length <= 14) {
    return reference;
  }
  return `...${reference.slice(-6)}`;
}

function salesPeriodLabel(period: SalesPeriod, startDate: string, endDate: string): string {
  if (period === "custom") {
    return startDate === endDate ? startDate : `${startDate} to ${endDate}`;
  }
  return PERIODS.find((option) => option.value === period)?.label ?? "Selected period";
}

// start_date / end_date are inclusive shop-local calendar days (YYYY-MM-DD)
// the backend already resolved, so this just formats them -- no timezone math.
// Collapses to a single date when the range is one day (Today / Yesterday /
// a one-day custom range).
function reportDateRangeLabel(startDate: string, endDate: string): string {
  const formatDate = (value: string) => {
    const [year, month, day] = value.split("-").map(Number);
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(
      new Date(year, month - 1, day)
    );
  };
  const startText = formatDate(startDate);
  const endText = formatDate(endDate);
  return startText === endText ? startText : `${startText} – ${endText}`;
}

// Plain-text daily summary: date, grand totals, and each operator's collected
// total. Built entirely from the already-loaded report -- no extra request.
function buildSalesSummaryText(report: SalesReport, periodLabel: string): string {
  const thin = "-".repeat(44);
  const lines: string[] = [
    "SECRET GROUNDS — SALES SUMMARY",
    "=".repeat(44),
    "",
    `Period:     ${periodLabel}`,
    `Date:       ${reportDateRangeLabel(report.start_date, report.end_date)}`,
    `Generated:  ${new Date().toLocaleString()}`,
    "",
    "TOTAL SALES",
    thin,
    `Net sales:      ${formatMoney(report.summary.net_sales ?? report.summary.total_sales)}`,
    `Gross sales:    ${formatMoney(report.summary.gross_sales ?? report.summary.total_sales)}`,
    `Refunds/voids:  ${formatMoney(report.summary.refund_total ?? "0.00")}`,
    `Payments:       ${report.summary.payment_count}`,
    "",
    "SALES BY OPERATOR",
    thin
  ];

  if (report.operators.length === 0) {
    lines.push("(no operator sales in this period)");
  } else {
    let operatorTotal = 0;
    let operatorPayments = 0;
    for (const operator of report.operators) {
      operatorTotal += Number(operator.total_amount);
      operatorPayments += operator.payment_count;
      lines.push(
        `${operator.operator_name.padEnd(22)}${formatMoney(operator.total_amount).padStart(14)}   (${operator.payment_count} payments)`
      );
    }
    lines.push(thin);
    lines.push(
      `${"TOTAL".padEnd(22)}${formatMoney(operatorTotal).padStart(14)}   (${operatorPayments} payments)`
    );
  }
  lines.push("");
  // CRLF so the file opens cleanly in Windows Notepad.
  return lines.join("\r\n");
}

function salesSummaryFileStamp(period: SalesPeriod, startDate: string, endDate: string): string {
  if (period === "custom") {
    return startDate === endDate ? startDate : `${startDate}_to_${endDate}`;
  }
  return period;
}

export function SalesManager() {
  const [period, setPeriod] = useState<SalesPeriod>("today");
  const [startDate, setStartDate] = useState(todayInputValue);
  const [endDate, setEndDate] = useState(todayInputValue);
  const [report, setReport] = useState<SalesReport | null>(null);
  const [message, setMessage] = useState<string | null>("Loading sales report...");
  const [isLoading, setIsLoading] = useState(true);
  const [operatorFilter, setOperatorFilter] = useState("all");
  const [methodFilter, setMethodFilter] = useState<PaymentMethod | "all">("all");
  const [typeFilter, setTypeFilter] = useState<SalesType | "all">("all");
  const [statusFilter, setStatusFilter] = useState<PaymentStatus | "all">("all");
  const [selectedOperatorKey, setSelectedOperatorKey] = useState<string | null>(null);
  const [showDetailedTable, setShowDetailedTable] = useState(false);
  const [role, setRole] = useState<UserRole | null>(null);
  const [showNetIncome, setShowNetIncome] = useState(false);
  const canSeeNetIncome = role === "admin" || role === "owner";

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
    let active = true;
    fetchCurrentUser()
      .then((user) => {
        if (active) setRole(user.role);
      })
      .catch(() => {
        if (active) setRole(null);
      });
    return () => {
      active = false;
    };
  }, []);

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
  const operatorCollections = useMemo(() => {
    const rows = report?.operator_collections ?? [];
    const includeOutstanding = methodFilter === "all" && typeFilter === "all" && statusFilter === "all";
    return rows
      .map((operator) => {
        const transactions = operator.transactions.filter((transaction) => {
          if (methodFilter !== "all" && transaction.payment_method !== methodFilter) {
            return false;
          }
          if (typeFilter !== "all" && transaction.sales_type !== typeFilter) {
            return false;
          }
          if (statusFilter !== "all" && transaction.status !== statusFilter) {
            return false;
          }
          return true;
        });
        return summarizeOperatorCollection(operator, transactions, includeOutstanding);
      })
      .filter((operator) => operatorFilter === "all" || (operator.operator_id ?? operator.operator_name) === operatorFilter)
      .filter((operator) => operator.transactions.length > 0 || moneyNumber(operator.outstanding) > 0);
  }, [methodFilter, operatorFilter, report, statusFilter, typeFilter]);
  const operatorOptions = report?.operator_collections ?? [];

  function changePeriod(nextPeriod: SalesPeriod) {
    setIsLoading(true);
    setPeriod(nextPeriod);
  }

  function downloadSalesSummaryText() {
    if (report === null) {
      return;
    }
    const text = buildSalesSummaryText(report, salesPeriodLabel(period, startDate, endDate));
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `secret-grounds-sales-${salesSummaryFileStamp(period, startDate, endDate)}.txt`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
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
        <div className="pageTitleLockup">
          <PageTitleIcon name="receipt" />
          <div>
            <h1>Sales</h1>
            <p>Collected payments, settlements, member purchases, and operator totals.</p>
          </div>
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
          {canSeeNetIncome ? (
            <button
              className={styles.dailySummaryButton}
              onClick={() => setShowNetIncome(true)}
              type="button"
            >
              Net Income
            </button>
          ) : null}
          <a className={styles.dailySummaryButton} href={exportUrl}>
            Export CSV
          </a>
        </div>
      </header>

      {showNetIncome ? <NetIncomeModal onClose={() => setShowNetIncome(false)} /> : null}

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
            <MetricCard label="Net Sales" value={formatMoney(report.summary.net_sales ?? report.summary.total_sales)} tone="primary" />
            <MetricCard label="Gross Sales" value={formatMoney(report.summary.gross_sales ?? report.summary.total_sales)} />
            <MetricCard label="Refunds / Voids" value={formatMoney(report.summary.refund_total ?? "0.00")} tone={Number(report.summary.refund_total ?? 0) > 0 ? "warning" : "default"} />
            <MetricCard label="Cash Received" value={formatMoney(report.summary.cash_received_total ?? report.summary.cash_total)} detail={`Change ${formatMoney(report.summary.change_given_total ?? "0.00")}`} />
            <MetricCard label="Non-Cash Collected" value={formatMoney(report.summary.non_cash_collected ?? "0.00")} detail="GCash, Maya, and other references" />
            <MetricCard label="Payments" value={String(report.summary.payment_count)} detail={`Average ${formatMoney(report.summary.average_transaction_value)}`} />
            <MetricCard label="Outstanding" value={formatMoney(report.summary.outstanding_amount)} detail="Unpaid and partially paid balances" tone={Number(report.summary.outstanding_amount) > 0 ? "warning" : "default"} />
          </section>

          <OperatorCollections
            dailySummary={{
              dateRangeLabel: reportDateRangeLabel(report.start_date, report.end_date),
              onExportTxt: downloadSalesSummaryText,
              operators: report.operators,
              summary: report.summary
            }}
            methodFilter={methodFilter}
            operatorCollections={operatorCollections}
            operatorFilter={operatorFilter}
            operatorOptions={operatorOptions}
            periodLabel={salesPeriodLabel(period, startDate, endDate)}
            selectedOperatorKey={selectedOperatorKey}
            setMethodFilter={setMethodFilter}
            setOperatorFilter={setOperatorFilter}
            setSelectedOperatorKey={setSelectedOperatorKey}
            setShowDetailedTable={setShowDetailedTable}
            setStatusFilter={setStatusFilter}
            setTypeFilter={setTypeFilter}
            showDetailedTable={showDetailedTable}
            statusFilter={statusFilter}
            typeFilter={typeFilter}
          />

          {!hasPayments ? (
            <section className={styles.emptyState}>
              <h2>No sales recorded for this period.</h2>
              <p>Collected payments will appear here.</p>
            </section>
          ) : null}

          <section className={styles.breakdownGrid} aria-label="Sales breakdowns">
            <div className={styles.breakdownRow3}>
              <div className={styles.breakdownStack}>
                <BreakdownPanel
                  title="Sales by Type"
                  rows={report.sales_types.map((item) => ({
                    amount: item.amount,
                    count: item.payment_count,
                    label: item.label,
                    color: ACCENT_LABEL
                  }))}
                />
                <BreakdownPanel
                  title="Payment Methods"
                  rows={report.payment_methods
                    .filter((item) => item.payment_method !== "maya" && item.payment_method !== "other")
                    .map((item) => ({
                      amount: item.amount,
                      count: item.payment_count,
                      label: displayMethod(item.payment_method),
                      color: ACCENT_LABEL
                    }))}
                />
              </div>
              <BreakdownPanel
                title="Income by Group"
                description="Revenue attributed to each income-group owner for this period, based on the PC each payment was made on at the time."
                rows={[
                  // Shop (the "United" owner) always leads the breakdown --
                  // everyone else keeps the order the backend returned.
                  ...[...report.income_groups]
                    .sort((a, b) =>
                      Number(b.income_group_name === "United") - Number(a.income_group_name === "United")
                    )
                    .map((group) => ({
                      amount: group.amount,
                      count: group.payment_count,
                      label: INCOME_GROUP_STYLE[group.income_group_name]?.label ?? group.income_group_name,
                      color: INCOME_GROUP_STYLE[group.income_group_name]?.color
                    })),
                  {
                    amount: report.pending_attribution_amount,
                    count: report.pending_attribution_count,
                    label: "Pending Attribution",
                    hint: "Payments awaiting automatic income-group assignment.",
                    color: "#4ade80"
                  }
                ]}
              />
            </div>
            <div className={styles.breakdownRow2}>
              <section className={styles.panel}>
                <h2>Sales by Operator</h2>
                <div className={styles.stackList}>
                  {report.operators.length === 0 ? <div className={styles.miniEmpty}>No operator sales yet.</div> : null}
                  {report.operators.map((operator) => (
                    <article key={operator.operator_id ?? operator.operator_name}>
                      <div className={styles.stackRow}>
                        <strong>{operator.operator_name}</strong>
                        <b>{formatMoney(operator.total_amount)}</b>
                      </div>
                      <small>
                        {operator.payment_count} payments · Cash {formatMoney(operator.cash_total)} · GCash{" "}
                        {formatMoney(operator.gcash_total)}
                      </small>
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
                      <div className={styles.stackRow}>
                        <strong>{pc.pc_name}</strong>
                        <b>{formatMoney(pc.total_amount)}</b>
                      </div>
                      <small>
                        {pc.paid_session_count} paid sessions
                        {pc.billable_seconds ? ` · ${formatNaturalDuration(pc.billable_seconds)} billable` : ""}
                      </small>
                    </article>
                  ))}
                </div>
              </section>
            </div>
          </section>

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
                      <td>{salesTypeLabel(payment.sales_type, payment.balance_type)}</td>
                      <td>{displayMethod(payment.payment_method)}</td>
                      <td>{formatMoney(payment.amount)}</td>
                      <td>{payment.status === "completed" ? "Completed" : "Voided"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
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

function OperatorCollections({
  dailySummary,
  methodFilter,
  operatorCollections,
  operatorFilter,
  operatorOptions,
  periodLabel,
  selectedOperatorKey,
  setMethodFilter,
  setOperatorFilter,
  setSelectedOperatorKey,
  setShowDetailedTable,
  setStatusFilter,
  setTypeFilter,
  showDetailedTable,
  statusFilter,
  typeFilter
}: {
  dailySummary: {
    dateRangeLabel: string;
    onExportTxt: () => void;
    operators: SalesOperatorBreakdown[];
    summary: SalesSummary;
  };
  methodFilter: PaymentMethod | "all";
  operatorCollections: OperatorCollectionView[];
  operatorFilter: string;
  operatorOptions: SalesOperatorCollection[];
  periodLabel: string;
  selectedOperatorKey: string | null;
  setMethodFilter: (value: PaymentMethod | "all") => void;
  setOperatorFilter: (value: string) => void;
  setSelectedOperatorKey: (key: string | null) => void;
  setShowDetailedTable: (value: boolean) => void;
  setStatusFilter: (value: PaymentStatus | "all") => void;
  setTypeFilter: (value: SalesType | "all") => void;
  showDetailedTable: boolean;
  statusFilter: PaymentStatus | "all";
  typeFilter: SalesType | "all";
}) {
  const [selectedTransaction, setSelectedTransaction] = useState<SalesOperatorTransaction | null>(null);
  const [showDailySummary, setShowDailySummary] = useState(false);
  const triggerRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const hasActiveFilters = operatorFilter !== "all" || methodFilter !== "all" || typeFilter !== "all" || statusFilter !== "all";
  const selectedOperator =
    operatorCollections.find((operator) => (operator.operator_id ?? operator.operator_name) === selectedOperatorKey) ?? null;
  const operatorGridClassName = [
    styles.operatorCardGrid,
    operatorCollections.length === 1
      ? styles.operatorCardGridSingle
      : operatorCollections.length === 2
        ? styles.operatorCardGridDouble
        : styles.operatorCardGridMultiple
  ].join(" ");
  const totals = operatorCollections.reduce(
    (sum, operator) => ({
      transaction_count: sum.transaction_count + operator.transaction_count,
      gross_sales: sum.gross_sales + Number(operator.gross_sales),
      refunds_voids: sum.refunds_voids + Number(operator.refunds_voids),
      net_sales: sum.net_sales + Number(operator.net_sales),
      cash_received: sum.cash_received + Number(operator.cash_received),
      change_given: sum.change_given + Number(operator.change_given),
      cash_refunds: sum.cash_refunds + Number(operator.cash_refunds),
      expected_cash: sum.expected_cash + Number(operator.expected_cash),
      non_cash_collected: sum.non_cash_collected + Number(operator.non_cash_collected),
      outstanding: sum.outstanding + Number(operator.outstanding)
    }),
    {
      transaction_count: 0,
      gross_sales: 0,
      refunds_voids: 0,
      net_sales: 0,
      cash_received: 0,
      change_given: 0,
      cash_refunds: 0,
      expected_cash: 0,
      non_cash_collected: 0,
      outstanding: 0
    }
  );
  const clearFilters = () => {
    setOperatorFilter("all");
    setMethodFilter("all");
    setTypeFilter("all");
    setStatusFilter("all");
  };
  const closeOperatorModal = () => {
    const closingKey = selectedOperatorKey;
    setSelectedOperatorKey(null);
    window.setTimeout(() => {
      if (closingKey) {
        triggerRefs.current[closingKey]?.focus();
      }
    }, 0);
  };
  const operatorFilterOptions = useMemo(
    () => [
      { label: "All operators", value: "all" },
      ...operatorOptions.map((operator) => ({
        label: operator.operator_name,
        value: operator.operator_id ?? operator.operator_name
      }))
    ],
    [operatorOptions]
  );

  return (
    <section className={styles.panel}>
      <div className={styles.panelHeader}>
        <div>
          <h2>Operator Collections</h2>
          <p>Expected drawer cash = cash received - change given - cash refunds paid out.</p>
        </div>
        <button
          className={styles.dailySummaryButton}
          onClick={() => setShowDailySummary(true)}
          type="button"
        >
          Daily Summary
        </button>
      </div>
      <div className={styles.collectionToolbar} aria-label="Operator collection filters">
        <div className={styles.collectionFilters}>
          <SecretSelect
            active={operatorFilter !== "all"}
            label="Operator"
            leadingIcon={<SelectGlyph name="users" />}
            onChange={setOperatorFilter}
            options={operatorFilterOptions}
            placeholder="All operators"
            searchable
            searchPlaceholder="Search operators..."
            value={operatorFilter}
          />
          <SecretSelect
            active={methodFilter !== "all"}
            label="Method"
            leadingIcon={<SelectGlyph name="credit-card" />}
            onChange={(nextValue) => setMethodFilter(nextValue as PaymentMethod | "all")}
            options={METHOD_FILTER_OPTIONS}
            placeholder="All methods"
            value={methodFilter}
          />
          <SecretSelect
            active={typeFilter !== "all"}
            label="Type"
            leadingIcon={<SelectGlyph name="list-filter" />}
            onChange={(nextValue) => setTypeFilter(nextValue as SalesType | "all")}
            options={TYPE_FILTER_OPTIONS}
            placeholder="All types"
            value={typeFilter}
          />
          <SecretSelect
            active={statusFilter !== "all"}
            label="Status"
            leadingIcon={<SelectGlyph name="badge" />}
            onChange={(nextValue) => setStatusFilter(nextValue as PaymentStatus | "all")}
            options={STATUS_FILTER_OPTIONS}
            placeholder="All statuses"
            value={statusFilter}
          />
        </div>
        <div className={styles.collectionActionCell}>
          <span aria-hidden="true" className={styles.collectionActionSpacer}>Action</span>
          <div className={styles.collectionActionRow}>
            {hasActiveFilters ? (
              <button className={styles.collectionActionButton} onClick={clearFilters} type="button">
                Clear filters
              </button>
            ) : null}
            <button className={styles.collectionActionButton} onClick={() => setShowDetailedTable(!showDetailedTable)} type="button">
              {showDetailedTable ? "Hide detailed table" : "Show detailed table"}
            </button>
          </div>
        </div>
      </div>
      {operatorCollections.length === 0 ? (
        <div className={styles.miniEmpty}>No operator collections match the current filters.</div>
      ) : (
        <div className={operatorGridClassName}>
          {operatorCollections.map((operator) => {
            const operatorKey = operator.operator_id ?? operator.operator_name;
            return (
              <article className={styles.operatorCollectionCard} key={operatorKey}>
                <div className={styles.operatorCardHeader}>
                  <div>
                    <h3>{operator.operator_name}</h3>
                    <span>{operator.transaction_count} transactions</span>
                  </div>
                  <div className={styles.operatorNetValue}>
                    <span>Net</span>
                    <strong>{formatMoney(operator.net_sales)}</strong>
                  </div>
                </div>
                <div className={styles.operatorGroups}>
                  <CollectionGroup title="Sales">
                    <MoneyRow label="Gross sales" value={formatMoney(operator.gross_sales)} />
                    <MoneyRow label="Refunds / voids" value={formatMoney(operator.refunds_voids)} tone="warning" />
                    <MoneyRow emphasized label="Net sales" value={formatMoney(operator.net_sales)} />
                  </CollectionGroup>
                  <CollectionGroup className={styles.cashAccountability} title="Cash Accountability">
                    <MoneyRow label="Cash received" value={formatMoney(operator.cash_received)} />
                    <MoneyRow label="Change given" value={formatMoney(operator.change_given)} tone="warning" />
                    <MoneyRow label="Cash refunds" value={formatMoney(operator.cash_refunds)} tone="warning" />
                    <MoneyRow emphasized label="Expected drawer cash" value={formatMoney(operator.expected_cash)} />
                  </CollectionGroup>
                  <CollectionGroup title="Other">
                    <MoneyRow label="Non-cash" value={formatMoney(operator.non_cash_collected)} />
                    <MoneyRow label="Outstanding" value={formatMoney(operator.outstanding)} tone={Number(operator.outstanding) > 0 ? "warning" : "muted"} />
                  </CollectionGroup>
                </div>
                <button
                  className={styles.operatorDetailsButton}
                  onClick={() => setSelectedOperatorKey(operatorKey)}
                  ref={(element) => {
                    triggerRefs.current[operatorKey] = element;
                  }}
                  type="button"
                >
                  <SelectGlyph name="list-filter" />
                  View Transactions
                </button>
              </article>
            );
          })}
        </div>
      )}
      {showDetailedTable ? (
        <OperatorCollectionsTable
          onViewOperator={setSelectedOperatorKey}
          operatorCollections={operatorCollections}
          totals={totals}
        />
      ) : null}
      {selectedOperator ? (
        <OperatorTransactionsModal
          onClose={closeOperatorModal}
          onViewDetails={setSelectedTransaction}
          operator={selectedOperator}
          periodLabel={periodLabel}
        />
      ) : null}
      {selectedTransaction ? (
        <TransactionDetailsModal onClose={() => setSelectedTransaction(null)} transaction={selectedTransaction} />
      ) : null}
      {showDailySummary ? (
        <DailySummaryModal
          dateRangeLabel={dailySummary.dateRangeLabel}
          onClose={() => setShowDailySummary(false)}
          onExportTxt={dailySummary.onExportTxt}
          operators={dailySummary.operators}
          periodLabel={periodLabel}
          summary={dailySummary.summary}
        />
      ) : null}
    </section>
  );
}

function DailySummaryModal({
  dateRangeLabel,
  onClose,
  onExportTxt,
  operators,
  periodLabel,
  summary
}: {
  dateRangeLabel: string;
  onClose: () => void;
  onExportTxt: () => void;
  operators: SalesOperatorBreakdown[];
  periodLabel: string;
  summary: SalesSummary;
}) {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div className={styles.transactionModalOverlay} onClick={onClose} role="presentation">
      <div
        aria-label="Daily sales summary"
        aria-modal="true"
        className={`${styles.transactionModal} ${styles.dailySummaryModal}`}
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <div className={styles.transactionModalHeader}>
          <div>
            <h2>Daily Sales Summary</h2>
            <span>{periodLabel} · {dateRangeLabel}</span>
          </div>
          <CloseButton
            className={pageStyles.iconButtonPremiumClose}
            label="Close daily sales summary"
            onClick={onClose}
          />
        </div>
        <div className={styles.stackList}>
          <article>
            <div className={styles.stackRow}>
              <strong>Total sales</strong>
              <b>{formatMoney(summary.net_sales ?? summary.total_sales)}</b>
            </div>
          </article>
          {operators.length === 0 ? (
            <div className={styles.miniEmpty}>No operator sales in this period.</div>
          ) : (
            operators.map((operator) => (
              <article key={operator.operator_id ?? operator.operator_name}>
                <div className={styles.stackRow}>
                  <strong>{operator.operator_name}</strong>
                  <b>{formatMoney(operator.total_amount)}</b>
                </div>
                <small>
                  {operator.signed_out_at
                    ? `Signed out ${formatDateTime(operator.signed_out_at)}`
                    : "Not signed out yet"}
                </small>
              </article>
            ))
          )}
        </div>
        <div className={styles.dailySummaryActions}>
          <button className={styles.dailySummaryButton} onClick={onExportTxt} type="button">
            Export TXT
          </button>
        </div>
      </div>
    </div>
  );
}

function MoneyTile({
  label,
  tone = "default",
  value
}: {
  label: string;
  tone?: "default" | "primary" | "warning";
  value: string;
}) {
  return (
    <div className={`${styles.collectionTotalTile} ${tone === "primary" ? styles.collectionTotalPrimary : ""} ${tone === "warning" ? styles.collectionTotalWarning : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function CollectionGroup({
  children,
  className = "",
  title
}: {
  children: ReactNode;
  className?: string;
  title: string;
}) {
  return (
    <section className={`${styles.collectionGroup} ${className}`}>
      <h4>{title}</h4>
      {children}
    </section>
  );
}

function MoneyRow({
  emphasized = false,
  label,
  tone = "default",
  value
}: {
  emphasized?: boolean;
  label: string;
  tone?: "default" | "muted" | "warning";
  value: string;
}) {
  return (
    <div className={`${styles.moneyRow} ${emphasized ? styles.moneyRowStrong : ""} ${tone === "warning" ? styles.moneyNegative : ""} ${tone === "muted" ? styles.moneyMuted : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function OperatorTransactionsModal({
  onClose,
  onViewDetails,
  operator,
  periodLabel
}: {
  onClose: () => void;
  onViewDetails: (transaction: SalesOperatorTransaction) => void;
  operator: OperatorCollectionView;
  periodLabel: string;
}) {
  const modalRef = useRef<HTMLDivElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const [modalTypeFilter, setModalTypeFilter] = useState<SalesType | "all">("all");
  const [modalMethodFilter, setModalMethodFilter] = useState<PaymentMethod | "all">("all");
  const [modalStatusFilter, setModalStatusFilter] = useState<PaymentStatus | "all">("all");
  const [sortOrder, setSortOrder] = useState("newest");
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(1);
  const filteredTransactions = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return operator.transactions
      .filter((transaction) => {
        if (modalTypeFilter !== "all" && transaction.sales_type !== modalTypeFilter) {
          return false;
        }
        if (modalMethodFilter !== "all" && transaction.payment_method !== modalMethodFilter) {
          return false;
        }
        if (modalStatusFilter !== "all" && transaction.status !== modalStatusFilter) {
          return false;
        }
        if (!query) {
          return true;
        }
        return [
          transaction.customer,
          transaction.pc_name,
          transaction.payment_id,
          transaction.reference_number,
          transaction.related_reference,
          transaction.recorded_by_operator,
          salesTypeLabel(transaction.sales_type, transaction.balance_type),
          transactionSourceLabel(transaction)
        ]
          .filter(Boolean)
          .some((value) => String(value).toLowerCase().includes(query));
      })
      .sort((left, right) => {
        if (sortOrder === "oldest") {
          return Date.parse(left.timestamp) - Date.parse(right.timestamp);
        }
        if (sortOrder === "highest") {
          return moneyNumber(right.amount_due) - moneyNumber(left.amount_due);
        }
        if (sortOrder === "lowest") {
          return moneyNumber(left.amount_due) - moneyNumber(right.amount_due);
        }
        return Date.parse(right.timestamp) - Date.parse(left.timestamp);
      });
  }, [modalMethodFilter, modalStatusFilter, modalTypeFilter, operator.transactions, searchQuery, sortOrder]);
  const totalPages = Math.max(1, Math.ceil(filteredTransactions.length / OPERATOR_TRANSACTION_PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pageStart = (currentPage - 1) * OPERATOR_TRANSACTION_PAGE_SIZE;
  const visibleTransactions = filteredTransactions.slice(pageStart, pageStart + OPERATOR_TRANSACTION_PAGE_SIZE);

  useEffect(() => {
    setPage(1);
  }, [modalMethodFilter, modalStatusFilter, modalTypeFilter, operator, searchQuery, sortOrder]);

  useEffect(() => {
    closeButtonRef.current?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || modalRef.current === null) {
        return;
      }
      const focusable = Array.from(
        modalRef.current.querySelectorAll<HTMLElement>(
          "button, input, summary, [href], [tabindex]:not([tabindex='-1'])"
        )
      ).filter((element) => !element.hasAttribute("disabled"));
      if (focusable.length === 0) {
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div className={styles.operatorModalBackdrop} role="presentation">
      <div aria-modal="true" className={styles.operatorModal} ref={modalRef} role="dialog">
        <header className={styles.operatorModalHeader}>
          <div className={styles.operatorModalTitle}>
            <h2>{operator.operator_name} Transactions</h2>
            <p>{operator.transaction_count} transactions · {periodLabel}</p>
            <div className={styles.operatorModalTotals}>
              <ModalSummaryItem label="Net sales" tone="primary" value={formatMoney(operator.net_sales)} />
              <ModalSummaryItem label="Expected drawer cash" value={formatMoney(operator.expected_cash)} />
            </div>
          </div>
          <button aria-label={`Close ${operator.operator_name} transactions`} className={styles.operatorModalClose} onClick={onClose} ref={closeButtonRef} type="button">
            Close
          </button>
        </header>
        <div className={styles.operatorModalFilters}>
          <SecretSelect
            active={modalTypeFilter !== "all"}
            label="Transaction type"
            leadingIcon={<SelectGlyph name="list-filter" />}
            onChange={(nextValue) => setModalTypeFilter(nextValue as SalesType | "all")}
            options={TYPE_FILTER_OPTIONS}
            placeholder="All types"
            value={modalTypeFilter}
          />
          <SecretSelect
            active={modalMethodFilter !== "all"}
            label="Payment method"
            leadingIcon={<SelectGlyph name="credit-card" />}
            onChange={(nextValue) => setModalMethodFilter(nextValue as PaymentMethod | "all")}
            options={METHOD_FILTER_OPTIONS}
            placeholder="All methods"
            value={modalMethodFilter}
          />
          <SecretSelect
            active={modalStatusFilter !== "all"}
            label="Status"
            leadingIcon={<SelectGlyph name="badge" />}
            onChange={(nextValue) => setModalStatusFilter(nextValue as PaymentStatus | "all")}
            options={STATUS_FILTER_OPTIONS}
            placeholder="All statuses"
            value={modalStatusFilter}
          />
          <SecretSelect
            active={sortOrder !== "newest"}
            label="Sort"
            leadingIcon={<SelectGlyph name="list-filter" />}
            onChange={setSortOrder}
            options={SORT_FILTER_OPTIONS}
            placeholder="Newest first"
            value={sortOrder}
          />
          <label className={styles.operatorModalSearch}>
            Search
            <input
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Member, PC, transaction ID, reference"
              type="search"
              value={searchQuery}
            />
          </label>
        </div>
        <div className={styles.operatorModalBody}>
          {visibleTransactions.length === 0 ? (
            <div className={styles.miniEmpty}>No transactions match the selected filters.</div>
          ) : (
            <div className={styles.detailTransactionGrid}>
              {visibleTransactions.map((transaction) => (
                <TransactionReceipt
                  key={transaction.payment_id}
                  onViewDetails={onViewDetails}
                  transaction={transaction}
                />
              ))}
            </div>
          )}
        </div>
        <footer className={styles.operatorPagination}>
          <span>
            {filteredTransactions.length === 0
              ? "0 results"
              : `${pageStart + 1}-${Math.min(pageStart + OPERATOR_TRANSACTION_PAGE_SIZE, filteredTransactions.length)} of ${filteredTransactions.length}`}
          </span>
          <div>
            <button aria-label="Previous operator transaction page" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)} type="button">
              Previous
            </button>
            <strong>Page {currentPage} of {totalPages}</strong>
            <button aria-label="Next operator transaction page" disabled={currentPage >= totalPages} onClick={() => setPage(currentPage + 1)} type="button">
              Next
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

function ModalSummaryItem({
  label,
  tone = "default",
  value
}: {
  label: string;
  tone?: "default" | "primary";
  value: string;
}) {
  return (
    <div className={`${styles.operatorModalSummaryItem} ${tone === "primary" ? styles.operatorModalSummaryPrimary : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function TransactionReceipt({
  onViewDetails,
  transaction
}: {
  onViewDetails: (transaction: SalesOperatorTransaction) => void;
  transaction: SalesOperatorTransaction;
}) {
  return (
    <article aria-label={`${salesTypeLabel(transaction.sales_type, transaction.balance_type)} ${statusLabel(transaction)} ${formatMoney(transaction.amount_due)}`} className={styles.transactionReceipt}>
      <div className={styles.transactionReceiptHeader}>
        <div>
          <strong>
            {salesTypeLabel(transaction.sales_type, transaction.balance_type)}
            {transaction.balance_type === "vip" ? (
              <span className={styles.vipPurchaseBadge}>Non-refundable</span>
            ) : null}
          </strong>
          <span>{formatDateTime(transaction.timestamp)}</span>
        </div>
        <span className={`${styles.statusBadge} ${transaction.status === "completed" ? styles.statusCompleted : styles.statusVoided}`}>
          {statusLabel(transaction)}
        </span>
      </div>
      <div className={styles.transactionReceiptBody}>
        <ReceiptField label="Member / customer" value={transaction.customer ?? "Walk-in"} />
        <ReceiptField label="Operator" value={transaction.recorded_by_operator} />
        <ReceiptField label="Amount due" value={formatMoney(transaction.amount_due)} />
        <ReceiptField label="Payment method" value={displayMethod(transaction.payment_method)} />
        {transaction.payment_method === "cash" ? (
          <>
            <ReceiptField label="Cash received" value={formatMoney(transaction.cash_received)} />
            <ReceiptField label="Change" value={formatMoney(transaction.change_given)} />
          </>
        ) : (
          <ReceiptField label="Payment reference" value={compactReference(transaction.reference_number) ?? "Not provided"} />
        )}
        <ReceiptField label={transaction.sales_type === "member_purchase" ? "Usage status" : "PC / Session"} value={transactionSourceLabel(transaction)} />
        {transaction.status === "voided" ? (
          <>
            <ReceiptField label="Correction amount" value={formatMoney(transaction.amount_due)} />
            <ReceiptField label="Corrected by" value={transaction.voided_by_operator ?? "Unknown"} />
          </>
        ) : null}
      </div>
      <details className={styles.technicalDetailsCompact}>
        <summary>Technical details</summary>
        <ReceiptField label="Transaction UUID" value={transaction.payment_id} />
        <ReceiptField label="Reference" value={transaction.reference_number ?? "None"} />
        <ReceiptField label="Linked reversal ID" value={transaction.related_reference ?? "None"} />
      </details>
      <div className={styles.transactionReceiptFooter}>
        <button className={styles.operatorDetailsButton} onClick={() => onViewDetails(transaction)} type="button">
          View details
        </button>
      </div>
    </article>
  );
}

function ReceiptField({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.receiptField}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function DetailSection({
  children,
  title
}: {
  children: ReactNode;
  title: string;
}) {
  return (
    <section className={styles.detailSection}>
      <h3>{title}</h3>
      <div>{children}</div>
    </section>
  );
}

function TransactionDetailsModal({
  onClose,
  transaction
}: {
  onClose: () => void;
  transaction: SalesOperatorTransaction;
}) {
  return (
    <div className={styles.transactionModalOverlay} role="presentation">
      <div aria-modal="true" className={styles.transactionModal} role="dialog">
        <div className={styles.transactionModalHeader}>
          <div>
            <h2>
              {salesTypeLabel(transaction.sales_type, transaction.balance_type)}
              {transaction.balance_type === "vip" ? (
                <span className={styles.vipPurchaseBadge}>Non-refundable</span>
              ) : null}
            </h2>
            <span>{formatDateTime(transaction.timestamp)}</span>
          </div>
          <button className={styles.collectionActionButton} onClick={onClose} type="button">
            Close
          </button>
        </div>
        <div className={styles.transactionModalGrid}>
          <DetailSection title="Transaction">
            <ReceiptField label="Status" value={statusLabel(transaction)} />
            <ReceiptField label="Type" value={salesTypeLabel(transaction.sales_type, transaction.balance_type)} />
            {transaction.balance_type === "vip" ? (
              <ReceiptField label="Refund policy" value="Non-refundable" />
            ) : null}
            <ReceiptField label="Original amount" value={formatMoney(transaction.amount_due)} />
            {transaction.status === "voided" ? <ReceiptField label="Correction amount" value={formatMoney(transaction.amount_due)} /> : null}
          </DetailSection>
          <DetailSection title="Payment">
            <ReceiptField label="Method" value={displayMethod(transaction.payment_method)} />
            {transaction.payment_method === "cash" ? (
              <>
                <ReceiptField label="Cash received" value={formatMoney(transaction.cash_received)} />
                <ReceiptField label="Change given" value={formatMoney(transaction.change_given)} />
              </>
            ) : (
              <ReceiptField label="Payment reference" value={transaction.reference_number ?? "Not provided"} />
            )}
          </DetailSection>
          <DetailSection title="Member / Customer">
            <ReceiptField label="Customer" value={transaction.customer ?? "Walk-in"} />
            {transaction.sales_type === "member_purchase" ? <ReceiptField label="Usage status" value="Not yet used on a PC" /> : null}
          </DetailSection>
          <DetailSection title="PC / Session">
            <ReceiptField label="Source" value={transaction.sales_type === "member_purchase" ? "Member prepaid balance" : transactionSourceLabel(transaction)} />
          </DetailSection>
          <DetailSection title="Audit">
            <ReceiptField label="Recorded by" value={transaction.recorded_by_operator} />
            {transaction.voided_by_operator ? <ReceiptField label="Correction by" value={transaction.voided_by_operator} /> : null}
            {transaction.voided_at ? <ReceiptField label="Correction time" value={formatDateTime(transaction.voided_at)} /> : null}
            {transaction.void_reason ? <ReceiptField label="Reason" value={transaction.void_reason} /> : null}
          </DetailSection>
        </div>
        <details className={styles.technicalDetails}>
          <summary>Technical details</summary>
          <ReceiptField label="Transaction UUID" value={transaction.payment_id} />
          <ReceiptField label="Internal payment reference" value={transaction.reference_number ?? "None"} />
          <ReceiptField label="Linked reversal ID" value={transaction.related_reference ?? "None"} />
          <ReceiptField label="Raw timestamp" value={transaction.timestamp} />
        </details>
      </div>
    </div>
  );
}

function OperatorCollectionsTable({
  onViewOperator,
  operatorCollections,
  totals
}: {
  onViewOperator: (key: string) => void;
  operatorCollections: OperatorCollectionView[];
  totals: {
    transaction_count: number;
    gross_sales: number;
    refunds_voids: number;
    net_sales: number;
    cash_received: number;
    change_given: number;
    cash_refunds: number;
    expected_cash: number;
    non_cash_collected: number;
    outstanding: number;
  };
}) {
  return (
    <div className={styles.tableWrap}>
      <table className={`${styles.table} ${styles.collectionTable}`}>
        <colgroup>
          <col className={styles.collectionOperatorColumn} />
          <col className={styles.collectionCountColumn} />
          <col className={styles.collectionMoneyColumn} />
          <col className={styles.collectionRefundsColumn} />
          <col className={styles.collectionMoneyColumn} />
          <col className={styles.collectionCashReceivedColumn} />
          <col className={styles.collectionMoneyColumn} />
          <col className={styles.collectionCashRefundsColumn} />
          <col className={styles.collectionExpectedCashColumn} />
          <col className={styles.collectionMoneyColumn} />
          <col className={styles.collectionOutstandingColumn} />
        </colgroup>
        <thead>
          <tr>
            <th>Operator</th>
            <th className={styles.numericCell}>Transactions</th>
            <th className={`${styles.numericCell} ${styles.salesGroupCell}`}>Gross</th>
            <th className={`${styles.numericCell} ${styles.salesGroupCell}`}>Refunds / Voids</th>
            <th className={`${styles.numericCell} ${styles.salesGroupCell}`}>Net</th>
            <th className={`${styles.numericCell} ${styles.cashGroupCell}`}>Cash Received</th>
            <th className={styles.numericCell}>Change</th>
            <th className={styles.numericCell}>Cash Refunds</th>
            <th className={`${styles.numericCell} ${styles.cashGroupCell}`}>Expected Cash</th>
            <th className={`${styles.numericCell} ${styles.otherGroupCell}`}>Non-Cash</th>
            <th className={styles.numericCell}>Outstanding</th>
          </tr>
        </thead>
        <tbody>
          {operatorCollections.length === 0 ? (
            <tr>
              <td colSpan={11}>No operator collections match the current filters.</td>
            </tr>
          ) : null}
          {operatorCollections.map((operator) => {
            const operatorKey = operator.operator_id ?? operator.operator_name;
            return (
              <tr key={operatorKey}>
                <td>
                  <button className={styles.operatorExpandButton} onClick={() => onViewOperator(operatorKey)} type="button">
                    {operator.operator_name}
                  </button>
                </td>
                <td className={styles.numericCell}>{operator.transaction_count}</td>
                <td className={`${styles.numericCell} ${styles.salesGroupCell}`}>{formatMoney(operator.gross_sales)}</td>
                <td className={`${styles.numericCell} ${styles.salesGroupCell}`}>{formatMoney(operator.refunds_voids)}</td>
                <td className={`${styles.numericCell} ${styles.salesGroupCell}`}>{formatMoney(operator.net_sales)}</td>
                <td className={`${styles.numericCell} ${styles.cashGroupCell}`}>{formatMoney(operator.cash_received)}</td>
                <td className={styles.numericCell}>{formatMoney(operator.change_given)}</td>
                <td className={styles.numericCell}>{formatMoney(operator.cash_refunds)}</td>
                <td className={`${styles.numericCell} ${styles.cashGroupCell}`}>{formatMoney(operator.expected_cash)}</td>
                <td className={`${styles.numericCell} ${styles.otherGroupCell}`}>{formatMoney(operator.non_cash_collected)}</td>
                <td className={styles.numericCell}>{formatMoney(operator.outstanding)}</td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <th>Totals</th>
            <td className={styles.numericCell}>{totals.transaction_count}</td>
            <td className={`${styles.numericCell} ${styles.salesGroupCell}`}>{formatMoney(totals.gross_sales)}</td>
            <td className={`${styles.numericCell} ${styles.salesGroupCell}`}>{formatMoney(totals.refunds_voids)}</td>
            <td className={`${styles.numericCell} ${styles.salesGroupCell}`}>{formatMoney(totals.net_sales)}</td>
            <td className={`${styles.numericCell} ${styles.cashGroupCell}`}>{formatMoney(totals.cash_received)}</td>
            <td className={styles.numericCell}>{formatMoney(totals.change_given)}</td>
            <td className={styles.numericCell}>{formatMoney(totals.cash_refunds)}</td>
            <td className={`${styles.numericCell} ${styles.cashGroupCell}`}>{formatMoney(totals.expected_cash)}</td>
            <td className={`${styles.numericCell} ${styles.otherGroupCell}`}>{formatMoney(totals.non_cash_collected)}</td>
            <td className={styles.numericCell}>{formatMoney(totals.outstanding)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
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
  title,
  description
}: {
  rows: Array<{ amount: string; count: number; label: string; hint?: string; color?: string }>;
  title: string;
  description?: string;
}) {
  return (
    <section className={styles.panel}>
      <h2>{title}</h2>
      {description ? <p>{description}</p> : null}
      <div className={styles.breakdownList}>
        {rows.map((row) => (
          <article key={row.label}>
            <div className={styles.breakdownRow}>
              <span style={row.color ? { color: row.color } : undefined}>{row.label}</span>
              <span className={styles.breakdownValue}>
                <strong>{formatMoney(row.amount)}</strong>
                {row.hint ? null : <span className={styles.breakdownCount}>{row.count}</span>}
              </span>
            </div>
            {row.hint ? <small>{row.hint}</small> : null}
          </article>
        ))}
      </div>
    </section>
  );
}

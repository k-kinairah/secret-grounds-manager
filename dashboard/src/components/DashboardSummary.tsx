import styles from "@/app/page.module.css";
import { formatMoney } from "@/lib/time";
import type { DashboardSummary as DashboardSummaryData } from "@/lib/types";

interface DashboardSummaryProps {
  currentOperator: string;
  summary: DashboardSummaryData;
  todaySales: string | null;
}

export function DashboardSummary({ currentOperator, summary, todaySales }: DashboardSummaryProps) {
  const cards: Array<[string, string, string | null]> = [
    ["Available", String(summary.available), null],
    ["In Use", String(summary.inUse), `${summary.total} registered`],
    ["Today's Sales", todaySales === null ? "--" : formatMoney(todaySales), null],
    ["Current Operator", currentOperator, null]
  ];
  if (summary.paymentDue > 0) {
    cards.push(["Settlement Due", formatMoney(summary.settlementDueAmount), `${summary.paymentDue} PCs`]);
  }

  return (
    <section className={styles.summaryGrid} aria-label="Shop summary">
      {cards.map(([label, value, detail]) => (
        <div className={styles.summaryCard} key={label}>
          <div className={styles.summaryLabel}>{label}</div>
          <div className={styles.summaryValue}>{value}</div>
          {detail ? <div className={styles.summaryDetail}>{detail}</div> : null}
        </div>
      ))}
    </section>
  );
}

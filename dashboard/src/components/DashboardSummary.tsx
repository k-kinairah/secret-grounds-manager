import styles from "@/app/page.module.css";
import type { DashboardSummary as DashboardSummaryData } from "@/lib/types";

interface DashboardSummaryProps {
  summary: DashboardSummaryData;
}

export function DashboardSummary({ summary }: DashboardSummaryProps) {
  const cards = [
    ["Total PCs", summary.total],
    ["Active PCs", summary.inUse],
    ["Grace Period", summary.gracePeriod],
    ["Shutting Down", summary.shuttingDown],
    ["Settlement Pending", summary.settlementPending],
    ["Available", summary.available],
    ["Powered Off", summary.poweredOff]
  ];

  return (
    <section className={styles.summaryGrid} aria-label="PC summary">
      {cards.map(([label, value]) => (
        <div className={styles.summaryCard} key={label}>
          <div className={styles.summaryLabel}>{label}</div>
          <div className={styles.summaryValue}>{value}</div>
        </div>
      ))}
    </section>
  );
}

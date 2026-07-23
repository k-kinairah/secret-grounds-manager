import styles from "@/app/page.module.css";
import type { DashboardSummary as DashboardSummaryData } from "@/lib/types";

interface DashboardSummaryProps {
  summary: DashboardSummaryData;
}

export function DashboardSummary({ summary }: DashboardSummaryProps) {
  const cards = [
    ["Total PCs", summary.total],
    ["In Use", summary.inUse],
    ["Available", summary.available],
    ["Offline", summary.offline]
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

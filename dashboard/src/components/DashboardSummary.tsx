import styles from "@/app/page.module.css";
import { formatMoney } from "@/lib/time";
import type { DashboardSummary as DashboardSummaryData } from "@/lib/types";
import { StatusCard } from "./StatusCard";

interface DashboardSummaryProps {
  currentOperator: string;
  summary: DashboardSummaryData;
  todaySales: string | null;
}

export function DashboardSummary({ currentOperator, summary, todaySales }: DashboardSummaryProps) {
  const cards: Array<{
    detail: string | null;
    icon: string;
    label: string;
    tone: "neutral" | "success" | "warning" | "danger" | "info";
    value: string;
  }> = [
    { icon: "OK", label: "Available", value: String(summary.available), detail: `${summary.total} stations`, tone: "success" },
    { icon: "PC", label: "In Use", value: String(summary.inUse), detail: "Active sessions", tone: "info" },
    { icon: "$", label: "Today's Sales", value: todaySales === null ? "--" : formatMoney(todaySales), detail: "Gross collected", tone: "neutral" },
    { icon: "OP", label: "Operator", value: currentOperator, detail: "On duty", tone: "neutral" }
  ];
  if (summary.paymentDue > 0) {
    cards.push({ icon: "!", label: "Settlement", value: formatMoney(summary.settlementDueAmount), detail: `${summary.paymentDue} PCs`, tone: "warning" });
  }

  return (
    <section className={styles.summaryGrid} aria-label="Shop summary">
      {cards.map((card) => (
        <StatusCard
          detail={card.detail}
          icon={card.icon}
          key={card.label}
          title={card.label}
          tone={card.tone}
          value={card.value}
        />
      ))}
    </section>
  );
}

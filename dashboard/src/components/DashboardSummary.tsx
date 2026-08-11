import styles from "@/app/page.module.css";
import { formatMoney } from "@/lib/time";
import type { DashboardSummary as DashboardSummaryData, OperatorSaleSummary } from "@/lib/types";
import { StatusCard } from "./StatusCard";

type SummaryIconName = "available" | "inUse" | "operator" | "sales" | "settlement";

interface DashboardSummaryProps {
  currentOperator: string;
  summary: DashboardSummaryData;
  operatorSale: OperatorSaleSummary | null;
}

function operatorSaleCard(operatorSale: OperatorSaleSummary | null): { detail: string; value: string } {
  // The amount always comes straight from the Stage 5 server-authoritative
  // endpoint -- never recomputed here from raw payments/shift data.
  if (operatorSale === null) {
    return { detail: "Current shift", value: "--" };
  }
  if (!operatorSale.has_open_shift) {
    return {
      detail: operatorSale.operator_user_id === null ? "No operator on duty" : "No open shift",
      value: formatMoney(null)
    };
  }
  return {
    detail: operatorSale.operator_name ? `${operatorSale.operator_name} · Current shift` : "Current shift",
    value: formatMoney(operatorSale.operator_sale)
  };
}

export function DashboardSummary({ currentOperator, summary, operatorSale }: DashboardSummaryProps) {
  const { detail: operatorSaleDetail, value: operatorSaleValue } = operatorSaleCard(operatorSale);
  const cards: Array<{
    detail: string | null;
    icon: SummaryIconName;
    label: string;
    tone: "neutral" | "success" | "warning" | "danger" | "info";
    value: string;
  }> = [
    { icon: "available", label: "Available", value: String(summary.available), detail: `${summary.total} stations`, tone: "success" },
    { icon: "inUse", label: "In Use", value: String(summary.inUse), detail: "Active sessions", tone: "info" },
    { icon: "sales", label: "Operator's Sale", value: operatorSaleValue, detail: operatorSaleDetail, tone: "neutral" },
    { icon: "operator", label: "Operator", value: currentOperator, detail: "On duty", tone: "neutral" }
  ];
  if (summary.paymentDue > 0) {
    cards.push({ icon: "settlement", label: "Settlement", value: formatMoney(summary.settlementDueAmount), detail: `${summary.paymentDue} PCs`, tone: "warning" });
  }

  return (
    <section className={styles.summaryGrid} aria-label="Shop summary">
      {cards.map((card) => (
        <StatusCard
          detail={card.detail}
          icon={<SummaryIcon name={card.icon} />}
          key={card.label}
          title={card.label}
          tone={card.tone}
          value={card.value}
          variant="dashboard"
        />
      ))}
    </section>
  );
}

function SummaryIcon({ name }: { name: SummaryIconName }) {
  const paths: Record<SummaryIconName, Array<string>> = {
    available: ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z", "m8.5 12 2.3 2.3 4.7-5.3"],
    inUse: ["M4 5h16v11H4z", "M8 20h8", "M12 16v4", "M10 9l5 3-5 3V9z"],
    operator: ["M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M4 21c1-4 3.7-6 8-6s7 2 8 6", "m16.5 6.5 1.2 1.2 2.4-2.7"],
    sales: ["M6 3h12v18l-2-1.2-2 1.2-2-1.2-2 1.2-2-1.2L6 21V3z", "M9 7h6", "M9 11h6", "M9 15h3"],
    settlement: ["M12 3 22 20H2L12 3z", "M12 9v4", "M12 17h.01"]
  };

  return (
    <svg aria-hidden="true" className={styles.statusCardGlyph} fill="none" viewBox="0 0 24 24">
      {paths[name].map((path) => (
        <path d={path} key={path} />
      ))}
    </svg>
  );
}

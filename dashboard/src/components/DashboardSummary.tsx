import { MonitorPlay, UserRoundCheck } from "lucide-react";
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
  // The amount always comes straight from the server-authoritative endpoint
  // -- never recomputed here. It is the on-duty operator's running total for
  // their whole on-duty session (no shift required, no midnight reset); it
  // only clears when the operator logs out / is switched.
  if (operatorSale === null) {
    return { detail: "On duty", value: "--" };
  }
  if (operatorSale.operator_user_id === null) {
    return { detail: "No operator on duty", value: formatMoney(null) };
  }
  return {
    detail: operatorSale.operator_name ? `${operatorSale.operator_name} · On duty` : "On duty",
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
  if (name === "inUse") {
    return <MonitorPlay aria-hidden="true" className={styles.statusCardGlyph} />;
  }
  if (name === "operator") {
    return <UserRoundCheck aria-hidden="true" className={styles.statusCardGlyph} />;
  }

  const paths: Record<"available" | "sales" | "settlement", Array<string>> = {
    available: ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z", "m8.5 12 2.3 2.3 4.7-5.3"],
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

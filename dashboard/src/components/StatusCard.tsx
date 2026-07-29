import { memo } from "react";
import type { ReactNode } from "react";
import styles from "@/app/page.module.css";

interface StatusCardProps {
  icon: ReactNode;
  title: string;
  value: string;
  detail?: string | null;
  tone?: "neutral" | "success" | "warning" | "danger" | "info";
  variant?: "default" | "dashboard";
}

function StatusCardComponent({ icon, title, value, detail, tone = "neutral", variant = "default" }: StatusCardProps) {
  return (
    <article className={`${styles.statusCard} ${styles[`statusCard_${tone}`]} ${
      variant === "dashboard" ? styles.statusCardDashboard : ""
    }`}>
      <span className={styles.statusCardIcon} aria-hidden="true">{icon}</span>
      <div>
        <span className={styles.statusCardTitle}>{title}</span>
        <strong className={styles.statusCardValue}>{value}</strong>
        {detail ? <span className={styles.statusCardDetail}>{detail}</span> : null}
      </div>
    </article>
  );
}

export const StatusCard = memo(StatusCardComponent);

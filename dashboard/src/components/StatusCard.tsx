import { memo } from "react";
import styles from "@/app/page.module.css";

interface StatusCardProps {
  icon: string;
  title: string;
  value: string;
  detail?: string | null;
  tone?: "neutral" | "success" | "warning" | "danger" | "info";
}

function StatusCardComponent({ icon, title, value, detail, tone = "neutral" }: StatusCardProps) {
  return (
    <article className={`${styles.statusCard} ${styles[`statusCard_${tone}`]}`}>
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

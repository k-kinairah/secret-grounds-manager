import { OperatorsManager } from "@/components/OperatorsManager";
import styles from "@/app/users/users.module.css";

export default function OperatorsPage() {
  return (
    <main className={styles.page}>
      <OperatorsManager />
    </main>
  );
}

import { OperatorsManager } from "@/components/OperatorsManager";
import styles from "@/app/users/users.module.css";

export default function OperatorsPage() {
  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <h1>Staff</h1>
        <p>Compact admin-only view of configured Secret Grounds family operators.</p>
      </header>
      <OperatorsManager />
    </main>
  );
}

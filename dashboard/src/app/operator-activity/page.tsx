import { OperatorActivity } from "@/components/OperatorActivity";
import styles from "@/app/history/history.module.css";

export default function OperatorActivityPage() {
  return (
    <main className={styles.page}>
      <header className={styles.topBar}>
        <div>
          <h1>Operator Activity</h1>
          <p>Important cashier and admin actions grouped by operator and date.</p>
        </div>
      </header>
      <OperatorActivity />
    </main>
  );
}

import { OperatorActivity } from "@/components/OperatorActivity";
import { PageTitleIcon } from "@/components/PageTitleIcon";
import styles from "@/app/history/history.module.css";

export default function OperatorActivityPage() {
  return (
    <main className={styles.page}>
      <header className={styles.topBar}>
        <div className="pageTitleLockup">
          <PageTitleIcon name="activity" />
          <div>
            <h1>Operator Activity</h1>
            <p>Important cashier and admin actions grouped by operator and date.</p>
          </div>
        </div>
      </header>
      <OperatorActivity />
    </main>
  );
}

import { HistoryTable } from "@/components/HistoryTable";
import { PageTitleIcon } from "@/components/PageTitleIcon";
import styles from "./history.module.css";

export default function HistoryPage() {
  return (
    <main className={styles.page}>
      <header className={styles.topBar}>
        <div className="pageTitleLockup">
          <PageTitleIcon name="history" />
          <div>
            <h1>Session History</h1>
            <p>Review completed, active, and settled computer sessions.</p>
          </div>
        </div>
      </header>
      <HistoryTable />
    </main>
  );
}

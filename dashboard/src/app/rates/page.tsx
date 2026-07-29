import { RatesManager } from "@/components/RatesManager";
import { PageTitleIcon } from "@/components/PageTitleIcon";
import styles from "./rates.module.css";

export default function RatesPage() {
  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div className="pageTitleLockup">
          <PageTitleIcon name="rates" />
          <div>
            <h1>Rate Profiles</h1>
            <p>Edit the cashier packages used when starting or extending sessions.</p>
          </div>
        </div>
      </header>
      <RatesManager />
    </main>
  );
}

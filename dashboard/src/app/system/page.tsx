import { SystemManager } from "@/components/SystemManager";
import { PageTitleIcon } from "@/components/PageTitleIcon";
import styles from "./system.module.css";

export default function SystemPage() {
  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div className="pageTitleLockup">
          <PageTitleIcon name="monitor-cog" />
          <div>
            <h1>Admin System</h1>
            <p>Monitor health, backups, restore readiness, storage, clients, and diagnostics.</p>
          </div>
        </div>
      </header>
      <SystemManager />
    </main>
  );
}

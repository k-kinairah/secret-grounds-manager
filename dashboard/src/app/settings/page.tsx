import { SettingsManager } from "@/components/SettingsManager";
import { PageTitleIcon } from "@/components/PageTitleIcon";
import styles from "./settings.module.css";

export default function SettingsPage() {
  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div className="pageTitleLockup">
          <PageTitleIcon name="settings" />
          <div>
            <h1>Admin Settings</h1>
            <p>Review and update safe Secret Grounds operational behavior.</p>
          </div>
        </div>
      </header>
      <SettingsManager />
    </main>
  );
}

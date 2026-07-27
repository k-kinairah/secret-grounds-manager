import { SettingsManager } from "@/components/SettingsManager";
import styles from "./settings.module.css";

export default function SettingsPage() {
  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <h1>Admin Settings</h1>
        <p>Review and update safe Secret Grounds operational behavior.</p>
      </header>
      <SettingsManager />
    </main>
  );
}

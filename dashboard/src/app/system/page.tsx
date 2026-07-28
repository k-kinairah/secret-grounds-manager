import { SystemManager } from "@/components/SystemManager";
import styles from "./system.module.css";

export default function SystemPage() {
  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <h1>Admin System</h1>
        <p>Monitor health, backups, restore readiness, storage, clients, and diagnostics.</p>
      </header>
      <SystemManager />
    </main>
  );
}

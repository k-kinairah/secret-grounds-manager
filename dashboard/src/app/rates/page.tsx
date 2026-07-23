import { RatesManager } from "@/components/RatesManager";
import styles from "./rates.module.css";

export default function RatesPage() {
  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <h1>Rate Profiles</h1>
        <p>Edit the cashier packages used when starting or extending sessions.</p>
      </header>
      <RatesManager />
    </main>
  );
}

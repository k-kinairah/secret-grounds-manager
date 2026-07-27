import { SalesManager } from "@/components/SalesManager";
import styles from "./sales.module.css";

export default function SalesPage() {
  return (
    <main className={styles.page}>
      <SalesManager />
    </main>
  );
}

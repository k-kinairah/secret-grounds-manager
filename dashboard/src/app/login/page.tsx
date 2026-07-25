import { LoginClient } from "@/components/LoginClient";
import styles from "./login.module.css";

export default function LoginPage() {
  return (
    <main className={styles.page}>
      <section className={styles.panel}>
        <div>
          <p className={styles.eyebrow}>Secret Grounds Manager</p>
          <h1>Sign in</h1>
          <p className={styles.subtitle}>Use your cashier account to manage sessions and payments.</p>
        </div>
        <LoginClient />
      </section>
    </main>
  );
}

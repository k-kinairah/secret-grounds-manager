import { LoginClient } from "@/components/LoginClient";
import styles from "./login.module.css";

export default function LoginPage() {
  return (
    <main className={styles.page}>
      <LoginClient />
    </main>
  );
}

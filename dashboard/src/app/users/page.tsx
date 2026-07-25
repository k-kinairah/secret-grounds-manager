import { UsersManager } from "@/components/UsersManager";
import styles from "./users.module.css";

export default function UsersPage() {
  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <h1>Users</h1>
        <p>Manage cashier access, roles, and active staff accounts.</p>
      </header>
      <UsersManager />
    </main>
  );
}

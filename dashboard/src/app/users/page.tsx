import { UsersManager } from "@/components/UsersManager";
import { PageTitleIcon } from "@/components/PageTitleIcon";
import styles from "./users.module.css";

export default function UsersPage() {
  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div className="pageTitleLockup">
          <PageTitleIcon name="users" />
          <div>
            <h1>Users</h1>
            <p>Manage cashier access, roles, and active staff accounts.</p>
          </div>
        </div>
      </header>
      <UsersManager />
    </main>
  );
}

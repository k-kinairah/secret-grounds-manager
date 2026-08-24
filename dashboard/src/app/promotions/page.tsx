import { PromotionsManager } from "@/components/PromotionsManager";
import { PageTitleIcon } from "@/components/PageTitleIcon";
import styles from "./promotions.module.css";

export default function PromotionsPage() {
  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div className="pageTitleLockup">
          <PageTitleIcon name="promotions" />
          <div>
            <h1>Promotions</h1>
            <p>Create and manage temporary promotional offers for Standard, VIP, Member Top-up, and New Member.</p>
          </div>
        </div>
      </header>
      <PromotionsManager />
    </main>
  );
}

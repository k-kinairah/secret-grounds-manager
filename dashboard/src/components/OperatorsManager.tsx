"use client";

import { useEffect, useState } from "react";
import { fetchAdminOperators } from "@/lib/api";
import { formatDateTime } from "@/lib/time";
import type { OperatorManagementRecord } from "@/lib/types";
import styles from "@/app/users/users.module.css";

export function OperatorsManager() {
  const [operators, setOperators] = useState<OperatorManagementRecord[]>([]);
  const [message, setMessage] = useState<string | null>("Loading operators...");

  useEffect(() => {
    let isMounted = true;
    fetchAdminOperators()
      .then((records) => {
        if (!isMounted) {
          return;
        }
        setOperators(records);
        setMessage(null);
      })
      .catch((error) => {
        if (isMounted) {
          setMessage(error instanceof Error ? error.message : "Could not load operators.");
        }
      });
    return () => {
      isMounted = false;
    };
  }, []);

  return (
    <>
      {message ? <div className={styles.message}>{message}</div> : null}
      <section className={styles.staffPanel}>
        <div className={styles.staffPanelHeader}>
          <div>
            <h2>Configured Staff</h2>
            <p>Family operator access is seeded by the backend and tracked for audit history.</p>
          </div>
          <span className={styles.staffCount}>{operators.length} operators</span>
        </div>
        <div className={styles.staffGrid}>
          {operators.map((operator) => (
            <article className={styles.staffCard} key={operator.operator_id}>
              <div className={styles.staffCardTop}>
                <span className={styles.staffAvatar} aria-hidden="true">
                  {operator.display_name.slice(0, 1)}
                </span>
                <span className={operator.is_active ? styles.activeBadge : styles.disabledBadge}>
                  {operator.is_active ? "Active" : "Disabled"}
                </span>
              </div>
              <h3>{operator.display_name}</h3>
              <p>{operator.password_required ? "Password Required" : "Quick Access"}</p>
              <dl className={styles.staffMeta}>
                <div>
                  <dt>Username</dt>
                  <dd>@{operator.username}</dd>
                </div>
                <div>
                  <dt>Last login</dt>
                  <dd>{operator.last_login_at ? formatDateTime(operator.last_login_at) : "Never"}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      </section>
    </>
  );
}

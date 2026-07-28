"use client";

import { useRouter } from "next/navigation";
import type { FormEvent } from "react";
import { useEffect, useMemo, useState } from "react";
import { adminOperatorLogin, fetchLoginOperators, passwordOperatorLogin, quickOperatorLogin } from "@/lib/api";
import type { OperatorAccess } from "@/lib/types";
import styles from "@/app/login/login.module.css";

export function LoginClient() {
  const router = useRouter();
  const [operators, setOperators] = useState<OperatorAccess[]>([]);
  const [selectedAdmin, setSelectedAdmin] = useState<OperatorAccess | null>(null);
  const [adminPassword, setAdminPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const initials = useMemo(
    () =>
      new Map(
        operators.map((operator) => [
          operator.operator_id,
          operator.display_name
            .split(/\s+/)
            .map((part) => part[0])
            .join("")
            .slice(0, 2)
            .toUpperCase()
        ])
      ),
    [operators]
  );

  useEffect(() => {
    let isMounted = true;
    fetchLoginOperators()
      .then((nextOperators) => {
        if (isMounted) {
          setOperators(nextOperators);
        }
      })
      .catch((loadError) => {
        if (isMounted) {
          setError(loadError instanceof Error ? loadError.message : "Could not load operators.");
        }
      });
    return () => {
      isMounted = false;
    };
  }, []);

  function nextDestination(): string {
    const searchParams = new URLSearchParams(window.location.search);
    const next = searchParams.get("next");
    return next && next.startsWith("/") ? next : "/";
  }

  async function chooseOperator(operator: OperatorAccess) {
    if (isSubmitting) {
      return;
    }

    setError(null);
    if (operator.password_required) {
      setSelectedAdmin(operator);
      setAdminPassword("");
      return;
    }

    setIsSubmitting(true);
    try {
      await quickOperatorLogin(operator.operator_id);
      router.replace(nextDestination());
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : "Could not enter dashboard.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function submitAdmin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }

    setIsSubmitting(true);
    setError(null);
    try {
      if (selectedAdmin?.user?.username === "admin") {
        await adminOperatorLogin(adminPassword);
      } else if (selectedAdmin) {
        await passwordOperatorLogin(selectedAdmin.operator_id, adminPassword);
      }
      router.replace(nextDestination());
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : "Invalid Admin password.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <section className={styles.operatorPanel} aria-labelledby="operator-title">
      <div className={styles.logoLockup} aria-label="Secret Grounds">
        <span className={styles.logoMark}>SG</span>
        <span className={styles.logoText}>Secret Grounds</span>
      </div>
      <div>
        <p className={styles.eyebrow}>Family Operator Access</p>
        <h1 id="operator-title">Who&apos;s in charge right now?</h1>
        <p className={styles.subtitle}>Choose your name to open the shop dashboard.</p>
      </div>
      {error ? (
        <div className={styles.error} role="alert">
          {error}
        </div>
      ) : null}
      <div className={styles.operatorGrid}>
        {operators.map((operator) => (
          <button
            className={`${styles.operatorCard} ${
              operator.password_required ? styles.adminCard : ""
            }`}
            disabled={isSubmitting}
            key={operator.operator_id}
            onClick={() => void chooseOperator(operator)}
            type="button"
          >
            <span className={styles.avatar}>{initials.get(operator.operator_id)}</span>
            <span>{operator.display_name}</span>
            {operator.password_required ? <small>Admin password required</small> : <small>Quick access</small>}
          </button>
        ))}
      </div>
      {selectedAdmin ? (
        <div className={styles.adminOverlay} role="presentation">
          <form
            aria-label="Admin password"
            aria-modal="true"
            className={styles.adminDialog}
            onSubmit={submitAdmin}
            role="dialog"
          >
            <h2>{selectedAdmin.user?.username === "admin" ? "Admin Access" : `${selectedAdmin.display_name} Access`}</h2>
            <p>Enter the operator password to continue.</p>
            <label className={styles.field}>
              Password
              <input
                autoComplete="current-password"
                autoFocus
                onChange={(event) => setAdminPassword(event.target.value)}
                type={showPassword ? "text" : "password"}
                value={adminPassword}
              />
            </label>
            <label className={styles.checkboxRow}>
              <input
                checked={showPassword}
                onChange={(event) => setShowPassword(event.target.checked)}
                type="checkbox"
              />
              Show password
            </label>
            <div className={styles.dialogActions}>
              <button
                className={styles.secondaryButton}
                disabled={isSubmitting}
                onClick={() => {
                  setSelectedAdmin(null);
                  setAdminPassword("");
                }}
                type="button"
              >
                Cancel
              </button>
              <button className={styles.button} disabled={isSubmitting || !adminPassword} type="submit">
                {isSubmitting ? "Checking..." : "Enter Dashboard"}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </section>
  );
}

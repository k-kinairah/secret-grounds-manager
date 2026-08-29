"use client";

import { useRouter } from "next/navigation";
import { Eye, EyeOff } from "lucide-react";
import type { FormEvent } from "react";
import { useEffect, useMemo, useState } from "react";
import { adminOperatorLogin, fetchLoginOperators, passwordOperatorLogin, quickOperatorLogin } from "@/lib/api";
import type { OperatorAccess } from "@/lib/types";
import styles from "@/app/login/login.module.css";

// Display-only tweaks for the operator picker. The backend/database are
// unchanged -- these just adjust how the fetched operators are shown.
const DISPLAY_NAME_OVERRIDES: Record<string, string> = {
  operator_bel: "Belle"
};
// Operators pinned to the top of the grid, in this order.
const PINNED_OPERATOR_IDS = ["operator_mama"];

function displayNameFor(operator: OperatorAccess): string {
  return DISPLAY_NAME_OVERRIDES[operator.operator_id] ?? operator.display_name;
}

function orderOperators(operators: OperatorAccess[]): OperatorAccess[] {
  const rank = (operator: OperatorAccess) => {
    const index = PINNED_OPERATOR_IDS.indexOf(operator.operator_id);
    return index === -1 ? PINNED_OPERATOR_IDS.length : index;
  };
  return [...operators].sort((a, b) => rank(a) - rank(b));
}

export function LoginClient() {
  const router = useRouter();
  const [operators, setOperators] = useState<OperatorAccess[]>([]);
  const [selectedAdmin, setSelectedAdmin] = useState<OperatorAccess | null>(null);
  const [adminPassword, setAdminPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const visibleOperators = useMemo(() => orderOperators(operators), [operators]);

  const initials = useMemo(
    () =>
      new Map(
        visibleOperators.map((operator) => [
          operator.operator_id,
          displayNameFor(operator)
            .split(/\s+/)
            .map((part) => part[0])
            .join("")
            .slice(0, 2)
            .toUpperCase()
        ])
      ),
    [visibleOperators]
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
        <img
          alt=""
          aria-hidden="true"
          className={styles.logoMark}
          height={54}
          src="/secret-grounds-logo.png"
          width={61}
        />
      </div>
      <div className={styles.intro}>
        <p className={styles.eyebrow}>Family Operator Access</p>
        <h1 id="operator-title">Who&apos;s in charge right now?</h1>
        <p className={styles.subtitle}>Choose your name to open the shop dashboard.</p>
      </div>
      {error && !selectedAdmin ? (
        <div className={styles.error} role="alert">
          {error}
        </div>
      ) : null}
      <div className={styles.operatorGrid}>
        {visibleOperators.map((operator) => {
          // Only the singular protected Admin account gets the Admin-specific
          // wording/styling. A normal password operator (e.g. an Owner set to
          // Password Required) just says "Password required".
          const isProtectedAdmin = operator.user?.is_protected === true;
          const caption = isProtectedAdmin
            ? "Admin password required"
            : operator.password_required
              ? "Password required"
              : "Quick access";
          return (
            <button
              className={`${styles.operatorCard} ${isProtectedAdmin ? styles.adminCard : ""}`}
              disabled={isSubmitting}
              key={operator.operator_id}
              onClick={() => void chooseOperator(operator)}
              type="button"
            >
              <span className={styles.avatar}>{initials.get(operator.operator_id)}</span>
              <span>{displayNameFor(operator)}</span>
              <small>{caption}</small>
            </button>
          );
        })}
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
              <div className={styles.passwordField}>
                <input
                  autoComplete="current-password"
                  autoFocus
                  onChange={(event) => setAdminPassword(event.target.value)}
                  type={showPassword ? "text" : "password"}
                  value={adminPassword}
                />
                <button
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  aria-pressed={showPassword}
                  className={styles.passwordToggle}
                  onClick={() => setShowPassword((visible) => !visible)}
                  type="button"
                >
                  {showPassword ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
                </button>
              </div>
              {error ? (
                <span className={styles.dialogError} role="alert">
                  {error}
                </span>
              ) : null}
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

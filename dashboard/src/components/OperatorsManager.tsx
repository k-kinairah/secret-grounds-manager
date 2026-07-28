"use client";

import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  archiveAdminOperator,
  createAdminOperator,
  disableAdminOperator,
  enableAdminOperator,
  fetchAdminOperators,
  fetchOperatorActivity,
  resetAdminOperatorPassword,
  signOutAdminOperator,
  updateAdminOperator
} from "@/lib/api";
import { formatDateTime } from "@/lib/time";
import type { OperatorActivityRecord, OperatorCreateInput, OperatorManagementRecord, OperatorUpdateInput } from "@/lib/types";
import styles from "@/app/users/users.module.css";
import { useToast } from "./ToastProvider";

type ModalState =
  | { kind: "add" }
  | { kind: "edit"; operator: OperatorManagementRecord }
  | { kind: "password"; operator: OperatorManagementRecord }
  | null;

function normalizeUsername(value: string): string {
  return value.trim().replace(/^@/, "").replace(/^operator_/i, "").toLowerCase();
}

function accessLabel(operator: OperatorManagementRecord): string {
  return operator.password_required ? "Password Required" : "Quick Access";
}

function validatePassword(password: string, confirmation: string): string | null {
  if (password.length < 8) {
    return "Password must be at least 8 characters.";
  }
  if (password !== confirmation) {
    return "Passwords do not match.";
  }
  return null;
}

function humanizeAction(action: string): string {
  return action.replace(/_/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function activityContext(item: OperatorActivityRecord): string {
  return [item.pc_name, item.customer].filter(Boolean).join(" / ") || (item.category === "operator" ? "Dashboard sign-in" : "General");
}

function activityAmount(item: OperatorActivityRecord): string | null {
  return item.amount ? `PHP ${item.amount}` : null;
}

export function OperatorsManager() {
  const [operators, setOperators] = useState<OperatorManagementRecord[]>([]);
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState<string | null>("Loading operators...");
  const [modal, setModal] = useState<ModalState>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [activityOperator, setActivityOperator] = useState<OperatorManagementRecord | null>(null);
  const [activity, setActivity] = useState<OperatorActivityRecord[]>([]);
  const [activityMessage, setActivityMessage] = useState<string | null>(null);
  const [isActivityLoading, setIsActivityLoading] = useState(false);
  const drawerRef = useRef<HTMLElement | null>(null);
  const activityTriggerRefs = useRef(new Map<string, HTMLButtonElement>());
  const { pushToast } = useToast();

  const filteredOperators = useMemo(() => {
    const search = query.trim().toLowerCase();
    if (!search) {
      return operators;
    }
    return operators.filter((operator) =>
      [operator.display_name, operator.username, accessLabel(operator), operator.is_active ? "active" : "disabled"]
        .join(" ")
        .toLowerCase()
        .includes(search)
    );
  }, [operators, query]);
  const activeCount = operators.filter((operator) => operator.is_active && !operator.archived_at).length;

  async function refresh() {
    const records = await fetchAdminOperators();
    setOperators(records);
    setMessage(null);
  }

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

  useEffect(() => {
    if (activityOperator === null) {
      return;
    }
    const drawerOperatorId = activityOperator.operator_id;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    window.setTimeout(() => drawerRef.current?.querySelector<HTMLElement>("button")?.focus(), 0);
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setActivityOperator(null);
        activityTriggerRefs.current.get(drawerOperatorId)?.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previousFocus?.focus();
    };
  }, [activityOperator]);

  async function runAction(action: () => Promise<OperatorManagementRecord>, success: string) {
    if (isSaving) {
      return;
    }
    setIsSaving(true);
    setMessage(null);
    try {
      await action();
      await refresh();
      pushToast(success, "success");
    } catch (error) {
      const text = error instanceof Error ? error.message : "Operator action failed.";
      setMessage(text);
      pushToast(text, "error");
    } finally {
      setIsSaving(false);
    }
  }

  async function handleAdd(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving) {
      return;
    }
    const form = new FormData(event.currentTarget);
    const accessMethod = String(form.get("accessMethod")) as "quick" | "password";
    const password = String(form.get("password") ?? "");
    const confirmation = String(form.get("confirmPassword") ?? "");
    if (accessMethod === "password") {
      const passwordError = validatePassword(password, confirmation);
      if (passwordError) {
        setFormError(passwordError);
        return;
      }
    }
    const payload: OperatorCreateInput = {
      display_name: String(form.get("displayName") ?? "").trim(),
      username: normalizeUsername(String(form.get("username") ?? "")),
      access_method: accessMethod,
      password: accessMethod === "password" ? password : undefined,
      is_active: form.get("isActive") === "on"
    };
    setFormError(null);
    setIsSaving(true);
    try {
      await createAdminOperator(payload);
      await refresh();
      setModal(null);
      pushToast("Operator created.", "success");
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Could not create operator.");
    } finally {
      setIsSaving(false);
    }
  }

  async function handleEdit(event: FormEvent<HTMLFormElement>, operator: OperatorManagementRecord) {
    event.preventDefault();
    if (isSaving) {
      return;
    }
    const form = new FormData(event.currentTarget);
    const payload: OperatorUpdateInput = {
      display_name: String(form.get("displayName") ?? "").trim(),
      username: operator.is_protected ? "admin" : normalizeUsername(String(form.get("username") ?? "")),
      access_method: operator.is_protected ? "password" : String(form.get("accessMethod")) as "quick" | "password",
      is_active: operator.is_protected ? true : form.get("isActive") === "on"
    };
    if (operator.access_method === "quick" && payload.access_method === "password") {
      setFormError("Use Change Password before converting this operator to password access.");
      return;
    }
    if (operator.access_method === "password" && payload.access_method === "quick" && !window.confirm("Quick Access allows sign-in without a password from the shop dashboard. Continue?")) {
      return;
    }
    setFormError(null);
    setIsSaving(true);
    try {
      await updateAdminOperator(operator.operator_id, payload);
      await refresh();
      setModal(null);
      pushToast("Operator updated.", "success");
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Could not update operator.");
    } finally {
      setIsSaving(false);
    }
  }

  async function handleChangePassword(password: string, operator: OperatorManagementRecord) {
    if (isSaving) {
      return;
    }
    setFormError(null);
    setIsSaving(true);
    try {
      await resetAdminOperatorPassword(operator.operator_id, password);
      await refresh();
      setModal(null);
      pushToast(`Password updated for ${operator.display_name}.`, "success");
    } catch {
      setFormError("Could not update the password. Check the requirements and try again.");
    } finally {
      setIsSaving(false);
    }
  }

  function confirmDisable(operator: OperatorManagementRecord) {
    if (!window.confirm(`Disable ${operator.display_name}?\n\nThey will no longer be able to operate Secret Grounds until re-enabled.`)) {
      return;
    }
    void runAction(() => disableAdminOperator(operator.operator_id), "Operator disabled.");
  }

  function confirmArchive(operator: OperatorManagementRecord) {
    if (!window.confirm(`Archive ${operator.display_name}?\n\nHistorical activity will remain readable, but this operator will no longer appear for login.`)) {
      return;
    }
    void runAction(() => archiveAdminOperator(operator.operator_id), "Operator archived.");
  }

  async function openActivityDrawer(operator: OperatorManagementRecord) {
    document.querySelectorAll(`.${styles.staffMoreMenu}[open]`).forEach((menu) => menu.removeAttribute("open"));
    setActivityOperator(operator);
    setActivity([]);
    setActivityMessage(null);
    setIsActivityLoading(true);
    try {
      const records = await fetchOperatorActivity({ dateScope: "all", operatorId: operator.operator_id });
      setActivity(records);
    } catch (error) {
      setActivityMessage(error instanceof Error ? error.message : "Could not load operator activity.");
    } finally {
      setIsActivityLoading(false);
    }
  }

  function openPasswordModal(operator: OperatorManagementRecord) {
    document.querySelectorAll(`.${styles.staffMoreMenu}[open]`).forEach((menu) => menu.removeAttribute("open"));
    setActivityOperator(null);
    setFormError(null);
    setModal({ kind: "password", operator });
  }

  function confirmSignOut(operator: OperatorManagementRecord) {
    document.querySelectorAll(`.${styles.staffMoreMenu}[open]`).forEach((menu) => menu.removeAttribute("open"));
    if (!window.confirm(`Sign out ${operator.display_name}?\n\nTheir current Secret Grounds dashboard session will end. They can sign in again afterward.`)) {
      return;
    }
    void runAction(() => signOutAdminOperator(operator.operator_id), "Operator signed out.");
  }

  function setActivityTrigger(operatorId: string, node: HTMLButtonElement | null) {
    if (node) {
      activityTriggerRefs.current.set(operatorId, node);
    } else {
      activityTriggerRefs.current.delete(operatorId);
    }
  }

  return (
    <>
      <section className={styles.staffPanel}>
        <div className={styles.staffPageHeader}>
          <div>
            <h1>Staff</h1>
            <p>Manage who can operate Secret Grounds.</p>
            <span>{operators.length} operators / {activeCount} active</span>
          </div>
          <div className={styles.staffHeaderActions}>
            <label className={styles.staffSearch}>
              <span className="srOnly">Search operators</span>
              <input
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search operators"
                type="search"
                value={query}
              />
            </label>
            <button className={styles.primaryButton} onClick={() => { setFormError(null); setModal({ kind: "add" }); }} type="button">
              Add Operator
            </button>
          </div>
        </div>

        {message ? <div className={styles.message}>{message}</div> : null}

        <div className={styles.staffDirectoryHeader} aria-hidden="true">
          <span>Operator</span>
          <span>Access Method</span>
          <span>Status</span>
          <span>Last Login</span>
          <span>Actions</span>
        </div>
        <div className={styles.staffDirectory}>
          {filteredOperators.length === 0 ? (
            <div className={styles.emptyState}>No operators found.</div>
          ) : null}
          {filteredOperators.map((operator) => (
            <article className={styles.staffDirectoryRow} key={operator.operator_id}>
              <div className={styles.staffOperatorCell}>
                <span className={styles.staffAvatar} aria-hidden="true">
                  {operator.display_name.slice(0, 1)}
                </span>
                <div className={styles.staffIdentity}>
                  <strong>{operator.display_name}</strong>
                  {operator.is_protected ? <span className={styles.adminMiniBadge}>Administrator</span> : null}
                </div>
              </div>
              <span>{accessLabel(operator)}</span>
              <span className={operator.is_active ? styles.activeBadge : styles.disabledBadge}>
                {operator.is_active ? "Active" : "Disabled"}
              </span>
              <time>{operator.last_login_at ? formatDateTime(operator.last_login_at) : "Never signed in"}</time>
              <div className={styles.staffRowActions}>
                <button className={styles.secondaryButton} onClick={() => { setFormError(null); setModal({ kind: "edit", operator }); }} type="button">
                  Edit
                </button>
                <details className={styles.staffMoreMenu}>
                  <summary>More</summary>
                  <div role="menu">
                    <button
                      onClick={() => void openActivityDrawer(operator)}
                      ref={(node) => setActivityTrigger(operator.operator_id, node)}
                      role="menuitem"
                      type="button"
                    >
                      View Activity
                    </button>
                    {operator.password_required ? (
                      <button onClick={() => openPasswordModal(operator)} role="menuitem" type="button">
                        Change Password
                      </button>
                    ) : null}
                    {!operator.is_protected && operator.active_session_count > 0 ? (
                      <button onClick={() => confirmSignOut(operator)} role="menuitem" type="button">Sign Out Operator</button>
                    ) : null}
                    {!operator.is_protected && (operator.is_active || !operator.is_active) ? <hr /> : null}
                    {!operator.is_protected && operator.is_active ? (
                      <button onClick={() => confirmDisable(operator)} role="menuitem" type="button">Disable Operator</button>
                    ) : null}
                    {!operator.is_protected && !operator.is_active ? (
                      <button onClick={() => void runAction(() => enableAdminOperator(operator.operator_id), "Operator enabled.")} role="menuitem" type="button">Enable Operator</button>
                    ) : null}
                    {!operator.is_protected ? (
                      <>
                        <button className={styles.staffDangerAction} onClick={() => confirmArchive(operator)} role="menuitem" type="button">Archive Operator</button>
                      </>
                    ) : null}
                  </div>
                </details>
              </div>
            </article>
          ))}
        </div>
      </section>

      {modal?.kind === "add" ? (
        <OperatorFormDialog title="Add Operator" formError={formError} isSaving={isSaving} onClose={() => setModal(null)} onSubmit={handleAdd} />
      ) : null}

      {modal?.kind === "edit" ? (
        <OperatorFormDialog
          operator={modal.operator}
          title={modal.operator.is_protected ? "Edit Admin Profile" : "Edit Operator"}
          formError={formError}
          isSaving={isSaving}
          onClose={() => setModal(null)}
          onSubmit={(event) => void handleEdit(event, modal.operator)}
        />
      ) : null}

      {modal?.kind === "password" ? (
        <PasswordDialog
          formError={formError}
          isSaving={isSaving}
          operator={modal.operator}
          onClose={() => setModal(null)}
          onSubmit={(password) => void handleChangePassword(password, modal.operator)}
        />
      ) : null}

      {activityOperator ? (
        <div
          className={styles.staffDrawerBackdrop}
          onClick={() => setActivityOperator(null)}
          role="presentation"
        >
          <aside
            aria-label={`${activityOperator.display_name} operator activity`}
            aria-modal="true"
            className={styles.staffActivityDrawer}
            onClick={(event) => event.stopPropagation()}
            ref={drawerRef}
            role="dialog"
          >
            <div className={styles.staffDialogHeader}>
              <div>
                <h2>{activityOperator.display_name}</h2>
                <p>Operator Activity</p>
              </div>
              <button className={styles.secondaryButton} onClick={() => setActivityOperator(null)} type="button">Close</button>
            </div>
            <div className={styles.staffActivityMeta}>
              <span>{accessLabel(activityOperator)}</span>
              <span>{activityOperator.is_active ? "Active" : "Disabled"}</span>
              <span>{activityOperator.last_login_at ? formatDateTime(activityOperator.last_login_at) : "Never signed in"}</span>
            </div>
            {isActivityLoading ? <div className={styles.staffActivityEmpty}>Loading activity...</div> : null}
            {activityMessage ? <div className={styles.message}>{activityMessage}</div> : null}
            {!isActivityLoading && activity.length === 0 && activityMessage === null ? (
              <div className={styles.staffActivityEmpty}>No activity recorded for this operator yet.</div>
            ) : null}
            <div className={styles.staffActivityList}>
              {activity.map((item, index) => (
                <article className={styles.staffActivityItem} key={`${item.timestamp}-${item.action}-${index}`}>
                  <div>
                    <strong>{humanizeAction(item.action)}</strong>
                    <time>{formatDateTime(item.timestamp)}</time>
                  </div>
                  <p>{[activityContext(item), activityAmount(item)].filter(Boolean).join(" / ")}</p>
                  <span>{item.result ? humanizeAction(item.result) : "Completed"}</span>
                </article>
              ))}
            </div>
          </aside>
        </div>
      ) : null}
    </>
  );
}

function OperatorFormDialog({
  formError,
  isSaving,
  onClose,
  onSubmit,
  operator,
  title
}: {
  formError: string | null;
  isSaving: boolean;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  operator?: OperatorManagementRecord;
  title: string;
}) {
  const isProtected = operator?.is_protected ?? false;
  const [accessMethod, setAccessMethod] = useState<"quick" | "password">(operator?.access_method ?? "quick");
  return (
    <div className={styles.overlay} role="presentation">
      <form aria-label={title} aria-modal="true" className={styles.staffDialog} onSubmit={onSubmit} role="dialog">
        <div className={styles.staffDialogHeader}>
          <h2>{title}</h2>
          <button aria-label="Close operator dialog" className={styles.secondaryButton} onClick={onClose} type="button">Close</button>
        </div>
        {formError ? <div className={styles.message}>{formError}</div> : null}
        <label className={styles.field}>
          Display Name
          <input defaultValue={operator?.display_name ?? ""} name="displayName" required />
        </label>
        <label className={styles.field}>
          Username
          <input defaultValue={operator?.username ?? ""} disabled={isProtected} name="username" pattern="[A-Za-z0-9_@]{3,40}" required />
        </label>
        <label className={styles.field}>
          Access Method
          <select disabled={isProtected} name="accessMethod" onChange={(event) => setAccessMethod(event.target.value as "quick" | "password")} value={isProtected ? "password" : accessMethod}>
            <option value="quick">Quick Access</option>
            <option value="password">Password Required</option>
          </select>
        </label>
        {accessMethod === "quick" && !isProtected ? (
          <p className={styles.staffWarning}>Quick Access allows this operator to sign in without a password from the shop dashboard.</p>
        ) : null}
        {!operator && accessMethod === "password" ? (
          <>
            <label className={styles.field}>
              Password
              <input autoComplete="new-password" name="password" required type="password" />
            </label>
            <label className={styles.field}>
              Confirm Password
              <input autoComplete="new-password" name="confirmPassword" required type="password" />
            </label>
          </>
        ) : null}
        <label className={styles.checkboxRow}>
          <input defaultChecked={operator?.is_active ?? true} disabled={isProtected} name="isActive" type="checkbox" />
          Active
        </label>
        <button className={styles.primaryButton} disabled={isSaving} type="submit">
          {isSaving ? "Saving..." : "Save Operator"}
        </button>
      </form>
    </div>
  );
}

function PasswordDialog({
  formError,
  isSaving,
  onClose,
  onSubmit,
  operator
}: {
  formError: string | null;
  isSaving: boolean;
  onClose: () => void;
  onSubmit: (password: string) => void;
  operator: OperatorManagementRecord;
}) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [touched, setTouched] = useState({ password: false, confirmation: false });
  const dialogRef = useRef<HTMLFormElement | null>(null);
  const passwordRef = useRef<HTMLInputElement | null>(null);
  const passwordError = password.length > 0 && password.length < 8 ? "Password must be at least 8 characters." : "";
  const confirmationError = confirmation.length > 0 && password !== confirmation ? "Passwords do not match." : "";
  const canSave = password.length >= 8 && password === confirmation && !isSaving;

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    window.setTimeout(() => passwordRef.current?.focus(), 0);
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !isSaving) {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      const dialog = dialogRef.current;
      if (!dialog) {
        return;
      }
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>("input, button, [href], [tabindex]:not([tabindex='-1'])")
      ).filter((element) => !element.hasAttribute("disabled"));
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) {
        return;
      }
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previousFocus?.focus();
    };
  }, [isSaving, onClose]);

  function submitPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTouched({ password: true, confirmation: true });
    if (!canSave) {
      return;
    }
    onSubmit(password);
  }

  return (
    <div className={styles.overlay} aria-hidden={false} role="presentation">
      <form
        aria-describedby="change-password-description change-password-requirements"
        aria-labelledby="change-password-title"
        aria-modal="true"
        className={`${styles.staffDialog} ${styles.passwordDialog}`}
        onSubmit={submitPassword}
        ref={dialogRef}
        role="dialog"
      >
        <div className={styles.passwordDialogHeader}>
          <div>
            <h2 id="change-password-title">Change Password</h2>
            <p id="change-password-description">Set a new password for {operator.display_name}.</p>
          </div>
          <button
            aria-label="Close change password dialog"
            className={styles.staffCloseButton}
            disabled={isSaving}
            onClick={onClose}
            type="button"
          >
            X
          </button>
        </div>
        {formError ? <div className={styles.passwordError} role="alert">{formError}</div> : null}
        <p className={styles.passwordHelper}>The current password cannot be viewed. Saving will replace it with the new password.</p>
        <p className={styles.passwordSessionNote}>
          {operator.is_protected
            ? "You will be signed out after changing your password."
            : "Active dashboard sessions for this operator will be ended. Customer PC sessions and billing are not affected."}
        </p>
        <p className={styles.passwordRequirements} id="change-password-requirements">At least 8 characters.</p>
        <label className={styles.passwordField}>
          New Password
          <div>
            <input
              autoComplete="new-password"
              name="new-password"
              onBlur={() => setTouched((value) => ({ ...value, password: true }))}
              onChange={(event) => setPassword(event.target.value)}
              ref={passwordRef}
              type={showPassword ? "text" : "password"}
              value={password}
            />
            <button onClick={() => setShowPassword((value) => !value)} type="button">
              {showPassword ? "Hide" : "Show"}
            </button>
          </div>
          {touched.password && password.length === 0 ? <span role="alert">Password is required.</span> : null}
          {touched.password && passwordError ? <span role="alert">{passwordError}</span> : null}
        </label>
        <label className={styles.passwordField}>
          Confirm New Password
          <div>
            <input
              autoComplete="new-password"
              name="confirm-new-password"
              onBlur={() => setTouched((value) => ({ ...value, confirmation: true }))}
              onChange={(event) => setConfirmation(event.target.value)}
              type={showPassword ? "text" : "password"}
              value={confirmation}
            />
            <button onClick={() => setShowPassword((value) => !value)} type="button">
              {showPassword ? "Hide" : "Show"}
            </button>
          </div>
          {touched.confirmation && confirmation.length === 0 ? <span role="alert">Confirm the new password.</span> : null}
          {touched.confirmation && confirmationError ? <span role="alert">{confirmationError}</span> : null}
        </label>
        <div className={styles.passwordDialogActions}>
          <button className={styles.secondaryButton} disabled={isSaving} onClick={onClose} type="button">Cancel</button>
          <button className={styles.primaryButton} disabled={!canSave} type="submit">
            {isSaving ? "Saving..." : "Save Password"}
          </button>
        </div>
      </form>
    </div>
  );
}

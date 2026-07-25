"use client";

import { useEffect, useState } from "react";
import {
  createUser,
  fetchUsers,
  resetUserPassword,
  updateUser,
  updateUserStatus
} from "@/lib/api";
import type { UserAdminRecord, UserRole } from "@/lib/types";
import styles from "@/app/users/users.module.css";

type MessageKind = "success" | "error";

interface UserFormState {
  username: string;
  displayName: string;
  password: string;
  role: UserRole;
}

const emptyForm: UserFormState = {
  username: "",
  displayName: "",
  password: "",
  role: "cashier"
};

const roleOptions: UserRole[] = ["cashier", "admin", "owner"];

export function UsersManager() {
  const [users, setUsers] = useState<UserAdminRecord[]>([]);
  const [form, setForm] = useState<UserFormState>(emptyForm);
  const [editingUserId, setEditingUserId] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: MessageKind; text: string } | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [pendingUserId, setPendingUserId] = useState<string | null>(null);

  async function refresh(clearMessage = true) {
    try {
      setUsers(await fetchUsers());
      if (clearMessage) {
        setMessage(null);
      }
    } catch (error) {
      setMessage({
        kind: "error",
        text: error instanceof Error ? error.message : "Could not load users."
      });
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    const initialFetchId = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(initialFetchId);
  }, []);

  function editUser(user: UserAdminRecord) {
    setEditingUserId(user.user_id);
    setForm({
      username: user.username,
      displayName: user.display_name,
      password: "",
      role: user.role
    });
    setMessage(null);
  }

  async function saveUser() {
    if (isSaving) {
      return;
    }

    setIsSaving(true);
    try {
      if (editingUserId) {
        await updateUser(editingUserId, {
          username: form.username.trim(),
          display_name: form.displayName.trim(),
          role: form.role
        });
        if (form.password.trim()) {
          await resetUserPassword(editingUserId, form.password);
        }
        setMessage({ kind: "success", text: "User updated." });
      } else {
        await createUser({
          username: form.username.trim(),
          display_name: form.displayName.trim(),
          password: form.password,
          role: form.role
        });
        setMessage({ kind: "success", text: "User created." });
      }
      setEditingUserId(null);
      setForm(emptyForm);
      await refresh(false);
    } catch (error) {
      setMessage({
        kind: "error",
        text: error instanceof Error ? error.message : "Could not save user."
      });
    } finally {
      setIsSaving(false);
    }
  }

  async function toggleUser(user: UserAdminRecord) {
    setPendingUserId(user.user_id);
    try {
      await updateUserStatus(user.user_id, !user.is_active);
      setMessage({
        kind: "success",
        text: user.is_active ? "User disabled." : "User enabled."
      });
      await refresh(false);
    } catch (error) {
      setMessage({
        kind: "error",
        text: error instanceof Error ? error.message : "Could not update user status."
      });
    } finally {
      setPendingUserId(null);
    }
  }

  return (
    <>
      {message ? (
        <div
          className={
            message.kind === "success"
              ? `${styles.message} ${styles.success}`
              : `${styles.message} ${styles.error}`
          }
          role={message.kind === "error" ? "alert" : "status"}
        >
          {message.text}
        </div>
      ) : null}
      <section className={styles.grid}>
        <div className={styles.panel}>
          <h2>{editingUserId ? "Edit User" : "Create User"}</h2>
          <label className={styles.field}>
            Username
            <input
              onChange={(event) => setForm({ ...form, username: event.target.value })}
              placeholder="cashier01"
              value={form.username}
            />
          </label>
          <label className={styles.field}>
            Display name
            <input
              onChange={(event) => setForm({ ...form, displayName: event.target.value })}
              placeholder="Cashier One"
              value={form.displayName}
            />
          </label>
          <label className={styles.field}>
            {editingUserId ? "New password" : "Password"}
            <input
              onChange={(event) => setForm({ ...form, password: event.target.value })}
              placeholder={editingUserId ? "Leave blank to keep current password" : "At least 8 characters"}
              type="password"
              value={form.password}
            />
          </label>
          <label className={styles.field}>
            Role
            <select
              onChange={(event) => setForm({ ...form, role: event.target.value as UserRole })}
              value={form.role}
            >
              {roleOptions.map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
          </label>
          <div className={styles.actions}>
            <button className={styles.primaryButton} disabled={isSaving} onClick={saveUser} type="button">
              {isSaving ? "Saving..." : editingUserId ? "Save User" : "Create User"}
            </button>
            {editingUserId ? (
              <button
                className={styles.secondaryButton}
                onClick={() => {
                  setEditingUserId(null);
                  setForm(emptyForm);
                }}
                type="button"
              >
                Cancel
              </button>
            ) : null}
          </div>
        </div>

        <div className={styles.panelWide}>
          <h2>Staff Accounts</h2>
          {isLoading ? (
            <div className={styles.loadingCard}>Loading users...</div>
          ) : users.length === 0 ? (
            <div className={styles.emptyState}>No users yet.</div>
          ) : (
            <div className={styles.userList}>
              {users.map((user) => (
                <article className={styles.userCard} key={user.user_id}>
                  <div>
                    <div className={styles.userTitleRow}>
                      <h3>{user.display_name}</h3>
                      <span className={user.is_active ? styles.activeBadge : styles.disabledBadge}>
                        {user.is_active ? "Active" : "Disabled"}
                      </span>
                    </div>
                    <p>
                      @{user.username} - {user.role}
                    </p>
                    <p>Last login: {user.last_login_at ?? "Never"}</p>
                  </div>
                  <div className={styles.cardActions}>
                    <button className={styles.secondaryButton} onClick={() => editUser(user)} type="button">
                      Edit
                    </button>
                    <button
                      className={styles.secondaryButton}
                      disabled={pendingUserId === user.user_id}
                      onClick={() => toggleUser(user)}
                      type="button"
                    >
                      {pendingUserId === user.user_id
                        ? "Updating..."
                        : user.is_active
                          ? "Disable"
                          : "Enable"}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
      </section>
    </>
  );
}

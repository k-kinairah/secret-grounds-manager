"use client";

import { useEffect, useState } from "react";
import { createRate, fetchRates, updateRate, updateRateStatus } from "@/lib/api";
import { saveRateProfile } from "@/lib/rateActions";
import { emptyRateForm, rateToForm, type RateFormState } from "@/lib/rateForm";
import { formatMoney, formatRateDuration } from "@/lib/time";
import type { RateProfile } from "@/lib/types";
import styles from "@/app/rates/rates.module.css";

type MessageKind = "success" | "error";

interface StatusMessage {
  kind: MessageKind;
  text: string;
}

export function RatesManager() {
  const [rates, setRates] = useState<RateProfile[]>([]);
  const [editingRateId, setEditingRateId] = useState<string | null>(null);
  const [form, setForm] = useState<RateFormState>(emptyRateForm);
  const [message, setMessage] = useState<StatusMessage | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [pendingRateId, setPendingRateId] = useState<string | null>(null);
  const meteredRate = rates.find((rate) => rate.rate_type === "metered") ?? null;
  const packageRates = rates.filter((rate) => rate.rate_type === "package");

  async function refresh(options: { clearMessage?: boolean } = {}) {
    try {
      setRates(await fetchRates(true));
      if (options.clearMessage ?? true) {
        setMessage(null);
      }
    } catch (error) {
      setMessage({
        kind: "error",
        text: error instanceof Error ? error.message : "Could not load rates."
      });
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    const initialFetchId = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(initialFetchId);
  }, []);

  function editRate(rate: RateProfile) {
    setEditingRateId(rate.rate_id);
    setForm(rateToForm(rate));
    setMessage(null);
  }

  async function saveRate() {
    if (isSaving) {
      return;
    }

    setIsSaving(true);
    try {
      const successText = await saveRateProfile({
        editingRateId,
        form,
        createRateProfile: createRate,
        updateRateProfile: updateRate,
        refreshRates: () => refresh({ clearMessage: false })
      });
      setMessage({ kind: "success", text: successText });
      setEditingRateId(null);
      setForm(emptyRateForm);
    } catch (error) {
      setMessage({
        kind: "error",
        text: error instanceof Error ? error.message : "Could not save rate."
      });
    } finally {
      setIsSaving(false);
    }
  }

  async function toggleRate(rate: RateProfile) {
    setPendingRateId(rate.rate_id);
    try {
      await updateRateStatus(rate.rate_id, !rate.is_active);
      setMessage({
        kind: "success",
        text: rate.is_active ? "Rate disabled." : "Rate enabled."
      });
      await refresh({ clearMessage: false });
    } catch (error) {
      setMessage({
        kind: "error",
        text: error instanceof Error ? error.message : "Could not change rate status."
      });
    } finally {
      setPendingRateId(null);
    }
  }

  const formTitle = editingRateId
    ? form.rateType === "metered"
      ? "Edit Metered Rate"
      : "Edit Package"
    : "Create Package";

  return (
    <>
      {message ? (
        <div
          className={
            message.kind === "success"
              ? `${styles.message} ${styles.successMessage}`
              : `${styles.message} ${styles.errorMessage}`
          }
          role={message.kind === "error" ? "alert" : "status"}
        >
          {message.text}
        </div>
      ) : null}
      <section className={styles.grid}>
        <div className={styles.panel}>
          <h2>{formTitle}</h2>
          <p className={styles.panelText}>
            {form.rateType === "metered"
              ? "Configure how much money buys how much time."
              : "Configure fixed packages for cashier session starts and extensions."}
          </p>
          <div className={styles.form}>
            <label className={styles.field}>
              Name
              <input
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                value={form.name}
              />
            </label>
            <label className={styles.field}>
              {form.rateType === "metered" ? "Time minutes" : "Duration minutes"}
              <input
                min="1"
                onChange={(event) => setForm({ ...form, minutes: event.target.value })}
                type="number"
                value={form.minutes}
              />
            </label>
            <label className={styles.field}>
              {form.rateType === "metered" ? "Amount" : "Price"}
              <input
                inputMode="decimal"
                onChange={(event) => setForm({ ...form, price: event.target.value })}
                placeholder="0.00"
                value={form.price}
              />
            </label>
            {form.rateType === "package" ? (
              <label className={styles.field}>
                Sort order
                <input
                  onChange={(event) => setForm({ ...form, sortOrder: event.target.value })}
                  type="number"
                  value={form.sortOrder}
                />
              </label>
            ) : null}
            <div className={styles.actions}>
              <button className={styles.button} disabled={isSaving} onClick={saveRate}>
                {isSaving ? "Saving..." : "Save Rate"}
              </button>
              {editingRateId ? (
                <button
                  className={styles.secondaryButton}
                  onClick={() => {
                    setEditingRateId(null);
                    setForm(emptyRateForm);
                  }}
                  type="button"
                >
                  Cancel
                </button>
              ) : null}
            </div>
          </div>
        </div>

        <div className={styles.panelWide}>
          <div className={styles.sectionHeader}>
            <div>
              <h2>Regular Metered Rate</h2>
              <p className={styles.panelText}>Used when the cashier enters an amount.</p>
            </div>
          </div>
          {isLoading ? (
            <div className={styles.loadingCard}>Loading metered rate...</div>
          ) : meteredRate === null ? (
            <div className={styles.emptyState}>No metered rate configured.</div>
          ) : (
            <article className={styles.meteredCard}>
              <div>
                <span
                  className={
                    meteredRate.is_active
                      ? `${styles.statusBadge} ${styles.statusActive}`
                      : `${styles.statusBadge} ${styles.statusDisabled}`
                  }
                >
                  {meteredRate.is_active ? "Enabled" : "Disabled"}
                </span>
                <h3>{meteredRate.name}</h3>
                <p>
                  {formatMoney(meteredRate.price)} buys{" "}
                  {formatRateDuration(meteredRate.duration_seconds)}
                </p>
              </div>
              <div className={styles.cardActions}>
                <button
                  className={styles.secondaryButton}
                  onClick={() => editRate(meteredRate)}
                  type="button"
                >
                  Edit
                </button>
                {!meteredRate.is_active ? (
                  <button
                    className={styles.secondaryButton}
                    disabled={pendingRateId === meteredRate.rate_id}
                    onClick={() => toggleRate(meteredRate)}
                    type="button"
                  >
                    Enable
                  </button>
                ) : null}
              </div>
            </article>
          )}

          <div className={styles.sectionHeader}>
            <div>
              <h2>Packages</h2>
              <p className={styles.panelText}>Fixed packages shown on the Packages tab.</p>
            </div>
          </div>
          {isLoading ? (
            <div className={styles.rateGrid} aria-label="Loading package rates">
              {Array.from({ length: 4 }, (_, index) => (
                <div className={styles.loadingCard} key={index}>
                  Loading package...
                </div>
              ))}
            </div>
          ) : packageRates.length === 0 ? (
            <div className={styles.emptyState}>No package rates yet.</div>
          ) : (
            <div className={styles.rateGrid}>
              {packageRates.map((rate) => (
                <article className={styles.rateCard} key={rate.rate_id}>
                  <div className={styles.rateTopLine}>
                    <span
                      className={
                        rate.is_active
                          ? `${styles.statusBadge} ${styles.statusActive}`
                          : `${styles.statusBadge} ${styles.statusDisabled}`
                      }
                    >
                      {rate.is_active ? "Enabled" : "Disabled"}
                    </span>
                    <span className={styles.orderPill}>Order {rate.sort_order}</span>
                  </div>
                  <h3>{rate.name}</h3>
                  <div className={styles.rateMeta}>
                    <span>{formatRateDuration(rate.duration_seconds)}</span>
                    <strong>{formatMoney(rate.price)}</strong>
                  </div>
                  <div className={styles.cardActions}>
                    <button
                      className={styles.secondaryButton}
                      onClick={() => editRate(rate)}
                      type="button"
                    >
                      Edit
                    </button>
                    <button
                      className={styles.secondaryButton}
                      disabled={pendingRateId === rate.rate_id}
                      onClick={() => toggleRate(rate)}
                      type="button"
                    >
                      {pendingRateId === rate.rate_id
                        ? "Updating..."
                        : rate.is_active
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

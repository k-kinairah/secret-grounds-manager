"use client";

import { useEffect, useState } from "react";
import {
  createRate,
  fetchRates,
  updateRate,
  updateRateStatus
} from "@/lib/api";
import { formatDuration, formatMoney, minutesToSeconds } from "@/lib/time";
import type { RateProfile, RateProfileInput } from "@/lib/types";
import styles from "@/app/rates/rates.module.css";

interface RateFormState {
  name: string;
  minutes: string;
  price: string;
  sortOrder: string;
}

const emptyForm: RateFormState = {
  name: "",
  minutes: "30",
  price: "0.00",
  sortOrder: "0"
};

export function RatesManager() {
  const [rates, setRates] = useState<RateProfile[]>([]);
  const [editingRateId, setEditingRateId] = useState<string | null>(null);
  const [form, setForm] = useState<RateFormState>(emptyForm);
  const [message, setMessage] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  async function refresh() {
    try {
      setRates(await fetchRates(true));
      setMessage(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load rates.");
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  function editRate(rate: RateProfile) {
    setEditingRateId(rate.rate_id);
    setForm({
      name: rate.name,
      minutes: String(rate.duration_seconds / 60),
      price: rate.price,
      sortOrder: String(rate.sort_order)
    });
  }

  function toInput(): RateProfileInput {
    return {
      name: form.name,
      duration_seconds: minutesToSeconds(Number(form.minutes)),
      price: form.price,
      sort_order: Number(form.sortOrder)
    };
  }

  async function saveRate() {
    setIsSaving(true);
    try {
      if (editingRateId) {
        await updateRate(editingRateId, toInput());
        setMessage("Rate updated.");
      } else {
        await createRate({ ...toInput(), is_active: true });
        setMessage("Rate created.");
      }
      setEditingRateId(null);
      setForm(emptyForm);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not save rate.");
    } finally {
      setIsSaving(false);
    }
  }

  async function toggleRate(rate: RateProfile) {
    try {
      await updateRateStatus(rate.rate_id, !rate.is_active);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not change rate status.");
    }
  }

  return (
    <>
      {message ? <div className={styles.message}>{message}</div> : null}
      <section className={styles.grid}>
        <div className={styles.panel}>
          <h2>{editingRateId ? "Edit Rate" : "Create Rate"}</h2>
          <div className={styles.form}>
            <label className={styles.field}>
              Name
              <input
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                value={form.name}
              />
            </label>
            <label className={styles.field}>
              Duration minutes
              <input
                min="1"
                onChange={(event) => setForm({ ...form, minutes: event.target.value })}
                type="number"
                value={form.minutes}
              />
            </label>
            <label className={styles.field}>
              Price
              <input
                inputMode="decimal"
                onChange={(event) => setForm({ ...form, price: event.target.value })}
                placeholder="0.00"
                value={form.price}
              />
            </label>
            <label className={styles.field}>
              Sort order
              <input
                onChange={(event) => setForm({ ...form, sortOrder: event.target.value })}
                type="number"
                value={form.sortOrder}
              />
            </label>
            <div className={styles.actions}>
              <button className={styles.button} disabled={isSaving} onClick={saveRate}>
                {isSaving ? "Saving..." : "Save Rate"}
              </button>
              {editingRateId ? (
                <button
                  className={styles.secondaryButton}
                  onClick={() => {
                    setEditingRateId(null);
                    setForm(emptyForm);
                  }}
                >
                  Cancel
                </button>
              ) : null}
            </div>
          </div>
        </div>
        <div className={styles.panel}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Name</th>
                <th>Duration</th>
                <th>Price</th>
                <th>Order</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rates.map((rate) => (
                <tr key={rate.rate_id}>
                  <td>{rate.name}</td>
                  <td>{formatDuration(rate.duration_seconds)}</td>
                  <td>{formatMoney(rate.price)}</td>
                  <td>{rate.sort_order}</td>
                  <td>{rate.is_active ? "Active" : "Disabled"}</td>
                  <td>
                    <div className={styles.actions}>
                      <button className={styles.secondaryButton} onClick={() => editRate(rate)}>
                        Edit
                      </button>
                      <button className={styles.secondaryButton} onClick={() => toggleRate(rate)}>
                        {rate.is_active ? "Disable" : "Enable"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

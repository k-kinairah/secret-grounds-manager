"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { archiveShopBill, createShopBill, fetchShopBills } from "@/lib/api";
import { formatMoney } from "@/lib/time";
import type { BillKind, ShopBill } from "@/lib/types";
import styles from "@/app/sales/sales.module.css";
import pageStyles from "@/app/page.module.css";
import { CloseButton } from "./CloseButton";
import { SecretSelect, type SecretSelectOption } from "./SecretSelect";

const KIND_OPTIONS: SecretSelectOption[] = [
  { label: "Enter amount each month", value: "variable" },
  { label: "Same amount every month", value: "fixed" }
];

function isValidAmount(value: string): boolean {
  const n = Number(value);
  return value.trim() !== "" && Number.isFinite(n) && n > 0;
}

export function ManageBillsModal({
  onChanged,
  onClose
}: {
  onChanged: () => void;
  onClose: () => void;
}) {
  const [bills, setBills] = useState<ShopBill[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<BillKind>("variable");
  const [amount, setAmount] = useState("");

  const load = useCallback(async () => {
    try {
      setBills(await fetchShopBills());
      setMessage(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load bills.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function run(action: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true);
    try {
      await action();
      await load();
      onChanged();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  const activeBills = useMemo(() => bills.filter((bill) => bill.archived_at === null), [bills]);
  const archivedBills = useMemo(() => bills.filter((bill) => bill.archived_at !== null), [bills]);
  const canAdd = name.trim() !== "" && (kind === "variable" || isValidAmount(amount));

  return (
    <div className={styles.transactionModalOverlay} onClick={onClose} role="presentation">
      <div
        aria-label="Manage bills"
        aria-modal="true"
        className={`${styles.transactionModal} ${styles.manageBillsModal}`}
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <div className={styles.transactionModalHeader}>
          <div>
            <h2>Manage bills</h2>
            <span>Recurring shop expenses</span>
          </div>
          <CloseButton
            className={pageStyles.iconButtonPremiumClose}
            label="Close manage bills"
            onClick={onClose}
          />
        </div>

        {message ? <div className={styles.netIncomeMessage}>{message}</div> : null}

        <div className={styles.netIncomeManageList}>
          {activeBills.length === 0 && archivedBills.length === 0 ? (
            <div className={styles.miniEmpty}>No bills yet.</div>
          ) : null}
          {activeBills.map((bill) => (
            <div className={styles.netIncomeManageRow} key={bill.bill_id}>
              <div>
                <strong>{bill.name}</strong>
                <span className={styles.netIncomeHint}>
                  {bill.kind === "fixed"
                    ? `${formatMoney(bill.fixed_amount)} every month`
                    : "amount entered each month"}
                </span>
              </div>
              <button
                className={styles.netIncomeSubtleButton}
                disabled={busy}
                onClick={() => void run(() => archiveShopBill(bill.bill_id))}
                type="button"
              >
                Archive
              </button>
            </div>
          ))}
          {archivedBills.map((bill) => (
            <div
              className={`${styles.netIncomeManageRow} ${styles.netIncomeArchived}`}
              key={bill.bill_id}
            >
              <div>
                <strong>{bill.name}</strong>
                <span className={styles.netIncomeHint}>Archived</span>
              </div>
            </div>
          ))}
        </div>

        <div className={styles.manageBillsAddForm}>
          <h3>Add a bill</h3>
          <input
            aria-label="New bill name"
            className={styles.manageBillsInput}
            onChange={(event) => setName(event.target.value)}
            placeholder="Bill name (e.g. Meralco)"
            value={name}
          />
          <SecretSelect
            label="How it's billed"
            onChange={(value) => setKind(value as BillKind)}
            options={KIND_OPTIONS}
            value={kind}
          />
          {kind === "fixed" ? (
            <input
              aria-label="Fixed amount"
              className={styles.manageBillsInput}
              inputMode="decimal"
              onChange={(event) => setAmount(event.target.value)}
              placeholder="₱0.00"
              value={amount}
            />
          ) : null}
          <button
            className={styles.manageBillsAddButton}
            disabled={busy || !canAdd}
            onClick={() =>
              void run(async () => {
                await createShopBill({
                  name: name.trim(),
                  kind,
                  ...(kind === "fixed" ? { fixed_amount: Number(amount).toFixed(2) } : {})
                });
                setName("");
                setAmount("");
                setKind("variable");
              })
            }
            type="button"
          >
            Add bill
          </button>
        </div>
      </div>
    </div>
  );
}

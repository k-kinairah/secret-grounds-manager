"use client";

import { useCallback, useEffect, useState } from "react";
import {
  addOneOffExpense,
  clearShopBillAmount,
  fetchFinancesSummary,
  setShopBillAmount,
  voidOneOffExpense
} from "@/lib/api";
import { formatMoney } from "@/lib/time";
import type { FinancesMonthSummary } from "@/lib/types";
import styles from "@/app/sales/sales.module.css";
import pageStyles from "@/app/page.module.css";
import { CloseButton } from "./CloseButton";
import { ManageBillsModal } from "./ManageBillsModal";

function currentMonthKey(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function shiftMonth(month: string, delta: number): string {
  const [year, mon] = month.split("-").map(Number);
  const d = new Date(year, mon - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(month: string): string {
  const [year, mon] = month.split("-").map(Number);
  return new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(
    new Date(year, mon - 1, 1)
  );
}

function isValidAmount(value: string): boolean {
  const n = Number(value);
  return value.trim() !== "" && Number.isFinite(n) && n > 0;
}

export function NetIncomeModal({ onClose }: { onClose: () => void }) {
  const [month, setMonth] = useState(currentMonthKey);
  const [summary, setSummary] = useState<FinancesMonthSummary | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [showManage, setShowManage] = useState(false);

  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [oneOffName, setOneOffName] = useState("");
  const [oneOffAmount, setOneOffAmount] = useState("");

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      const nextSummary = await fetchFinancesSummary(month);
      setSummary(nextSummary);
      setDrafts(
        Object.fromEntries(
          nextSummary.lines
            .filter((line) => line.source === "variable" && line.bill_id)
            .map((line) => [line.bill_id as string, line.amount ?? ""])
        )
      );
      setMessage(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load net income.");
    } finally {
      setIsLoading(false);
    }
  }, [month]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [onClose]);

  async function run(action: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true);
    try {
      await action();
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  const netIsNegative = summary !== null && Number(summary.net_income) < 0;

  return (
    <>
    <div className={styles.transactionModalOverlay} onClick={onClose} role="presentation">
      <div
        aria-label="Net income"
        aria-modal="true"
        className={`${styles.transactionModal} ${styles.netIncomeModal}`}
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <div className={styles.transactionModalHeader}>
          <div>
            <h2>Net Income</h2>
            <div className={styles.netIncomeMonthNav}>
              <button
                aria-label="Previous month"
                onClick={() => setMonth((current) => shiftMonth(current, -1))}
                type="button"
              >
                ‹
              </button>
              <span>{monthLabel(month)}</span>
              <button
                aria-label="Next month"
                onClick={() => setMonth((current) => shiftMonth(current, 1))}
                type="button"
              >
                ›
              </button>
            </div>
          </div>
          <CloseButton
            className={pageStyles.iconButtonPremiumClose}
            label="Close net income"
            onClick={onClose}
          />
        </div>

        {message ? <div className={styles.netIncomeMessage}>{message}</div> : null}

        {isLoading || summary === null ? (
          <div className={styles.miniEmpty}>Loading…</div>
        ) : (
          <>
            <div className={styles.netIncomeIncomeRow}>
              <div>
                <strong>Income</strong>
                <small>Net sales collected this month</small>
              </div>
              <b>{formatMoney(summary.income)}</b>
            </div>

            <div className={styles.netIncomeSection}>
              <div className={styles.netIncomeBillsHeader}>
                <h3>Bills</h3>
                <button
                  className={styles.netIncomeManageButton}
                  onClick={() => setShowManage(true)}
                  type="button"
                >
                  Manage bills
                </button>
              </div>
              <div className={styles.netIncomeBills}>
                {summary.lines.length === 0 ? (
                  <div className={styles.miniEmpty}>No bills yet. Add one with “Manage bills”.</div>
                ) : null}

                {summary.lines.map((line) => {
                  if (line.source === "fixed") {
                    return (
                      <div className={styles.netIncomeBillRow} key={`fixed-${line.bill_id}`}>
                        <div className={styles.netIncomeBillName}>
                          <strong>{line.label}</strong>
                        </div>
                        <b>{formatMoney(line.amount)}</b>
                      </div>
                    );
                  }
                  if (line.source === "variable") {
                    const billId = line.bill_id as string;
                    const draft = drafts[billId] ?? "";
                    const changed = draft.trim() !== (line.amount ?? "");
                    return (
                      <div className={styles.netIncomeBillRow} key={`var-${billId}`}>
                        <div className={styles.netIncomeBillName}>
                          <strong>{line.label}</strong>
                        </div>
                        <div className={styles.netIncomeVarControls}>
                          <input
                            aria-label={`${line.label} amount`}
                            className={styles.netIncomeAmountInput}
                            inputMode="decimal"
                            onChange={(event) =>
                              setDrafts((current) => ({ ...current, [billId]: event.target.value }))
                            }
                            placeholder="₱0.00"
                            value={draft}
                          />
                          <button
                            disabled={busy || !changed || !isValidAmount(draft)}
                            onClick={() =>
                              void run(() => setShopBillAmount(billId, month, Number(draft).toFixed(2)))
                            }
                            type="button"
                          >
                            Save
                          </button>
                          {line.applied ? (
                            <button
                              className={styles.netIncomeSubtleButton}
                              disabled={busy}
                              onClick={() => void run(() => clearShopBillAmount(billId, month))}
                              type="button"
                            >
                              Clear
                            </button>
                          ) : null}
                        </div>
                      </div>
                    );
                  }
                  return (
                    <div className={styles.netIncomeBillRow} key={`one-${line.amount_id}`}>
                      <div className={styles.netIncomeBillName}>
                        <strong>{line.label}</strong>
                      </div>
                      <div className={styles.netIncomeVarControls}>
                        <b>{formatMoney(line.amount)}</b>
                        <button
                          aria-label={`Remove ${line.label}`}
                          className={styles.netIncomeSubtleButton}
                          disabled={busy}
                          onClick={() => void run(() => voidOneOffExpense(line.amount_id as string))}
                          type="button"
                        >
                          Remove
                        </button>
                      </div>
                    </div>
                  );
                })}

                <div className={styles.netIncomeAddOneOff}>
                  <input
                    aria-label="One-off expense name"
                    className={styles.netIncomeNameInput}
                    onChange={(event) => setOneOffName(event.target.value)}
                    placeholder="Expense"
                    value={oneOffName}
                  />
                  <input
                    aria-label="One-off expense amount"
                    className={styles.netIncomeAmountInput}
                    inputMode="decimal"
                    onChange={(event) => setOneOffAmount(event.target.value)}
                    placeholder="₱0.00"
                    value={oneOffAmount}
                  />
                  <button
                    disabled={busy || oneOffName.trim() === "" || !isValidAmount(oneOffAmount)}
                    onClick={() =>
                      void run(async () => {
                        await addOneOffExpense({
                          month,
                          label: oneOffName.trim(),
                          amount: Number(oneOffAmount).toFixed(2)
                        });
                        setOneOffName("");
                        setOneOffAmount("");
                      })
                    }
                    type="button"
                  >
                    Add
                  </button>
                </div>
              </div>
            </div>

            <div className={styles.netIncomeTotals}>
              <div className={styles.netIncomeTotalRow}>
                <span>Total bills</span>
                <strong>{formatMoney(summary.total_bills)}</strong>
              </div>
              <div
                className={`${styles.netIncomeTotalRow} ${styles.netIncomeNetRow} ${
                  netIsNegative ? styles.netIncomeNegative : ""
                }`}
              >
                <span>Net income</span>
                <strong>{formatMoney(summary.net_income)}</strong>
              </div>
            </div>

          </>
        )}
      </div>
    </div>

    {showManage ? (
      <ManageBillsModal onChanged={() => void load()} onClose={() => setShowManage(false)} />
    ) : null}
    </>
  );
}

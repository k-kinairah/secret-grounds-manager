"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "@/app/page.module.css";
import {
  createMember,
  fetchMemberLedger,
  fetchMembers,
  fetchMembershipProducts,
  purchaseMemberTime,
  updateMemberStatus
} from "@/lib/api";
import { formatDateTime, formatMoney, formatRateDuration } from "@/lib/time";
import type { MemberRecord, MembershipProduct, MemberTimeTransaction, PaymentMethod } from "@/lib/types";

export function MembersManager() {
  const [members, setMembers] = useState<MemberRecord[]>([]);
  const [products, setProducts] = useState<MembershipProduct[]>([]);
  const [query, setQuery] = useState("");
  const [selectedMember, setSelectedMember] = useState<MemberRecord | null>(null);
  const [ledger, setLedger] = useState<MemberTimeTransaction[]>([]);
  const [message, setMessage] = useState<string | null>("Loading members...");
  const [isSaving, setIsSaving] = useState(false);
  const activeProducts = useMemo(() => products.filter((product) => product.is_active), [products]);

  async function refresh(search = query) {
    const [nextMembers, nextProducts] = await Promise.all([
      fetchMembers(search),
      fetchMembershipProducts()
    ]);
    setMembers(nextMembers);
    setProducts(nextProducts);
    setMessage(null);
  }

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      refresh().catch((error) => {
        setMessage(error instanceof Error ? error.message : "Could not load members.");
      });
    }, 0);
    return () => window.clearTimeout(timeoutId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleCreate(formData: FormData) {
    setIsSaving(true);
    try {
      await createMember({
        member_code: String(formData.get("memberCode") ?? ""),
        full_name: String(formData.get("fullName") ?? ""),
        phone_number: String(formData.get("phoneNumber") ?? "") || undefined
      });
      setMessage("Member created.");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not create member.");
    } finally {
      setIsSaving(false);
    }
  }

  async function handleSelect(member: MemberRecord) {
    setSelectedMember(member);
    setLedger(await fetchMemberLedger(member.member_id));
  }

  async function handlePurchase(formData: FormData) {
    if (!selectedMember) {
      return;
    }
    setIsSaving(true);
    try {
      const paymentMethod = String(formData.get("paymentMethod") ?? "cash") as PaymentMethod;
      const response = await purchaseMemberTime(selectedMember.member_id, {
        product_id: String(formData.get("productId") ?? ""),
        payment_method: paymentMethod,
        cash_received: paymentMethod === "cash" ? String(formData.get("cashReceived") ?? "") : undefined,
        reference_number: paymentMethod !== "cash" ? String(formData.get("referenceNumber") ?? "") : undefined
      });
      setSelectedMember(response.member);
      setLedger(await fetchMemberLedger(selectedMember.member_id));
      setMessage("Member time added.");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not process purchase.");
    } finally {
      setIsSaving(false);
    }
  }

  async function toggleStatus(member: MemberRecord) {
    setIsSaving(true);
    try {
      await updateMemberStatus(member.member_id, member.status === "active" ? "inactive" : "active");
      await refresh();
      setMessage(member.status === "active" ? "Member disabled." : "Member enabled.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not update member.");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <main className={styles.page}>
      <div className={styles.topBar}>
        <div className={styles.brandBlock}>
          <h1>Members</h1>
          <p>Reusable digital time balances and membership purchase history.</p>
        </div>
      </div>

      {message ? <div className={styles.statusMessage}>{message}</div> : null}

      <section className={styles.paymentBox}>
        <form
          className={styles.amountPanel}
          onSubmit={(event) => {
            event.preventDefault();
            void refresh(query);
          }}
        >
          <label className={styles.field}>
            Search members
            <input
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Code, name, or phone"
              value={query}
            />
          </label>
          <button className={styles.secondaryButton} type="submit">Search</button>
        </form>
        <form
          className={styles.optionGrid}
          onSubmit={(event) => {
            event.preventDefault();
            void handleCreate(new FormData(event.currentTarget));
            event.currentTarget.reset();
          }}
        >
          <label className={styles.field}>
            Member code
            <input name="memberCode" placeholder="SG-001" required />
          </label>
          <label className={styles.field}>
            Full name
            <input name="fullName" placeholder="Customer name" required />
          </label>
          <label className={styles.field}>
            Phone
            <input name="phoneNumber" placeholder="Optional" />
          </label>
          <button className={styles.primaryButton} disabled={isSaving} type="submit">
            Create Member
          </button>
        </form>
      </section>

      <section className={styles.pcGrid}>
        {members.length === 0 ? <div className={styles.emptyState}>No members found.</div> : null}
        {members.map((member) => (
          <article className={styles.pcCard} key={member.member_id}>
            <div className={styles.pcHeader}>
              <div>
                <div className={styles.pcName}>{member.member_code}</div>
                <p>{member.full_name}</p>
              </div>
              <span className={`${styles.statusPill} ${member.status === "active" ? styles.badgeOnline : styles.badgeOffline}`}>
                {member.status}
              </span>
            </div>
            <dl className={styles.detailList}>
              <div className={styles.detailRow}>
                <span>Balance</span>
                <strong>{formatRateDuration(member.time_balance_seconds)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Phone</span>
                <strong>{member.phone_number ?? "None"}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Joined</span>
                <strong>{formatDateTime(member.joined_at)}</strong>
              </div>
            </dl>
            <div className={styles.actions}>
              <button className={styles.secondaryButton} onClick={() => void handleSelect(member)} type="button">
                View Ledger
              </button>
              <button className={styles.dangerButton} disabled={isSaving} onClick={() => void toggleStatus(member)} type="button">
                {member.status === "active" ? "Disable" : "Enable"}
              </button>
            </div>
          </article>
        ))}
      </section>

      {selectedMember ? (
        <div className={styles.overlay} role="presentation">
          <section className={styles.dialog} role="dialog" aria-label="Member detail" aria-modal="true">
            <h2>{selectedMember.member_code} - {selectedMember.full_name}</h2>
            <div className={styles.paymentSummary}>
              <span>Current balance</span>
              <strong>{formatRateDuration(selectedMember.time_balance_seconds)}</strong>
            </div>
            <form
              className={styles.paymentBox}
              onSubmit={(event) => {
                event.preventDefault();
                void handlePurchase(new FormData(event.currentTarget));
              }}
            >
              <label className={styles.field}>
                Product
                <select name="productId">
                  {activeProducts.map((product) => (
                    <option key={product.product_id} value={product.product_id}>
                      {product.name} - {formatRateDuration(product.time_seconds)} - {formatMoney(product.price)}
                    </option>
                  ))}
                </select>
              </label>
              <label className={styles.field}>
                Payment method
                <select name="paymentMethod">
                  <option value="cash">Cash</option>
                  <option value="gcash">GCash</option>
                  <option value="maya">Maya</option>
                  <option value="other">Other</option>
                </select>
              </label>
              <label className={styles.field}>
                Cash received / reference
                <input name="cashReceived" placeholder="100.00 or reference number" />
              </label>
              <button className={styles.primaryButton} disabled={isSaving} type="submit">
                Add Member Time
              </button>
            </form>
            <div className={styles.detailList}>
              {ledger.map((transaction) => (
                <div className={styles.detailRow} key={transaction.transaction_id}>
                  <span>{transaction.transaction_type}</span>
                  <strong>
                    {formatRateDuration(Math.abs(transaction.seconds_delta))} →{" "}
                    {formatRateDuration(transaction.balance_after_seconds)}
                  </strong>
                </div>
              ))}
            </div>
            <div className={styles.dialogActions}>
              <button className={styles.secondaryButton} onClick={() => setSelectedMember(null)} type="button">
                Close
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}

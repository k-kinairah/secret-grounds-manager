"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "@/app/page.module.css";
import {
  createMember,
  fetchCurrentUser,
  fetchMemberLedger,
  fetchMembers,
  fetchMembershipProducts,
  purchaseMemberTime,
  resetMemberPin,
  updateMemberStatus
} from "@/lib/api";
import { formatDateTime, formatMoney, formatNaturalDuration } from "@/lib/time";
import type {
  CurrentUser,
  MemberRecord,
  MembershipProduct,
  MemberTimeTransaction,
  PaymentMethod
} from "@/lib/types";

function describeActivity(transaction: MemberTimeTransaction): {
  title: string;
  detail: string;
  amount: string | null;
  isTechnical: boolean;
} {
  const delta = Math.abs(transaction.seconds_delta);
  const balance = formatNaturalDuration(transaction.balance_after_seconds);

  switch (transaction.transaction_type) {
    case "membership_purchase":
      return {
        title: "Membership Purchased",
        detail: `Added ${formatNaturalDuration(delta)}. New balance: ${balance}.`,
        amount: transaction.amount_paid,
        isTechnical: false
      };
    case "promo_topup":
      return {
        title: "Member Promo Purchased",
        detail: `Added ${formatNaturalDuration(delta)}. New balance: ${balance}.`,
        amount: transaction.amount_paid,
        isTechnical: false
      };
    case "session_usage_finalized":
      return {
        title: "Played Session",
        detail:
          delta === 0
            ? `No time deducted. Balance remains ${balance}.`
            : `Used ${formatNaturalDuration(delta)}. Remaining balance: ${balance}.`,
        amount: null,
        isTechnical: false
      };
    case "session_refund":
      return {
        title: "Time Returned",
        detail: `Returned ${formatNaturalDuration(delta)}. New balance: ${balance}.`,
        amount: null,
        isTechnical: false
      };
    case "manual_adjustment":
      return {
        title: "Manual Balance Adjustment",
        detail: `Adjusted by ${formatNaturalDuration(delta)}. New balance: ${balance}.`,
        amount: null,
        isTechnical: false
      };
    case "expiration_adjustment":
      return {
        title: "Expired Time Adjustment",
        detail: `Adjusted ${formatNaturalDuration(delta)}. New balance: ${balance}.`,
        amount: null,
        isTechnical: false
      };
    case "session_reservation_released":
      return {
        title: "Session Reservation Released",
        detail: `Held time was released. Balance: ${balance}.`,
        amount: null,
        isTechnical: true
      };
    default:
      return {
        title: "Session Reservation",
        detail: `Temporarily held ${formatNaturalDuration(delta)}. Balance: ${balance}.`,
        amount: null,
        isTechnical: true
      };
  }
}

export function MembersManager() {
  const [members, setMembers] = useState<MemberRecord[]>([]);
  const [products, setProducts] = useState<MembershipProduct[]>([]);
  const [query, setQuery] = useState("");
  const [selectedMember, setSelectedMember] = useState<MemberRecord | null>(null);
  const [ledger, setLedger] = useState<MemberTimeTransaction[]>([]);
  const [message, setMessage] = useState<string | null>("Loading members...");
  const [isSaving, setIsSaving] = useState(false);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [selectedProductId, setSelectedProductId] = useState("");
  const [showAdvancedActivity, setShowAdvancedActivity] = useState(false);

  const activeProducts = useMemo(() => products.filter((product) => product.is_active), [products]);
  const selectedProduct =
    activeProducts.find((product) => product.product_id === selectedProductId) ??
    activeProducts[0] ??
    null;
  const visibleLedger = useMemo(
    () =>
      ledger
        .map((transaction) => ({ transaction, activity: describeActivity(transaction) }))
        .filter(({ activity }) => showAdvancedActivity || !activity.isTechnical),
    [ledger, showAdvancedActivity]
  );
  const canManagePins = currentUser?.role === "owner";

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
      Promise.all([refresh(), fetchCurrentUser().then(setCurrentUser)]).catch((error) => {
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
    setSelectedProductId(activeProducts[0]?.product_id ?? "");
    setShowAdvancedActivity(false);
    setLedger(await fetchMemberLedger(member.member_id));
  }

  async function handlePurchase(formData: FormData) {
    if (!selectedMember || selectedProduct === null) {
      return;
    }
    setIsSaving(true);
    try {
      const paymentMethod = String(formData.get("paymentMethod") ?? "cash") as PaymentMethod;
      const response = await purchaseMemberTime(selectedMember.member_id, {
        product_id: selectedProduct.product_id,
        payment_method: paymentMethod,
        cash_received:
          paymentMethod === "cash" ? String(formData.get("cashReceived") ?? "") : undefined,
        reference_number:
          paymentMethod !== "cash" ? String(formData.get("referenceNumber") ?? "") : undefined
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

  async function handlePinReset(formData: FormData) {
    if (!selectedMember) {
      return;
    }

    const pin = String(formData.get("memberPin") ?? "").trim();
    setIsSaving(true);
    try {
      const updated = await resetMemberPin(selectedMember.member_id, pin || null);
      setSelectedMember(updated);
      await refresh();
      setMessage(pin ? "Member PIN updated." : "Member PIN removed.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not update member PIN.");
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
          <p>Member play balances, purchases, and activity history.</p>
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
          <button className={styles.secondaryButton} type="submit">
            Search
          </button>
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
        {members.length === 0 ? <div className={styles.emptyState}>No members yet.</div> : null}
        {members.map((member) => (
          <article className={styles.pcCard} key={member.member_id}>
            <div className={styles.pcHeader}>
              <div>
                <div className={styles.pcName}>{member.member_code}</div>
                <p>{member.full_name}</p>
              </div>
              <span
                className={`${styles.statusPill} ${
                  member.status === "active" ? styles.badgeOnline : styles.badgeOffline
                }`}
              >
                {member.status}
              </span>
            </div>
            <dl className={styles.detailList}>
              <div className={styles.detailRow}>
                <span>Balance</span>
                <strong>{formatNaturalDuration(member.time_balance_seconds)}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Phone</span>
                <strong>{member.phone_number ?? "None"}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>PIN</span>
                <strong>{member.has_pin ? "Enabled" : "Not set"}</strong>
              </div>
              <div className={styles.detailRow}>
                <span>Joined</span>
                <strong>{formatDateTime(member.joined_at)}</strong>
              </div>
            </dl>
            <div className={styles.actions}>
              <button
                className={styles.secondaryButton}
                onClick={() => void handleSelect(member)}
                type="button"
              >
                View Activity
              </button>
              <button
                className={styles.dangerButton}
                disabled={isSaving}
                onClick={() => void toggleStatus(member)}
                type="button"
              >
                {member.status === "active" ? "Disable" : "Enable"}
              </button>
            </div>
          </article>
        ))}
      </section>

      {selectedMember ? (
        <div className={styles.overlay} role="presentation">
          <section
            className={styles.dialog}
            role="dialog"
            aria-label="Member detail"
            aria-modal="true"
          >
            <h2>
              {selectedMember.member_code} - {selectedMember.full_name}
            </h2>
            <div className={styles.paymentSummary}>
              <span>Current balance</span>
              <strong>{formatNaturalDuration(selectedMember.time_balance_seconds)}</strong>
            </div>
            <form
              className={styles.paymentBox}
              onSubmit={(event) => {
                event.preventDefault();
                void handlePurchase(new FormData(event.currentTarget));
              }}
            >
              <div className={styles.optionGrid}>
                {activeProducts.map((product) => (
                  <button
                    className={`${styles.rateOption} ${
                      selectedProduct?.product_id === product.product_id
                        ? styles.rateOptionSelected
                        : ""
                    }`}
                    key={product.product_id}
                    onClick={() => setSelectedProductId(product.product_id)}
                    type="button"
                  >
                    <span className={styles.rateTitle}>{product.name}</span>
                    <span className={styles.rateDuration}>
                      Adds {formatNaturalDuration(product.time_seconds)}
                    </span>
                    <span className={styles.ratePrice}>{formatMoney(product.price)}</span>
                  </button>
                ))}
              </div>
              {selectedProduct ? (
                <div className={styles.amountPreview}>
                  <span>Balance preview</span>
                  <strong>
                    {formatNaturalDuration(selectedMember.time_balance_seconds)} +{" "}
                    {formatNaturalDuration(selectedProduct.time_seconds)} ={" "}
                    {formatNaturalDuration(
                      selectedMember.time_balance_seconds + selectedProduct.time_seconds
                    )}
                  </strong>
                </div>
              ) : null}
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

            {canManagePins ? (
              <form
                className={styles.paymentBox}
                onSubmit={(event) => {
                  event.preventDefault();
                  void handlePinReset(new FormData(event.currentTarget));
                  event.currentTarget.reset();
                }}
              >
                <div className={styles.amountPreview}>
                  <span>Member PIN</span>
                  <strong>
                    {selectedMember.has_pin
                      ? "PIN enabled. Enter a new PIN to reset it, or leave blank to remove it."
                      : "Optional 4-6 digit PIN. Required only when this member starts play."}
                  </strong>
                </div>
                <label className={styles.field}>
                  New PIN
                  <input
                    inputMode="numeric"
                    maxLength={6}
                    minLength={4}
                    name="memberPin"
                    placeholder="4-6 digits, blank removes"
                    type="password"
                  />
                </label>
                <button className={styles.secondaryButton} disabled={isSaving} type="submit">
                  {selectedMember.has_pin ? "Reset PIN" : "Set PIN"}
                </button>
              </form>
            ) : null}

            <div className={styles.sectionHeader}>
              <div>
                <h2>Activity</h2>
                <p className={styles.panelText}>Purchases and actual play time deducted.</p>
              </div>
              {currentUser?.role === "admin" || currentUser?.role === "owner" ? (
                <button
                  className={styles.secondaryButton}
                  onClick={() => setShowAdvancedActivity((value) => !value)}
                  type="button"
                >
                  {showAdvancedActivity ? "Hide Technical" : "Show Technical"}
                </button>
              ) : null}
            </div>
            <div className={styles.detailList}>
              {visibleLedger.length === 0 ? (
                <div className={styles.emptyState}>No member activity yet.</div>
              ) : null}
              {visibleLedger.map(({ transaction, activity }) => (
                <div className={styles.detailRow} key={transaction.transaction_id}>
                  <span>
                    {activity.title}
                    <small>{formatDateTime(transaction.created_at)}</small>
                  </span>
                  <strong>
                    {activity.amount ? `${formatMoney(activity.amount)} - ` : ""}
                    {activity.detail}
                  </strong>
                </div>
              ))}
            </div>
            <div className={styles.dialogActions}>
              <button
                className={styles.secondaryButton}
                onClick={() => setSelectedMember(null)}
                type="button"
              >
                Close
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}

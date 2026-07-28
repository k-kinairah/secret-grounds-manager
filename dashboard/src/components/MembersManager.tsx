"use client";

import {
  type FormEvent,
  type RefObject,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";
import { createPortal } from "react-dom";
import { usePathname } from "next/navigation";
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

function formatMemberBalance(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;

  if (hours > 0 && remainingMinutes > 0) {
    return `${hours} hr ${remainingMinutes} min`;
  }

  if (hours > 0) {
    return `${hours} hr`;
  }

  return `${minutes} min`;
}

function describeActivity(transaction: MemberTimeTransaction): {
  title: string;
  detail: string;
  amount: string | null;
  isTechnical: boolean;
} {
  const delta = Math.abs(transaction.seconds_delta);
  const balance = formatMemberBalance(transaction.balance_after_seconds);

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

function useModalFocus(
  isOpen: boolean,
  modalRef: RefObject<HTMLElement | null>,
  onClose: () => void
) {
  useEffect(() => {
    if (!isOpen) {
      return;
    }

    const modal = modalRef.current;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    window.setTimeout(() => {
      const firstField = modal?.querySelector<HTMLElement>(
        "input, select, button, textarea, [href], [tabindex]:not([tabindex='-1'])"
      );
      firstField?.focus();
    }, 0);

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }

      if (event.key !== "Tab" || modal === null) {
        return;
      }

      const focusable = Array.from(
        modal.querySelectorAll<HTMLElement>(
          "input, select, button, textarea, [href], [tabindex]:not([tabindex='-1'])"
        )
      ).filter((element) => !element.hasAttribute("disabled"));

      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
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
  }, [isOpen, modalRef, onClose]);
}

export function MembersManager() {
  const pathname = usePathname();
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
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [statusFilter, setStatusFilter] = useState<"all" | "active" | "inactive">("all");
  const [confirmingStatusMember, setConfirmingStatusMember] = useState<MemberRecord | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const createModalRef = useRef<HTMLElement | null>(null);
  const detailModalRef = useRef<HTMLElement | null>(null);
  const confirmModalRef = useRef<HTMLElement | null>(null);
  const memberMenuTriggerRefs = useRef(new Map<string, HTMLButtonElement>());
  const [openMemberMenu, setOpenMemberMenu] = useState<{
    member: MemberRecord;
    left: number;
    top: number;
    placement: "up" | "down";
  } | null>(null);

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
  const filteredMembers = useMemo(
    () =>
      statusFilter === "all"
        ? members
        : members.filter((member) => member.status === statusFilter),
    [members, statusFilter]
  );
  const canManagePins = currentUser?.role === "owner";
  const hasSearch = query.trim().length > 0 || statusFilter !== "all";
  const closeCreateDialog = useCallback(() => setIsCreateOpen(false), []);
  const closeDetailDialog = useCallback(() => setSelectedMember(null), []);
  const closeConfirmDialog = useCallback(() => setConfirmingStatusMember(null), []);
  const closeMemberMenu = useCallback(() => setOpenMemberMenu(null), []);

  useModalFocus(isCreateOpen, createModalRef, closeCreateDialog);
  useModalFocus(selectedMember !== null, detailModalRef, closeDetailDialog);
  useModalFocus(confirmingStatusMember !== null, confirmModalRef, closeConfirmDialog);

  useEffect(() => {
    const timeoutId = window.setTimeout(closeMemberMenu, 0);
    return () => window.clearTimeout(timeoutId);
  }, [closeMemberMenu, pathname, selectedMember]);

  useEffect(() => {
    if (openMemberMenu === null) {
      return;
    }
    const menuMemberId = openMemberMenu.member.member_id;

    function handlePointerDown(event: PointerEvent) {
      const target = event.target as Node;
      const trigger = memberMenuTriggerRefs.current.get(menuMemberId);
      const menu = document.getElementById("member-floating-menu");
      if (trigger?.contains(target) || menu?.contains(target)) {
        return;
      }
      closeMemberMenu();
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeMemberMenu();
        memberMenuTriggerRefs.current.get(menuMemberId)?.focus();
      }
    }

    function handleViewportChange() {
      closeMemberMenu();
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    window.addEventListener("resize", handleViewportChange);
    window.addEventListener("scroll", handleViewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("resize", handleViewportChange);
      window.removeEventListener("scroll", handleViewportChange, true);
    };
  }, [closeMemberMenu, openMemberMenu]);

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
    let isMounted = true;
    Promise.all([fetchMembers(""), fetchMembershipProducts(), fetchCurrentUser()])
      .then(([nextMembers, nextProducts, nextUser]) => {
        if (!isMounted) {
          return;
        }
        setMembers(nextMembers);
        setProducts(nextProducts);
        setCurrentUser(nextUser);
        setMessage(null);
      })
      .catch((error) => {
        if (isMounted) {
          setMessage(error instanceof Error ? error.message : "Could not load members.");
        }
      });
    return () => {
      isMounted = false;
    };
  }, []);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      refresh(query).catch((error) => {
        setMessage(error instanceof Error ? error.message : "Could not load members.");
      });
    }, 250);
    return () => window.clearTimeout(timeoutId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving) {
      return;
    }

    const form = event.currentTarget;
    const formData = new FormData(form);
    setIsSaving(true);
    setCreateError(null);
    try {
      await createMember({
        member_code: String(formData.get("memberCode") ?? ""),
        full_name: String(formData.get("fullName") ?? ""),
        phone_number: String(formData.get("phoneNumber") ?? "") || undefined
      });
      setMessage("Member created.");
      await refresh();
      form.reset();
      setIsCreateOpen(false);
    } catch (error) {
      const errorText = error instanceof Error ? error.message : "Could not create member.";
      setCreateError(errorText);
      setMessage(errorText);
    } finally {
      setIsSaving(false);
    }
  }

  async function handleSelect(member: MemberRecord) {
    closeMemberMenu();
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
      setConfirmingStatusMember(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not update member.");
    } finally {
      setIsSaving(false);
    }
  }

  function clearSearch() {
    setQuery("");
    setStatusFilter("all");
  }

  function toggleMemberMenu(member: MemberRecord, trigger: HTMLButtonElement) {
    if (openMemberMenu?.member.member_id === member.member_id) {
      closeMemberMenu();
      return;
    }
    const rect = trigger.getBoundingClientRect();
    const menuWidth = 190;
    const menuHeight = 110;
    const placement = window.innerHeight - rect.bottom >= menuHeight + 10 ? "down" : "up";
    setOpenMemberMenu({
      member,
      left: Math.min(Math.max(8, rect.right - menuWidth), window.innerWidth - menuWidth - 8),
      top: placement === "down" ? rect.bottom + 6 : Math.max(8, rect.top - menuHeight - 6),
      placement
    });
  }

  function setMemberMenuTrigger(memberId: string, node: HTMLButtonElement | null) {
    if (node) {
      memberMenuTriggerRefs.current.set(memberId, node);
    } else {
      memberMenuTriggerRefs.current.delete(memberId);
    }
  }

  const floatingMemberMenu = openMemberMenu;

  return (
    <main className={styles.page}>
      <div className={styles.memberHeader}>
        <div className={styles.brandBlock}>
          <h1>Members</h1>
          <p>Member balances, purchases, and activity.</p>
        </div>
        <div className={styles.memberHeaderActions}>
          <label className={styles.memberSearch}>
            <span className="srOnly">Search members</span>
            <input
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by code, name, or phone"
              value={query}
            />
            {query ? (
              <button aria-label="Clear member search" onClick={clearSearch} type="button">
                Clear
              </button>
            ) : null}
          </label>
          <button
            className={styles.primaryButton}
            onClick={() => {
              setCreateError(null);
              setIsCreateOpen(true);
            }}
            type="button"
          >
            Add Member
          </button>
        </div>
      </div>

      <div className={styles.memberToolbar}>
        <div className={styles.memberFilterGroup} aria-label="Member status filter">
          {(["all", "active", "inactive"] as const).map((filter) => (
            <button
              className={statusFilter === filter ? styles.memberFilterActive : ""}
              key={filter}
              onClick={() => setStatusFilter(filter)}
              type="button"
            >
              {filter === "all" ? "All Members" : filter === "active" ? "Active" : "Disabled"}
            </button>
          ))}
        </div>
        <span>
          {filteredMembers.length} {filteredMembers.length === 1 ? "member" : "members"}
          {hasSearch ? " matched" : ""}
        </span>
      </div>

      {message ? <div className={styles.statusMessage}>{message}</div> : null}

      <section className={styles.memberDirectory} aria-label="Member directory">
        {filteredMembers.length === 0 ? (
          <div className={styles.memberEmptyState}>
            <h2>{hasSearch ? "No members found" : "No members yet"}</h2>
            <p>
              {hasSearch
                ? "Try another code, name, or phone number."
                : "Create the first member to begin storing reusable play balances."}
            </p>
            {hasSearch ? (
              <button className={styles.secondaryButton} onClick={clearSearch} type="button">
                Clear Search
              </button>
            ) : (
              <button
                className={styles.primaryButton}
                onClick={() => {
                  setCreateError(null);
                  setIsCreateOpen(true);
                }}
                type="button"
              >
                Add Member
              </button>
            )}
          </div>
        ) : (
          <div className={styles.memberTableWrap}>
            <table className={styles.memberTable}>
              <thead>
                <tr>
                  <th>Member</th>
                  <th>Code</th>
                  <th>Balance</th>
                  <th>Phone</th>
                  <th>PIN</th>
                  <th>Joined</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredMembers.map((member) => (
                  <tr key={member.member_id}>
                    <td data-label="Member">
                      <strong>{member.full_name}</strong>
                    </td>
                    <td data-label="Code">{member.member_code}</td>
                    <td data-label="Balance">
                      <span className={styles.memberBalance}>
                        {formatMemberBalance(member.time_balance_seconds)}
                      </span>
                    </td>
                    <td data-label="Phone">{member.phone_number || "No phone"}</td>
                    <td data-label="PIN">{member.has_pin ? "PIN enabled" : "PIN not set"}</td>
                    <td data-label="Joined">{formatDateTime(member.joined_at)}</td>
                    <td data-label="Status">
                      <span
                        className={`${styles.statusPill} ${
                          member.status === "active" ? styles.badgeOnline : styles.badgeOffline
                        }`}
                      >
                        {member.status === "active" ? "Active" : "Disabled"}
                      </span>
                    </td>
                    <td data-label="Actions">
                      <div className={styles.memberActions}>
                        <button
                          className={styles.secondaryButton}
                          onClick={() => void handleSelect(member)}
                          type="button"
                        >
                          View Member
                        </button>
                        <button
                          aria-expanded={openMemberMenu?.member.member_id === member.member_id}
                          aria-haspopup="menu"
                          aria-label={`More actions for ${member.member_code}`}
                          className={styles.memberMoreButton}
                          onClick={(event) => toggleMemberMenu(member, event.currentTarget)}
                          ref={(node) => setMemberMenuTrigger(member.member_id, node)}
                          type="button"
                        >
                          More
                          <span aria-hidden="true" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {isCreateOpen ? (
        <div className={styles.overlay} role="presentation">
          <section
            aria-label="Add member"
            aria-modal="true"
            className={styles.memberModal}
            ref={createModalRef}
            role="dialog"
          >
            <div className={styles.memberModalHeader}>
              <div>
                <h2>Add Member</h2>
                <p>Create a reusable play-balance profile.</p>
              </div>
              <button
                aria-label="Close add member dialog"
                className={styles.iconButton}
                onClick={() => setIsCreateOpen(false)}
                type="button"
              >
                X
              </button>
            </div>
            {createError ? <div className={styles.memberDialogMessage}>{createError}</div> : null}
            <form className={styles.memberFormGrid} onSubmit={handleCreate}>
              <label className={styles.field}>
                Member code
                <input name="memberCode" placeholder="SG-001" required />
              </label>
              <label className={styles.field}>
                Full name
                <input name="fullName" placeholder="Customer name" required />
              </label>
              <label className={styles.field}>
                Phone, optional
                <input name="phoneNumber" placeholder="No phone" />
              </label>
              <div className={styles.dialogActions}>
                <button
                  className={styles.secondaryButton}
                  onClick={() => setIsCreateOpen(false)}
                  type="button"
                >
                  Cancel
                </button>
                <button className={styles.primaryButton} disabled={isSaving} type="submit">
                  {isSaving ? "Creating..." : "Create Member"}
                </button>
              </div>
            </form>
          </section>
        </div>
      ) : null}

      {floatingMemberMenu && typeof document !== "undefined"
        ? createPortal(
            <div
              className={`${styles.memberFloatingMenu} ${
                floatingMemberMenu.placement === "up" ? styles.memberFloatingMenuUp : ""
              }`}
              id="member-floating-menu"
              role="menu"
              style={{ left: floatingMemberMenu.left, top: floatingMemberMenu.top }}
            >
              <button
                onClick={() => {
                  void handleSelect(floatingMemberMenu.member);
                }}
                role="menuitem"
                type="button"
              >
                View Activity
              </button>
              <button
                onClick={() => {
                  const member = floatingMemberMenu.member;
                  closeMemberMenu();
                  setConfirmingStatusMember(member);
                }}
                role="menuitem"
                type="button"
              >
                {floatingMemberMenu.member.status === "active" ? "Disable Member" : "Enable Member"}
              </button>
            </div>,
            document.body
          )
        : null}

      {confirmingStatusMember ? (
        <div className={styles.overlay} role="presentation">
          <section
            aria-label="Confirm member status change"
            aria-modal="true"
            className={styles.confirmDialog}
            ref={confirmModalRef}
            role="dialog"
          >
            <h2>
              {confirmingStatusMember.status === "active" ? "Disable" : "Enable"}{" "}
              {confirmingStatusMember.member_code} - {confirmingStatusMember.full_name}?
            </h2>
            <p>
              {confirmingStatusMember.status === "active"
                ? "This member will no longer be available for new sessions, but their history and balance records will remain preserved."
                : "This member will become available for member-balance sessions again."}
            </p>
            <div className={styles.dialogActions}>
              <button
                className={styles.secondaryButton}
                onClick={() => setConfirmingStatusMember(null)}
                type="button"
              >
                Cancel
              </button>
              <button
                className={
                  confirmingStatusMember.status === "active"
                    ? styles.dangerButton
                    : styles.primaryButton
                }
                disabled={isSaving}
                onClick={() => void toggleStatus(confirmingStatusMember)}
                type="button"
              >
                {confirmingStatusMember.status === "active" ? "Disable Member" : "Enable Member"}
              </button>
            </div>
          </section>
        </div>
      ) : null}

      {selectedMember ? (
        <div className={styles.overlay} role="presentation">
          <section
            aria-label="Member detail"
            aria-modal="true"
            className={styles.memberDetailModal}
            ref={detailModalRef}
            role="dialog"
          >
            <div className={styles.memberModalHeader}>
              <div>
                <h2>{selectedMember.full_name}</h2>
                <p>
                  {selectedMember.member_code} /{" "}
                  {selectedMember.status === "active" ? "Active" : "Disabled"}
                </p>
              </div>
              <button
                aria-label="Close member detail"
                className={styles.iconButton}
                onClick={() => setSelectedMember(null)}
                type="button"
              >
                X
              </button>
            </div>

            <div className={styles.memberDetailGrid}>
              <section className={styles.controlSection}>
                <h3>Member Profile</h3>
                <dl className={styles.compactDetailList}>
                  <div className={styles.detailRow}>
                    <span>Current balance</span>
                    <strong>{formatMemberBalance(selectedMember.time_balance_seconds)}</strong>
                  </div>
                  <div className={styles.detailRow}>
                    <span>Phone</span>
                    <strong>{selectedMember.phone_number || "No phone"}</strong>
                  </div>
                  <div className={styles.detailRow}>
                    <span>PIN</span>
                    <strong>{selectedMember.has_pin ? "PIN enabled" : "PIN not set"}</strong>
                  </div>
                  <div className={styles.detailRow}>
                    <span>Joined</span>
                    <strong>{formatDateTime(selectedMember.joined_at)}</strong>
                  </div>
                </dl>
              </section>

              <section className={styles.controlSection}>
                <h3>Add Balance / Purchase Time</h3>
                <form
                  onSubmit={(event) => {
                    event.preventDefault();
                    void handlePurchase(new FormData(event.currentTarget));
                  }}
                >
                  <div className={styles.memberProductGrid}>
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
                          Adds {formatMemberBalance(product.time_seconds)}
                        </span>
                        <span className={styles.ratePrice}>{formatMoney(product.price)}</span>
                      </button>
                    ))}
                  </div>
                  {selectedProduct ? (
                    <div className={styles.amountPreview}>
                      <span>Balance preview</span>
                      <strong>
                        {formatMemberBalance(selectedMember.time_balance_seconds)} +{" "}
                        {formatMemberBalance(selectedProduct.time_seconds)} ={" "}
                        {formatMemberBalance(
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
                    {isSaving ? "Processing..." : "Add Member Time"}
                  </button>
                </form>
              </section>
            </div>

            {canManagePins ? (
              <section className={styles.controlSection}>
                <h3>PIN Management</h3>
                <form
                  className={styles.memberPinForm}
                  onSubmit={(event) => {
                    event.preventDefault();
                    void handlePinReset(new FormData(event.currentTarget));
                    event.currentTarget.reset();
                  }}
                >
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
              </section>
            ) : null}

            <section className={styles.controlSection}>
              <div className={styles.memberSectionHeader}>
                <div>
                  <h3>Recent Activity</h3>
                  <p>Purchases and actual play time deducted.</p>
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
              <div className={styles.memberActivityList}>
                {visibleLedger.length === 0 ? (
                  <div className={styles.memberMiniEmpty}>No member activity yet.</div>
                ) : null}
                {visibleLedger.map(({ transaction, activity }) => (
                  <div className={styles.memberActivityRow} key={transaction.transaction_id}>
                    <div>
                      <strong>{activity.title}</strong>
                      <span>{formatDateTime(transaction.created_at)}</span>
                    </div>
                    <p>
                      {activity.amount ? `${formatMoney(activity.amount)} - ` : ""}
                      {activity.detail}
                    </p>
                  </div>
                ))}
              </div>
            </section>
          </section>
        </div>
      ) : null}
    </main>
  );
}

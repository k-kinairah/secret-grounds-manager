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
  refundMemberPurchase,
  resetMemberPin,
  updateMember,
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
import { PageTitleIcon } from "./PageTitleIcon";
import { useToast } from "./ToastProvider";
import { CloseButton } from "./CloseButton";
import { SecretSelect, type SecretSelectOption } from "./SecretSelect";

// Payment is collected as cash or GCash only.
const PAYMENT_METHOD_OPTIONS: SecretSelectOption[] = [
  { value: "cash", label: "Cash" },
  { value: "gcash", label: "GCash" }
];

const REFUND_REASON_OPTIONS: SecretSelectOption[] = [
  { value: "Duplicate transaction", label: "Duplicate transaction" },
  { value: "Wrong member", label: "Wrong member" },
  { value: "Wrong package", label: "Wrong package" },
  { value: "Wrong amount", label: "Wrong amount" },
  { value: "Customer cancellation", label: "Customer cancellation" },
  { value: "Other", label: "Other" }
];

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

type MemberAccountState = "ready" | "setup" | "disabled" | "locked";
type MemberDetailTab = "overview" | "balance" | "security" | "activity";
type MemberStatusFilter = "all" | "active" | "setup" | "inactive";

function MoreVerticalIcon() {
  return (
    <svg
      aria-hidden="true"
      className={styles.memberMoreIcon}
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle cx="12" cy="5" fill="currentColor" r="1.8" />
      <circle cx="12" cy="12" fill="currentColor" r="1.8" />
      <circle cx="12" cy="19" fill="currentColor" r="1.8" />
    </svg>
  );
}

function EyeIcon({ visible }: { visible: boolean }) {
  return (
    <svg
      aria-hidden="true"
      className={styles.memberEyeIcon}
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
    >
      {visible ? (
        <>
          <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" />
          <circle cx="12" cy="12" r="3" />
        </>
      ) : (
        <>
          <path d="M9.9 5.2A9.6 9.6 0 0 1 12 5c6.4 0 10 7 10 7a17.6 17.6 0 0 1-3 3.6" />
          <path d="M6.3 6.4A17.4 17.4 0 0 0 2 12s3.6 7 10 7a9.6 9.6 0 0 0 4.3-1" />
          <path d="M10 10a2.8 2.8 0 0 0 4 4" />
          <line x1="3" y1="3" x2="21" y2="21" />
        </>
      )}
    </svg>
  );
}

function createIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? `member-payment-${Date.now()}-${Math.random()}`;
}

function getChangeState(amount: string, cashReceived: string): { label: string; state: "muted" | "warning" | "ready" } {
  if (!cashReceived.trim()) {
    return { label: "Enter cash received", state: "muted" };
  }
  const amountValue = Number(amount);
  const cashValue = Number(cashReceived);
  if (!Number.isFinite(amountValue) || !Number.isFinite(cashValue) || cashValue <= 0 || cashValue < amountValue) {
    return { label: "Insufficient cash", state: "warning" };
  }
  return { label: formatMoney(cashValue - amountValue), state: "ready" };
}

function getMemberAccountState(member: MemberRecord): MemberAccountState {
  if (member.status !== "active") {
    return "disabled";
  }

  if (member.requires_account_setup) {
    return "setup";
  }

  return "ready";
}

function getMemberAccountLabel(state: MemberAccountState): string {
  switch (state) {
    case "disabled":
      return "Disabled";
    case "locked":
      return "Locked";
    case "setup":
      return "Setup Required";
    case "ready":
    default:
      return "Ready";
  }
}

function getMemberAvatarInitial(member: MemberRecord): string {
  const source = member.display_name || member.full_name || member.username || member.member_code;
  const initial = source.trim().charAt(0);
  return initial ? initial.toUpperCase() : "?";
}

function formatMemberDateParts(value: string): { date: string; time: string } {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return { date: "Unknown", time: "" };
  }

  return {
    date: new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric"
    }).format(date),
    time: new Intl.DateTimeFormat(undefined, {
      hour: "numeric",
      minute: "2-digit"
    }).format(date)
  };
}

function getMemberLastActivityLabel(member: MemberRecord): string {
  if (member.updated_at && member.updated_at !== member.created_at) {
    return formatDateTime(member.updated_at);
  }

  return "No activity yet";
}

function describeActivity(transaction: MemberTimeTransaction): {
  title: string;
  detail: string;
  amount: string | null;
  isTechnical: boolean;
  balanceLabel: "Standard" | "VIP";
} {
  const delta = Math.abs(transaction.seconds_delta);
  const balance = formatMemberBalance(transaction.balance_after_seconds);
  // Historical rows recorded before balance_type existed are always
  // 'standard' in the API response (the backend backfills them, never the
  // client) -- so old Standard transactions never get relabeled as VIP.
  const balanceLabel: "Standard" | "VIP" = transaction.balance_type === "vip" ? "VIP" : "Standard";

  switch (transaction.transaction_type) {
    case "membership_purchase":
      return {
        title: "Membership Purchased",
        detail: `Added ${formatNaturalDuration(delta)} of Standard time. New Standard balance: ${balance}.`,
        amount: transaction.amount_paid,
        isTechnical: false,
        balanceLabel
      };
    case "promo_topup":
      return {
        title: balanceLabel === "VIP" ? "VIP Time Purchased" : "Standard Top-up Purchased",
        detail: `Added ${formatNaturalDuration(delta)} of ${balanceLabel} time. New ${balanceLabel} balance: ${balance}.`,
        amount: transaction.amount_paid,
        isTechnical: false,
        balanceLabel
      };
    case "purchase_refund":
      return {
        title: "Purchase Refunded",
        detail: `Removed ${formatNaturalDuration(delta)} of ${balanceLabel} time. New ${balanceLabel} balance: ${balance}.`,
        amount: transaction.amount_paid,
        isTechnical: false,
        balanceLabel
      };
    case "session_usage_finalized":
      return {
        title: "Played Session",
        detail:
          delta === 0
            ? `No time deducted. Balance remains ${balance}.`
            : `Used ${formatNaturalDuration(delta)}. Remaining balance: ${balance}.`,
        amount: null,
        isTechnical: false,
        balanceLabel
      };
    case "session_refund":
      return {
        title: "Time Returned",
        detail: `Returned ${formatNaturalDuration(delta)}. New balance: ${balance}.`,
        amount: null,
        isTechnical: false,
        balanceLabel
      };
    case "manual_adjustment":
      return {
        title: "Manual Balance Adjustment",
        detail: `Adjusted by ${formatNaturalDuration(delta)}. New balance: ${balance}.`,
        amount: null,
        isTechnical: false,
        balanceLabel
      };
    case "expiration_adjustment":
      return {
        title: "Expired Time Adjustment",
        detail: `Adjusted ${formatNaturalDuration(delta)}. New balance: ${balance}.`,
        amount: null,
        isTechnical: false,
        balanceLabel
      };
    case "session_reservation_released":
      return {
        title: "Session Reservation Released",
        detail: `Held time was released. Balance: ${balance}.`,
        amount: null,
        isTechnical: true,
        balanceLabel
      };
    default:
      return {
        title: "Session Reservation",
        detail: `Temporarily held ${formatNaturalDuration(delta)}. Balance: ${balance}.`,
        amount: null,
        isTechnical: true,
        balanceLabel
      };
  }
}

export function useModalFocus(
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
  const { pushToast } = useToast();
  const [members, setMembers] = useState<MemberRecord[]>([]);
  const [products, setProducts] = useState<MembershipProduct[]>([]);
  const [query, setQuery] = useState("");
  const [selectedMember, setSelectedMember] = useState<MemberRecord | null>(null);
  const [ledger, setLedger] = useState<MemberTimeTransaction[]>([]);
  const [message, setMessage] = useState<string | null>("Loading members...");
  const [isSaving, setIsSaving] = useState(false);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [selectedProductId, setSelectedProductId] = useState("");
  const [memberPaymentMethod, setMemberPaymentMethod] = useState<PaymentMethod>("cash");
  const [memberPaymentValue, setMemberPaymentValue] = useState("");
  const [memberPaymentError, setMemberPaymentError] = useState("");
  const memberPaymentInputRef = useRef<HTMLInputElement | null>(null);
  const memberPurchaseKeyRef = useRef(createIdempotencyKey());
  const [showAdvancedActivity, setShowAdvancedActivity] = useState(false);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [statusFilter, setStatusFilter] = useState<MemberStatusFilter>("all");
  const [confirmingStatusMember, setConfirmingStatusMember] = useState<MemberRecord | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createPaymentMethod, setCreatePaymentMethod] = useState<PaymentMethod>("cash");
  const [createPaymentValue, setCreatePaymentValue] = useState("");
  const createMemberKeyRef = useRef(createIdempotencyKey());
  const [pinDraft, setPinDraft] = useState("");
  const [confirmPinDraft, setConfirmPinDraft] = useState("");
  const [showPin, setShowPin] = useState(false);
  const [showConfirmPin, setShowConfirmPin] = useState(false);
  const [activeMemberTab, setActiveMemberTab] = useState<MemberDetailTab>("overview");
  const [refundTarget, setRefundTarget] = useState<MemberTimeTransaction | null>(null);
  const [refundReason, setRefundReason] = useState("Duplicate transaction");
  const [refundNotes, setRefundNotes] = useState("");
  const [refundError, setRefundError] = useState("");
  const createModalRef = useRef<HTMLElement | null>(null);
  const detailModalRef = useRef<HTMLElement | null>(null);
  const confirmModalRef = useRef<HTMLElement | null>(null);
  const refundModalRef = useRef<HTMLElement | null>(null);
  const memberMenuTriggerRefs = useRef(new Map<string, HTMLButtonElement>());
  const [openMemberMenu, setOpenMemberMenu] = useState<{
    member: MemberRecord;
    left: number;
    top: number;
    placement: "up" | "down";
  } | null>(null);

  // Top-up picker: never the Membership Fee (initial_membership) -- that
  // product is only ever charged at account creation.
  const topupProducts = useMemo(
    () => products.filter((product) => product.is_active && product.product_type === "member_topup"),
    [products]
  );
  const membershipFee = useMemo(
    () => products.find((product) => product.is_active && product.product_type === "initial_membership") ?? null,
    [products]
  );
  const selectedProduct =
    topupProducts.find((product) => product.product_id === selectedProductId) ??
    topupProducts[0] ??
    null;
  const visibleLedger = useMemo(
    () =>
      ledger
        .map((transaction) => ({ transaction, activity: describeActivity(transaction) }))
        .filter(({ activity }) => showAdvancedActivity || !activity.isTechnical),
    [ledger, showAdvancedActivity]
  );
  const filteredMembers = useMemo(
    () => {
      if (statusFilter === "all") {
        return members;
      }

      if (statusFilter === "setup") {
        return members.filter((member) => member.requires_account_setup);
      }

      if (statusFilter === "active") {
        return members.filter(
          (member) => member.status === "active" && !member.requires_account_setup
        );
      }

      return members.filter((member) => member.status === "inactive");
    },
    [members, statusFilter]
  );
  const canManagePins = currentUser?.role === "admin" || currentUser?.role === "owner";
  const canRefundPurchases = currentUser?.role === "admin" || currentUser?.role === "owner";
  const refundedPaymentIds = useMemo(
    () =>
      new Set(
        ledger
          .filter((transaction) => transaction.transaction_type === "purchase_refund")
          .map((transaction) => transaction.related_payment_id)
          .filter((paymentId): paymentId is string => Boolean(paymentId))
      ),
    [ledger]
  );
  const isPinResetValid =
    /^[0-9]{4,6}$/.test(pinDraft) && pinDraft === confirmPinDraft;
  const showPinMismatch =
    pinDraft.length > 0 &&
    confirmPinDraft.length > 0 &&
    /^[0-9]{4,6}$/.test(pinDraft) &&
    pinDraft !== confirmPinDraft;
  const hasSearch = query.trim().length > 0 || statusFilter !== "all";
  const closeCreateDialog = useCallback(() => setIsCreateOpen(false), []);
  const closeDetailDialog = useCallback(() => setSelectedMember(null), []);
  const closeConfirmDialog = useCallback(() => setConfirmingStatusMember(null), []);
  const closeRefundDialog = useCallback(() => setRefundTarget(null), []);
  const closeMemberMenu = useCallback(() => setOpenMemberMenu(null), []);

  useModalFocus(isCreateOpen, createModalRef, closeCreateDialog);
  useModalFocus(selectedMember !== null && refundTarget === null, detailModalRef, closeDetailDialog);
  useModalFocus(confirmingStatusMember !== null, confirmModalRef, closeConfirmDialog);
  useModalFocus(refundTarget !== null, refundModalRef, closeRefundDialog);

  useEffect(() => {
    setPinDraft("");
    setConfirmPinDraft("");
    setShowPin(false);
    setShowConfirmPin(false);
    setActiveMemberTab("balance");
    setMemberPaymentMethod("cash");
    setMemberPaymentValue("");
    setMemberPaymentError("");
    memberPurchaseKeyRef.current = createIdempotencyKey();
  }, [selectedMember?.member_id]);

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

    if (!membershipFee) {
      setCreateError("No active membership fee is configured. Set one in Settings → Rates & Pricing.");
      return;
    }
    const paymentValue = createPaymentValue.trim();
    if (createPaymentMethod === "cash") {
      const cashReceived = Number(paymentValue);
      if (!paymentValue || !Number.isFinite(cashReceived) || cashReceived <= 0) {
        setCreateError("Enter a valid cash amount.");
        return;
      }
      if (cashReceived < Number(membershipFee.price)) {
        setCreateError(`Cash received must be at least ${formatMoney(membershipFee.price)}.`);
        return;
      }
    } else if (!paymentValue) {
      setCreateError("Enter the payment reference.");
      return;
    }

    setIsSaving(true);
    setCreateError(null);
    try {
      const response = await createMember({
        username: String(formData.get("username") ?? ""),
        pin: String(formData.get("memberPin") ?? ""),
        confirm_pin: String(formData.get("confirmMemberPin") ?? ""),
        payment_method: createPaymentMethod,
        cash_received: createPaymentMethod === "cash" ? paymentValue : undefined,
        reference_number: createPaymentMethod !== "cash" ? paymentValue : undefined,
        idempotency_key: createMemberKeyRef.current
      });
      const payment = response.creation_payment;
      const feePart =
        createPaymentMethod === "cash"
          ? `Change ${formatMoney(payment?.change_given ?? "0.00")}`
          : `Ref ${payment?.reference_number ?? "recorded"}`;
      pushToast(
        "Member created",
        "success",
        `${response.username ?? response.full_name}  ·  Fee ${formatMoney(payment?.amount ?? membershipFee.price)}  ·  ${feePart}  ·  Balance ${formatMemberBalance(response.time_balance_seconds)}`
      );
      createMemberKeyRef.current = createIdempotencyKey();
      setCreatePaymentValue("");
      await refresh();
      form.reset();
      setIsCreateOpen(false);
    } catch (error) {
      const errorText = error instanceof Error ? error.message : "Could not create member.";
      setCreateError(errorText);
      pushToast(errorText, "error");
    } finally {
      setIsSaving(false);
    }
  }

  async function handleProfileUpdate(formData: FormData) {
    if (!selectedMember) {
      return;
    }

    setIsSaving(true);
    try {
      const updated = await updateMember(selectedMember.member_id, {
        username: String(formData.get("username") ?? ""),
        // No longer an editable field -- preserve whatever's stored.
        display_name: selectedMember.display_name ?? undefined,
        phone_number: String(formData.get("phoneNumber") ?? "") || undefined
      });
      setSelectedMember(updated);
      await refresh();
      pushToast("Profile saved", "success");
    } catch (error) {
      pushToast(error instanceof Error ? error.message : "Unable to save profile", "error");
    } finally {
      setIsSaving(false);
    }
  }

  async function handleSelect(member: MemberRecord) {
    closeMemberMenu();
    setSelectedMember(member);
    setSelectedProductId(topupProducts[0]?.product_id ?? "");
    setShowAdvancedActivity(false);
    setLedger(await fetchMemberLedger(member.member_id));
  }

  async function handlePurchase(formData: FormData) {
    if (!selectedMember || selectedProduct === null) {
      return;
    }
    if (isSaving) {
      return;
    }
    const paymentValue = String(formData.get("paymentValue") ?? "").trim();
    if (memberPaymentMethod === "cash") {
      if (!paymentValue) {
        setMemberPaymentError("Enter the cash received.");
        memberPaymentInputRef.current?.focus();
        return;
      }
      const cashReceived = Number(paymentValue);
      if (!Number.isFinite(cashReceived) || cashReceived <= 0) {
        setMemberPaymentError("Enter a valid cash amount.");
        memberPaymentInputRef.current?.focus();
        return;
      }
      if (cashReceived < Number(selectedProduct.price)) {
        setMemberPaymentError(`Cash received must be at least ${formatMoney(selectedProduct.price)}.`);
        memberPaymentInputRef.current?.focus();
        return;
      }
    } else if (!paymentValue) {
      setMemberPaymentError("Enter the payment reference.");
      memberPaymentInputRef.current?.focus();
      return;
    }
    setMemberPaymentError("");
    setIsSaving(true);
    try {
      const response = await purchaseMemberTime(selectedMember.member_id, {
        product_id: selectedProduct.product_id,
        payment_method: memberPaymentMethod,
        cash_received:
          memberPaymentMethod === "cash" ? paymentValue : undefined,
        reference_number:
          memberPaymentMethod !== "cash" ? paymentValue : undefined,
        idempotency_key: memberPurchaseKeyRef.current
      });
      setSelectedMember(response.member);
      setLedger(await fetchMemberLedger(selectedMember.member_id));
      const isVipPurchase = response.product.balance_type === "vip";
      const who = response.member.username ?? response.member.full_name;
      const added = `+${formatMemberBalance(response.product.time_seconds)}`;
      const balance = isVipPurchase
        ? `VIP balance ${formatMemberBalance(response.member.vip_balance_seconds)}`
        : `Balance ${formatMemberBalance(response.member.time_balance_seconds)}`;
      const paymentPart =
        memberPaymentMethod === "cash"
          ? `Change ${formatMoney(response.payment.change_given ?? "0.00")}`
          : `Ref ${response.payment.reference_number ?? "recorded"}`;
      const appliedNow =
        (response.applied_to_active_session_seconds ?? 0) > 0
          ? `  ·  +${formatMemberBalance(response.applied_to_active_session_seconds ?? 0)} added to their active session`
          : "";
      pushToast(
        isVipPurchase ? "VIP time added" : "Standard time added",
        "success",
        `${who}  ·  ${added}  ·  ${paymentPart}  ·  ${balance}${appliedNow}`
      );
      memberPurchaseKeyRef.current = createIdempotencyKey();
      setMemberPaymentValue("");
      await refresh();
    } catch (error) {
      pushToast(
        "Unable to add member time",
        "error",
        error instanceof Error ? error.message : "Check the payment details and try again."
      );
    } finally {
      setIsSaving(false);
    }
  }

  async function handleRefundPurchase() {
    if (!selectedMember || !refundTarget?.related_payment_id) {
      return;
    }
    if (isSaving) {
      return;
    }
    if (refundReason === "Other" && !refundNotes.trim()) {
      setRefundError("Add a short note when using Other as the refund reason.");
      return;
    }
    setRefundError("");
    setIsSaving(true);
    try {
      const response = await refundMemberPurchase(refundTarget.related_payment_id, {
        reason: refundReason,
        notes: refundNotes.trim() || undefined
      });
      setSelectedMember(response.member);
      setLedger(await fetchMemberLedger(response.member.member_id));
      setRefundTarget(null);
      setRefundNotes("");
      setRefundError("");
      await refresh();
      pushToast(
        "Transaction refunded",
        "warning",
        `Member: ${response.member.username ?? response.member.full_name}. Time removed: ${formatMemberBalance(Math.abs(response.reversal_transaction.seconds_delta))}. Amount refunded: ${formatMoney(Math.abs(Number(response.reversal_transaction.amount_paid ?? response.payment.amount)))}. New balance: ${formatMemberBalance(response.member.time_balance_seconds)}.`
      );
    } catch (error) {
      setRefundError(error instanceof Error ? error.message : "Check the member balance and try again.");
    } finally {
      setIsSaving(false);
    }
  }

  async function handlePinReset(formData: FormData) {
    if (!selectedMember) {
      return;
    }

    const pin = String(formData.get("memberPin") ?? "").trim();
    const confirmPin = String(formData.get("confirmMemberPin") ?? "").trim();
    if (pin !== confirmPin) {
      pushToast("PINs do not match", "error");
      return;
    }
    setIsSaving(true);
    try {
      const updated = await resetMemberPin(selectedMember.member_id, pin, confirmPin);
      setSelectedMember(updated);
      await refresh();
      pushToast("PIN reset successfully", "success");
    } catch (error) {
      pushToast(error instanceof Error ? error.message : "Unable to reset PIN", "error");
    } finally {
      setIsSaving(false);
    }
  }

  async function toggleStatus(member: MemberRecord) {
    setIsSaving(true);
    try {
      await updateMemberStatus(member.member_id, member.status === "active" ? "inactive" : "active");
      await refresh();
      pushToast(member.status === "active" ? "Member disabled" : "Member enabled", "success");
      setConfirmingStatusMember(null);
    } catch (error) {
      pushToast(error instanceof Error ? error.message : "Could not update member", "error");
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
    const menuHeight = canManagePins ? 146 : 108;
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
  const selectedAccountState = selectedMember ? getMemberAccountState(selectedMember) : null;
  const selectedLastActivity = visibleLedger.length > 0 ? visibleLedger[visibleLedger.length - 1] : null;
  const memberDetailTabs: Array<{ id: MemberDetailTab; label: string }> = [
    { id: "overview", label: "Overview" },
    { id: "balance", label: "Balance" },
    ...(canManagePins ? [{ id: "security" as MemberDetailTab, label: "Security" }] : []),
    { id: "activity", label: "Activity" }
  ];
  const refundTimeSeconds = Math.abs(refundTarget?.seconds_delta ?? 0);
  const refundAmount = Math.abs(Number(refundTarget?.amount_paid ?? 0));
  const refundBalanceAfter =
    selectedMember && refundTarget
      ? selectedMember.time_balance_seconds - refundTimeSeconds
      : null;
  const memberChangeState = selectedProduct
    ? getChangeState(String(selectedProduct.price), memberPaymentValue)
    : { label: "Enter cash received", state: "muted" as const };
  const createChangeState = membershipFee
    ? getChangeState(String(membershipFee.price), createPaymentValue)
    : { label: "Enter cash received", state: "muted" as const };

  return (
    <main className={styles.page}>
      <div className={styles.memberHeader}>
        <div className={`${styles.brandBlock} pageTitleLockup`}>
          <PageTitleIcon name="members" />
          <div>
            <h1>Members</h1>
            <p>Member balances, purchases, and activity.</p>
          </div>
        </div>
        <div className={styles.memberHeaderActions}>
          <label className={styles.memberSearch}>
            <span className="srOnly">Search members</span>
            <input
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by display name, username, or phone"
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
          {(["all", "active", "setup", "inactive"] as const).map((filter) => (
            <button
              className={statusFilter === filter ? styles.memberFilterActive : ""}
              key={filter}
              onClick={() => setStatusFilter(filter)}
              type="button"
            >
              {filter === "all"
                ? "All Members"
                : filter === "active"
                  ? "Active"
                  : filter === "setup"
                    ? "Setup Required"
                    : "Disabled"}
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
                ? "No members match the current filters."
                : "Create the first member to begin storing reusable play balances."}
            </p>
            {hasSearch ? (
              <button className={styles.secondaryButton} onClick={clearSearch} type="button">
                Clear Filters
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
          <div className={styles.memberRowList}>
            {filteredMembers.map((member) => {
              const accountState = getMemberAccountState(member);
              const isSelected = selectedMember?.member_id === member.member_id;

              return (
                <div
                  className={`${styles.memberRow} ${
                    isSelected ? styles.memberRowSelected : ""
                  }`}
                  key={member.member_id}
                  onClick={() => void handleSelect(member)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      void handleSelect(member);
                    }
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <div
                    aria-hidden="true"
                    className={`${styles.memberAvatar} ${
                      accountState === "setup" ? styles.memberAvatarSetup : ""
                    }`}
                  >
                    {getMemberAvatarInitial(member)}
                  </div>

                  <div className={styles.memberRowIdentity}>
                    <strong>{member.full_name}</strong>
                    <span className={styles.memberRowSub}>
                      <span
                        className={
                          member.username ? styles.memberUsername : styles.memberUsernameMissing
                        }
                      >
                        {member.username ? `@${member.username}` : "Not configured"}
                      </span>
                      {member.phone_number ? (
                        <>
                          <span className={styles.memberRowSep}>·</span>
                          <span>{member.phone_number}</span>
                        </>
                      ) : null}
                    </span>
                  </div>

                  <div className={styles.memberRowMeta}>
                    <span
                      className={`${styles.memberRowStatus} ${
                        styles[`memberRowStatus-${accountState}`]
                      }`}
                    >
                      <span className={styles.memberStatusDot} aria-hidden="true" />
                      {getMemberAccountLabel(accountState)}
                    </span>
                    <span className={styles.memberRowMetric}>
                      <span>Standard</span>
                      <strong>{formatMemberBalance(member.time_balance_seconds)}</strong>
                    </span>
                    <span className={styles.memberRowMetric}>
                      <span>VIP</span>
                      <strong>{formatMemberBalance(member.vip_balance_seconds)}</strong>
                    </span>
                    <span className={`${styles.memberRowMetric} ${styles.memberRowMetricText}`}>
                      <span>Joined</span>
                      <strong>{formatMemberDateParts(member.joined_at).date}</strong>
                    </span>
                    <span className={`${styles.memberRowMetric} ${styles.memberRowMetricText} ${styles.memberRowActivity}`}>
                      <span>Last activity</span>
                      <strong>{getMemberLastActivityLabel(member)}</strong>
                    </span>
                  </div>

                  <button
                    aria-expanded={openMemberMenu?.member.member_id === member.member_id}
                    aria-haspopup="menu"
                    aria-label={`More actions for ${member.username ?? member.member_code}`}
                    className={styles.memberMoreButton}
                    onClick={(event) => {
                      event.stopPropagation();
                      toggleMemberMenu(member, event.currentTarget);
                    }}
                    ref={(node) => setMemberMenuTrigger(member.member_id, node)}
                    type="button"
                  >
                    <MoreVerticalIcon />
                  </button>
                </div>
              );
            })}
            <div className={styles.memberListEnd}>You've reached the end of the list.</div>
          </div>
        )}
      </section>

      {isCreateOpen ? (
        <div className={styles.overlay} role="presentation">
          <section
            aria-label="Add member"
            aria-modal="true"
            className={`${styles.memberModal} ${styles.createMemberModal}`}
            ref={createModalRef}
            role="dialog"
          >
            <div className={styles.createMemberHeader}>
              <span className={styles.createMemberIcon} aria-hidden="true">
                <PageTitleIcon name="members" />
              </span>
              <div className={styles.createMemberHeadingText}>
                <h2>Add Member</h2>
                <p>Collect the membership fee and set up a play-balance profile.</p>
              </div>
              <CloseButton label="Close add member dialog" onClick={() => setIsCreateOpen(false)} />
            </div>
            {createError ? <div className={styles.memberDialogMessage}>{createError}</div> : null}
            <form className={styles.createMemberForm} onSubmit={handleCreate}>
              <section className={styles.createSection}>
                <h3 className={styles.createSectionTitle}>Account</h3>
                <label className={styles.field}>
                  Username
                  <input
                    autoComplete="username"
                    autoFocus
                    maxLength={30}
                    minLength={3}
                    name="username"
                    pattern="[A-Za-z0-9._-]{3,30}"
                    placeholder="rogene"
                    required
                  />
                </label>
                <div className={styles.createFieldRow}>
                  <label className={styles.field}>
                    PIN
                    <input
                      inputMode="numeric"
                      maxLength={6}
                      minLength={4}
                      name="memberPin"
                      pattern="[0-9]{4,6}"
                      placeholder="4-6 digits"
                      required
                      type="password"
                    />
                  </label>
                  <label className={styles.field}>
                    Confirm PIN
                    <input
                      inputMode="numeric"
                      maxLength={6}
                      minLength={4}
                      name="confirmMemberPin"
                      pattern="[0-9]{4,6}"
                      placeholder="Repeat PIN"
                      required
                      type="password"
                    />
                  </label>
                </div>
              </section>

              <section className={styles.createSection}>
                <h3 className={styles.createSectionTitle}>Membership fee</h3>
                {membershipFee ? (
                  <>
                    <div className={styles.createFeeCard}>
                      <div>
                        <span className={styles.createFeeLabel}>{membershipFee.name}</span>
                        <span className={styles.createFeeSub}>
                          Adds {formatMemberBalance(membershipFee.time_seconds)} of Standard time
                        </span>
                      </div>
                      <span className={styles.createFeeAmount}>{formatMoney(membershipFee.price)}</span>
                    </div>
                    <div className={styles.createFieldRow}>
                      <SecretSelect
                        label="Payment method"
                        options={PAYMENT_METHOD_OPTIONS}
                        value={createPaymentMethod}
                        onChange={(next) => {
                          setCreatePaymentMethod(next as PaymentMethod);
                          setCreatePaymentValue("");
                          setCreateError(null);
                        }}
                      />
                      <label className={styles.field}>
                        {createPaymentMethod === "cash" ? "Cash received" : "Reference number"}
                        <input
                          inputMode={createPaymentMethod === "cash" ? "decimal" : undefined}
                          placeholder={createPaymentMethod === "cash" ? String(membershipFee.price) : "Required"}
                          value={createPaymentValue}
                          onChange={(event) => {
                            setCreatePaymentValue(event.target.value);
                            setCreateError(null);
                          }}
                        />
                      </label>
                    </div>
                    {createPaymentMethod === "cash" ? (
                      <div className={`${styles.changeSummary} ${styles[`changeSummary-${createChangeState.state}`]}`}>
                        <span>Change</span>
                        <strong>{createChangeState.label}</strong>
                      </div>
                    ) : null}
                  </>
                ) : (
                  <p className={styles.formError}>
                    No active membership fee is configured. Set one in Settings &rarr; Rates &amp; Pricing.
                  </p>
                )}
              </section>

              <div className={styles.createMemberActions}>
                <button
                  className={styles.secondaryButton}
                  onClick={() => setIsCreateOpen(false)}
                  type="button"
                >
                  Cancel
                </button>
                <button
                  className={styles.primaryButton}
                  disabled={isSaving || !membershipFee}
                  type="submit"
                >
                  {isSaving
                    ? "Creating…"
                    : membershipFee
                      ? `Charge ${formatMoney(membershipFee.price)} · Create`
                      : "Create Member"}
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
                Edit
              </button>
              {canManagePins ? (
                <button
                  onClick={() => {
                    void handleSelect(floatingMemberMenu.member);
                  }}
                  role="menuitem"
                  type="button"
                >
                  Reset PIN
                </button>
              ) : null}
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
              {confirmingStatusMember.username ?? confirmingStatusMember.member_code} -{" "}
              {confirmingStatusMember.full_name}?
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
        <div className={styles.memberModalLayer} role="presentation">
          <button
            aria-label="Close member detail backdrop"
            className={styles.memberModalBackdrop}
            onClick={() => setSelectedMember(null)}
            type="button"
          />
          <section
            aria-label="Member detail"
            aria-modal="true"
            className={styles.memberDetailPopout}
            ref={detailModalRef}
            role="dialog"
          >
            <div className={styles.memberModalChrome}>
            <div className={styles.memberPopoutHeader}>
              <span
                className={`${styles.memberAvatar} ${
                  selectedAccountState === "setup" ? styles.memberAvatarSetup : ""
                }`}
                aria-hidden="true"
              >
                {getMemberAvatarInitial(selectedMember)}
              </span>
              <div>
                <h2>{selectedMember.full_name}</h2>
                <p>@{selectedMember.username ?? "not-configured"}</p>
              </div>
              <CloseButton label="Close member detail" onClick={() => setSelectedMember(null)} />
            </div>

            <div
              aria-label="Member detail sections"
              className={styles.memberDetailTabs}
              onKeyDown={(event) => {
                if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
                  return;
                }
                event.preventDefault();
                const currentIndex = memberDetailTabs.findIndex((tab) => tab.id === activeMemberTab);
                const delta = event.key === "ArrowRight" ? 1 : -1;
                const nextIndex =
                  (currentIndex + delta + memberDetailTabs.length) % memberDetailTabs.length;
                setActiveMemberTab(memberDetailTabs[nextIndex].id);
              }}
              role="tablist"
            >
              {memberDetailTabs.map((tab) => (
                <button
                  aria-selected={activeMemberTab === tab.id}
                  className={`${styles.memberDetailTab} ${
                    activeMemberTab === tab.id ? styles.memberDetailTabActive : ""
                  }`}
                  key={tab.id}
                  onClick={() => setActiveMemberTab(tab.id)}
                  role="tab"
                  type="button"
                >
                  {tab.label}
                </button>
              ))}
            </div>
            </div>

            <div className={styles.memberModalContent}>
              {activeMemberTab === "overview" ? (
              <div className={styles.memberOverviewGrid} role="tabpanel">
              <section className={styles.memberDetailCard}>
                <div className={styles.memberCardHeader}>
                  <h3>Profile</h3>
                </div>
                <form
                  className={styles.memberFormGrid}
                  onSubmit={(event) => {
                    event.preventDefault();
                    void handleProfileUpdate(new FormData(event.currentTarget));
                  }}
                >
                  <label className={styles.field}>
                    Username
                    <input
                      defaultValue={selectedMember.username ?? ""}
                      maxLength={30}
                      minLength={3}
                      name="username"
                      pattern="[A-Za-z0-9._-]{3,30}"
                      required
                    />
                  </label>
                  <label className={styles.field}>
                    Phone
                    <input defaultValue={selectedMember.phone_number ?? ""} name="phoneNumber" />
                  </label>
                  <button className={styles.secondaryButton} disabled={isSaving} type="submit">
                    Save Profile
                  </button>
                </form>
              </section>

              <section className={styles.memberDetailCard}>
                <div className={styles.memberCardHeader}>
                  <h3>Account Summary</h3>
                </div>
                <dl className={styles.compactDetailList}>
                  <div className={styles.detailRow}>
                    <span>Status</span>
                    <strong>
                      {selectedAccountState ? (
                        <span
                          className={`${styles.memberSummaryStatus} ${
                            selectedAccountState
                              ? styles[`memberSummaryStatus-${selectedAccountState}`]
                              : ""
                          }`}
                        >
                          {getMemberAccountLabel(selectedAccountState)}
                        </span>
                      ) : null}
                    </strong>
                  </div>
                  <div className={styles.detailRow}>
                    <span>Standard Time</span>
                    <strong>{formatMemberBalance(selectedMember.time_balance_seconds)}</strong>
                  </div>
                  <div className={styles.detailRow}>
                    <span>VIP Time</span>
                    <strong>{formatMemberBalance(selectedMember.vip_balance_seconds)}</strong>
                  </div>
                  <div className={styles.detailRow}>
                    <span>Last activity</span>
                    <strong>
                      {selectedLastActivity
                        ? formatDateTime(selectedLastActivity.transaction.created_at)
                        : "No activity yet"}
                    </strong>
                  </div>
                  <div className={styles.detailRow}>
                    <span>Joined</span>
                    <strong>{formatDateTime(selectedMember.joined_at)}</strong>
                  </div>
                </dl>
              </section>
              </div>
              ) : null}

              {activeMemberTab === "balance" ? (
              <section className={`${styles.memberDetailCard} ${styles.memberBalanceCard}`} role="tabpanel">
                <div className={styles.memberCardHeader}>
                  <h3>Balance Management</h3>
                </div>
                <form
                  className={styles.memberBalanceForm}
                  onSubmit={(event) => {
                    event.preventDefault();
                    void handlePurchase(new FormData(event.currentTarget));
                  }}
                >
                  <div className={styles.memberBalanceHeroRow}>
                    <div className={styles.memberBalanceHero}>
                      <span>Standard Time</span>
                      <strong>{formatMemberBalance(selectedMember.time_balance_seconds)}</strong>
                    </div>
                    <div className={styles.memberBalanceHero}>
                      <span>VIP Time</span>
                      <strong>{formatMemberBalance(selectedMember.vip_balance_seconds)}</strong>
                    </div>
                  </div>
                  <div className={styles.memberProductGrid}>
                    {topupProducts.map((product) => (
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
                        <span className={styles.rateTitle}>
                          {product.name}
                          {product.balance_type === "vip" ? (
                            <span className={`${styles.memberBalanceTypeBadge} ${styles["memberBalanceType-vip"]}`}>
                              VIP Only
                            </span>
                          ) : null}
                        </span>
                        <span className={styles.rateDuration}>
                          Adds {formatMemberBalance(product.time_seconds)} of{" "}
                          {product.balance_type === "vip" ? "VIP" : "Standard"} time
                        </span>
                        <span className={styles.ratePrice}>{formatMoney(product.price)}</span>
                      </button>
                    ))}
                  </div>
                  {selectedProduct ? (
                    <div className={styles.memberBalancePreview}>
                      <span>{selectedProduct.balance_type === "vip" ? "VIP" : "Standard"} balance preview</span>
                      <strong>
                        {selectedProduct.balance_type === "vip"
                          ? `${formatMemberBalance(selectedMember.vip_balance_seconds)} + ${formatMemberBalance(
                              selectedProduct.time_seconds
                            )} = ${formatMemberBalance(
                              selectedMember.vip_balance_seconds + selectedProduct.time_seconds
                            )}`
                          : `${formatMemberBalance(selectedMember.time_balance_seconds)} + ${formatMemberBalance(
                              selectedProduct.time_seconds
                            )} = ${formatMemberBalance(
                              selectedMember.time_balance_seconds + selectedProduct.time_seconds
                            )}`}
                      </strong>
                      {selectedProduct.balance_type === "vip" ? (
                        <small>VIP promo purchases are non-refundable.</small>
                      ) : null}
                    </div>
                  ) : null}
                  <div className={styles.memberPaymentGrid}>
                  <SecretSelect
                    label="Payment method"
                    options={PAYMENT_METHOD_OPTIONS}
                    value={memberPaymentMethod}
                    onChange={(next) => {
                      setMemberPaymentMethod(next as PaymentMethod);
                      setMemberPaymentError("");
                      setMemberPaymentValue("");
                    }}
                  />
                  <label className={styles.field}>
                    {memberPaymentMethod === "cash" ? "Cash received" : "Reference number"}
                    <input
                      ref={memberPaymentInputRef}
                      inputMode={memberPaymentMethod === "cash" ? "decimal" : undefined}
                      name="paymentValue"
                      placeholder={memberPaymentMethod === "cash" ? String(selectedProduct.price) : "Required"}
                      onChange={(event) => {
                        setMemberPaymentValue(event.target.value);
                        setMemberPaymentError("");
                      }}
                      value={memberPaymentValue}
                    />
                  </label>
                  </div>
                  {memberPaymentMethod === "cash" && selectedProduct ? (
                    <div className={`${styles.changeSummary} ${styles[`changeSummary-${memberChangeState.state}`]}`}>
                      <span>Change</span>
                      <strong>{memberChangeState.label}</strong>
                    </div>
                  ) : null}
                  {memberPaymentError ? <p className={styles.formError}>{memberPaymentError}</p> : null}
                  <div className={styles.memberTabActions}>
                  <button className={styles.primaryButton} disabled={isSaving} type="submit">
                    {isSaving ? "Processing..." : "Add Member Time"}
                  </button>
                  </div>
                </form>
              </section>
              ) : null}

              {canManagePins && activeMemberTab === "security" ? (
                <section className={styles.memberDetailCard} role="tabpanel">
                  <div className={styles.memberCardHeader}>
                    <h3>Reset Member PIN</h3>
                    <p>Assign a new 4-6 digit login PIN.</p>
                  </div>
                <form
                  className={styles.memberPinForm}
                  onSubmit={(event) => {
                    event.preventDefault();
                    void handlePinReset(new FormData(event.currentTarget));
                    event.currentTarget.reset();
                    setPinDraft("");
                    setConfirmPinDraft("");
                  }}
                >
                  <div className={styles.memberPinGrid}>
                    <label className={styles.field}>
                      New PIN
                      <span className={styles.memberPasswordField}>
                        <input
                          inputMode="numeric"
                          maxLength={6}
                          minLength={4}
                          name="memberPin"
                          onChange={(event) => setPinDraft(event.target.value)}
                          pattern="[0-9]{4,6}"
                          required
                          type={showPin ? "text" : "password"}
                          value={pinDraft}
                        />
                        <button
                          aria-label={showPin ? "Hide new PIN" : "Show new PIN"}
                          className={styles.memberPasswordToggle}
                          onClick={() => setShowPin((value) => !value)}
                          type="button"
                        >
                          <EyeIcon visible={showPin} />
                        </button>
                      </span>
                    </label>
                    <label className={styles.field}>
                      Confirm PIN
                      <span className={styles.memberPasswordField}>
                        <input
                          inputMode="numeric"
                          maxLength={6}
                          minLength={4}
                          name="confirmMemberPin"
                          onChange={(event) => setConfirmPinDraft(event.target.value)}
                          pattern="[0-9]{4,6}"
                          required
                          type={showConfirmPin ? "text" : "password"}
                          value={confirmPinDraft}
                        />
                        <button
                          aria-label={showConfirmPin ? "Hide confirmed PIN" : "Show confirmed PIN"}
                          className={styles.memberPasswordToggle}
                          onClick={() => setShowConfirmPin((value) => !value)}
                          type="button"
                        >
                          <EyeIcon visible={showConfirmPin} />
                        </button>
                      </span>
                    </label>
                  </div>
                  <p className={styles.memberSecurityHint}>PIN must contain 4-6 digits.</p>
                  {showPinMismatch ? (
                    <p className={styles.memberSecurityError}>PIN entries do not match.</p>
                  ) : null}
                  <div className={styles.memberSecurityActions}>
                    <button
                      className={styles.secondaryButton}
                      disabled={isSaving || !isPinResetValid}
                      type="submit"
                    >
                      Reset PIN
                    </button>
                  </div>
                </form>
                </section>
              ) : null}

            {activeMemberTab === "activity" ? (
            <section className={`${styles.memberDetailCard} ${styles.memberActivityCard}`} role="tabpanel">
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
                  <div className={styles.memberMiniEmpty}>
                    No activity has been recorded for this member.
                  </div>
                ) : null}
                {visibleLedger.map(({ transaction, activity }) => {
                  const isRefundablePurchase =
                    canRefundPurchases &&
                    Boolean(transaction.related_payment_id) &&
                    !refundedPaymentIds.has(transaction.related_payment_id ?? "") &&
                    // VIP promo purchases are non-refundable -- the backend
                    // rejects this regardless, but the action is hidden
                    // here too so a cashier is never offered it at all.
                    transaction.balance_type !== "vip" &&
                    (transaction.transaction_type === "membership_purchase" ||
                      transaction.transaction_type === "promo_topup");
                  return (
                  <div className={styles.memberActivityRow} key={transaction.transaction_id}>
                    <div className={styles.memberActivityTop}>
                      <div className={styles.memberActivityTitle}>
                        <strong>{activity.title}</strong>
                        <span
                          className={`${styles.memberBalanceTypeBadge} ${
                            activity.balanceLabel === "VIP"
                              ? styles["memberBalanceType-vip"]
                              : styles["memberBalanceType-standard"]
                          }`}
                        >
                          {activity.balanceLabel}
                        </span>
                      </div>
                      {isRefundablePurchase ? (
                        <button
                          className={styles.memberActivityRefund}
                          onClick={() => {
                            setRefundTarget(transaction);
                            setRefundReason("Duplicate transaction");
                            setRefundNotes("");
                            setRefundError("");
                          }}
                          type="button"
                        >
                          Refund
                        </button>
                      ) : null}
                    </div>
                    <time className={styles.memberActivityTime}>
                      {formatDateTime(transaction.created_at)}
                    </time>
                    <p className={styles.memberActivityText}>
                      {activity.amount ? `${formatMoney(activity.amount)} — ` : ""}
                      {activity.detail}
                    </p>
                  </div>
                  );
                })}
              </div>
            </section>
            ) : null}
            </div>
          </section>
        </div>
      ) : null}
      {refundTarget ? (
        <div className={`${styles.overlay} ${styles.confirmOverlay}`} role="presentation">
          <section
            aria-label="Refund member purchase"
            aria-modal="true"
            className={`${styles.dialog} ${styles.refundDialog}`}
            ref={refundModalRef}
            role="dialog"
          >
            <div className={styles.refundHeader}>
              <span className={styles.refundIcon} aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9 14 4 9l5-5" />
                  <path d="M4 9h11a5 5 0 0 1 0 10h-4" />
                </svg>
              </span>
              <div>
                <h2>Refund Member Purchase</h2>
                <p>Reverses the payment and removes the time. The original stays in history.</p>
              </div>
            </div>
            <div className={styles.refundSummary} aria-label="Refund summary">
              {selectedMember ? (
                <div className={styles.refundSummaryRow}>
                  <span>Member</span>
                  <strong>{selectedMember.username ?? selectedMember.full_name}</strong>
                </div>
              ) : null}
              {refundTarget.notes ? (
                <div className={styles.refundSummaryRow}>
                  <span>Original package</span>
                  <strong>{refundTarget.notes}</strong>
                </div>
              ) : null}
              {refundTarget.amount_paid ? (
                <div className={styles.refundSummaryRow}>
                  <span>Amount to refund</span>
                  <strong>{formatMoney(refundAmount)}</strong>
                </div>
              ) : null}
              <div className={`${styles.refundSummaryRow} ${styles.refundSummaryRowHighlight}`}>
                <span>Time to be removed</span>
                <strong>{formatMemberBalance(refundTimeSeconds)}</strong>
              </div>
              {selectedMember ? (
                <div className={styles.refundSummaryRow}>
                  <span>Current balance</span>
                  <strong>{formatMemberBalance(selectedMember.time_balance_seconds)}</strong>
                </div>
              ) : null}
              {refundBalanceAfter !== null ? (
                <div className={`${styles.refundSummaryRow} ${styles.refundSummaryRowResult}`}>
                  <span>Balance after refund</span>
                  <strong>{formatMemberBalance(Math.max(0, refundBalanceAfter))}</strong>
                </div>
              ) : null}
            </div>
            <div className={styles.refundFieldRow}>
              <SecretSelect
                label="Refund reason"
                options={REFUND_REASON_OPTIONS}
                value={refundReason}
                onChange={(next) => {
                  setRefundReason(next);
                  setRefundError("");
                }}
              />
              <label className={styles.field}>
                {refundReason === "Other" ? "Notes (required)" : "Notes"}
                <input
                  onChange={(event) => setRefundNotes(event.target.value)}
                  placeholder={refundReason === "Other" ? "Add a short note" : "Optional"}
                  value={refundNotes}
                />
              </label>
            </div>
            {refundError ? <p className={styles.formError}>{refundError}</p> : null}
            <p className={styles.refundWarning}>
              Removes {formatMemberBalance(refundTimeSeconds)} from the member's balance and voids the payment.
            </p>
            <div className={styles.dialogActions}>
              <button className={styles.secondaryButton} disabled={isSaving} onClick={closeRefundDialog} type="button">
                Cancel
              </button>
              <button
                className={`${styles.primaryButton} ${styles.destructiveButton}`}
                disabled={isSaving || (refundReason === "Other" && !refundNotes.trim())}
                onClick={() => void handleRefundPurchase()}
                type="button"
              >
                {isSaving ? "Processing..." : "Confirm Refund"}
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}

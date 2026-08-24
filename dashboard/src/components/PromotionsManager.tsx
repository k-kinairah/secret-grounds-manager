"use client";

import { useEffect, useState } from "react";
import { createPromo, fetchPromos, transitionPromoState, updatePromo } from "@/lib/api";
import {
  applyAudienceRules,
  emptyPromoForm,
  promoAudienceRules,
  promoFormToInput,
  promoToForm,
  transitionActionToOwnerState,
  type PromoFormState,
  type PromoTransitionAction
} from "@/lib/promoForm";
import { formatDateTime, formatMoney, formatRateDuration } from "@/lib/time";
import type { Promo, PromoAudience, PromoDisplayStatus } from "@/lib/types";
import { PromoDetailModal } from "./PromoDetailModal";
import { PromoStateConfirmDialog } from "./PromoStateConfirmDialog";
import { useToast } from "./ToastProvider";
import styles from "@/app/promotions/promotions.module.css";

type MessageKind = "success" | "error";

interface StatusMessage {
  kind: MessageKind;
  text: string;
}

const AUDIENCE_OPTIONS: Array<{ value: PromoAudience; label: string; hint: string }> = [
  { value: "standard", label: "Standard", hint: "Station scope + Applies To required, exact amount only" },
  { value: "vip", label: "VIP", hint: "Station scope + Applies To required, exact amount only" },
  { value: "member_topup", label: "Member Top-up", hint: "Exact or minimum amount, per-member limit allowed" },
  { value: "new_member", label: "New Member", hint: "Exact amount only, account creation only" }
];

const FILTER_OPTIONS: Array<{ value: "all" | PromoDisplayStatus; label: string }> = [
  { value: "all", label: "All" },
  { value: "Active", label: "Active" },
  { value: "Scheduled", label: "Scheduled" },
  { value: "Draft", label: "Draft" },
  { value: "Disabled", label: "Disabled" },
  { value: "Ended", label: "Ended" }
];

function audienceLabel(audience: PromoAudience): string {
  return AUDIENCE_OPTIONS.find((option) => option.value === audience)?.label ?? audience;
}

function statusBadgeClassName(status: PromoDisplayStatus): string {
  switch (status) {
    case "Draft":
      return styles.statusDraft;
    case "Scheduled":
      return styles.statusScheduled;
    case "Active":
      return styles.statusActive;
    case "Disabled":
      return styles.statusDisabled;
    case "Ended":
    default:
      return styles.statusEnded;
  }
}

export function PromotionsManager() {
  const { pushToast } = useToast();
  const [promos, setPromos] = useState<Promo[]>([]);
  const [filter, setFilter] = useState<"all" | PromoDisplayStatus>("all");
  const [editingPromoId, setEditingPromoId] = useState<string | null>(null);
  const [form, setForm] = useState<PromoFormState>(emptyPromoForm());
  const [message, setMessage] = useState<StatusMessage | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedPromo, setSelectedPromo] = useState<Promo | null>(null);
  const [confirmTransition, setConfirmTransition] = useState<{
    promo: Promo;
    action: PromoTransitionAction;
  } | null>(null);
  const [isTransitionPending, setIsTransitionPending] = useState(false);

  const rules = promoAudienceRules(form.audience);
  const visiblePromos = filter === "all" ? promos : promos.filter((promo) => promo.status === filter);

  async function refresh(options: { clearMessage?: boolean } = {}) {
    try {
      const loaded = await fetchPromos();
      setPromos(loaded);
      if (options.clearMessage ?? true) {
        setMessage(null);
      }
      if (selectedPromo) {
        const updated = loaded.find((promo) => promo.promo_id === selectedPromo.promo_id);
        setSelectedPromo(updated ?? null);
      }
    } catch (error) {
      setMessage({
        kind: "error",
        text: error instanceof Error ? error.message : "Could not load promos."
      });
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    const initialFetchId = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(initialFetchId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function editPromo(promo: Promo) {
    setEditingPromoId(promo.promo_id);
    setForm(promoToForm(promo));
    setMessage(null);
  }

  function startCreate() {
    setEditingPromoId(null);
    setForm(emptyPromoForm());
    setMessage(null);
  }

  function setAudience(audience: PromoAudience) {
    setForm((current) => applyAudienceRules({ ...current, audience }));
  }

  async function savePromo() {
    if (isSaving) {
      return;
    }
    setIsSaving(true);
    try {
      const input = promoFormToInput(form);
      if (editingPromoId) {
        await updatePromo(editingPromoId, input);
        setMessage({ kind: "success", text: "Promo updated." });
        pushToast("Promo updated.", "success");
      } else {
        await createPromo(input);
        setMessage({ kind: "success", text: "Promo created as Draft. Enable it from its card when ready." });
        pushToast("Promo created.", "success");
      }
      await refresh({ clearMessage: false });
      setEditingPromoId(null);
      setForm(emptyPromoForm());
    } catch (error) {
      const text = error instanceof Error ? error.message : "Could not save promo.";
      setMessage({ kind: "error", text });
      pushToast("Could not save promo.", "error", text);
    } finally {
      setIsSaving(false);
    }
  }

  function requestTransition(promo: Promo, action: PromoTransitionAction) {
    setConfirmTransition({ promo, action });
  }

  async function confirmTransitionAction(endsAtIso?: string) {
    if (!confirmTransition || isTransitionPending) {
      return;
    }
    const { promo, action } = confirmTransition;
    setIsTransitionPending(true);
    try {
      await transitionPromoState(promo.promo_id, {
        owner_state: transitionActionToOwnerState(action),
        ...(endsAtIso ? { ends_at: endsAtIso } : {})
      });
      pushToast(`Promo ${action}d.`, "success");
      setConfirmTransition(null);
      await refresh({ clearMessage: false });
    } catch (error) {
      const text = error instanceof Error ? error.message : "Could not change promo state.";
      pushToast("Could not change promo state.", "error", text);
    } finally {
      setIsTransitionPending(false);
    }
  }

  const formTitle = editingPromoId ? "Edit Promo" : "Create Promo";

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
            Configure a temporary promotional offer. Backend validation is authoritative -- these fields are
            guidance only.
          </p>
          <div className={styles.form}>
            <label className={styles.field}>
              Audience
              <div className={styles.audienceGroup}>
                {AUDIENCE_OPTIONS.map((option) => (
                  <button
                    className={`${styles.audienceOption} ${
                      form.audience === option.value ? styles.audienceOptionActive : ""
                    }`}
                    key={option.value}
                    onClick={() => setAudience(option.value)}
                    type="button"
                  >
                    <span>{option.label}</span>
                    <span>{option.hint}</span>
                  </button>
                ))}
              </div>
            </label>
            <label className={styles.field}>
              Name
              <input
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                value={form.name}
              />
            </label>
            <div className={styles.formRow}>
              <label className={styles.field}>
                Amount mode
                <select
                  disabled={rules.allowedAmountModes.length <= 1}
                  onChange={(event) =>
                    setForm({ ...form, amountMode: event.target.value as PromoFormState["amountMode"] })
                  }
                  value={form.amountMode}
                >
                  {rules.allowedAmountModes.map((mode) => (
                    <option key={mode} value={mode}>
                      {mode === "exact" ? "Exact" : "Minimum"}
                    </option>
                  ))}
                </select>
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
            </div>
            <label className={styles.field}>
              Granted time (minutes)
              <input
                min="1"
                onChange={(event) => setForm({ ...form, grantedMinutes: event.target.value })}
                type="number"
                value={form.grantedMinutes}
              />
              <span className={styles.fieldHint}>
                {form.audience === "member_topup"
                  ? "Bonus time credited on top of the ordinary top-up."
                  : form.audience === "new_member"
                    ? "Total starting balance for the new account (not an add-on)."
                    : "Total playtime granted for this purchase."}
              </span>
            </label>
            {rules.stationScopeRequired ? (
              <label className={styles.field}>
                Station scope
                <select
                  onChange={(event) =>
                    setForm({ ...form, stationScope: event.target.value as PromoFormState["stationScope"] })
                  }
                  value={form.stationScope}
                >
                  <option value="standard">Standard</option>
                  <option value="vip">VIP</option>
                </select>
              </label>
            ) : null}
            {rules.appliesToRequired ? (
              <label className={styles.field}>
                Applies to
                <select
                  onChange={(event) =>
                    setForm({ ...form, appliesTo: event.target.value as PromoFormState["appliesTo"] })
                  }
                  value={form.appliesTo}
                >
                  <option value="start_session">Start Session</option>
                  <option value="add_time">Add Time</option>
                  <option value="both">Start Session &amp; Add Time</option>
                </select>
              </label>
            ) : null}
            <div className={styles.formRow}>
              <label className={styles.field}>
                Starts
                <input
                  onChange={(event) => setForm({ ...form, startsAt: event.target.value })}
                  type="datetime-local"
                  value={form.startsAt}
                />
              </label>
              <label className={styles.field}>
                Ends
                <input
                  onChange={(event) => setForm({ ...form, endsAt: event.target.value })}
                  type="datetime-local"
                  value={form.endsAt}
                />
              </label>
            </div>
            <div className={styles.formRow}>
              <label className={styles.field}>
                Priority
                <input
                  onChange={(event) => setForm({ ...form, priority: event.target.value })}
                  type="number"
                  value={form.priority}
                />
                <span className={styles.fieldHint}>Higher wins ties against another eligible promo.</span>
              </label>
              <label className={styles.field}>
                Total redemption limit
                <input
                  min="1"
                  onChange={(event) => setForm({ ...form, totalLimit: event.target.value })}
                  placeholder="Unlimited"
                  type="number"
                  value={form.totalLimit}
                />
              </label>
            </div>
            {rules.perMemberLimitAllowed ? (
              <label className={styles.field}>
                Per-member redemption limit
                <input
                  min="1"
                  onChange={(event) => setForm({ ...form, perMemberLimit: event.target.value })}
                  placeholder="Unlimited"
                  type="number"
                  value={form.perMemberLimit}
                />
              </label>
            ) : null}
            <div className={styles.actions}>
              <button className={styles.button} disabled={isSaving} onClick={() => void savePromo()} type="button">
                {isSaving ? "Saving..." : "Save Promo"}
              </button>
              {editingPromoId ? (
                <button className={styles.secondaryButton} onClick={startCreate} type="button">
                  Cancel
                </button>
              ) : null}
            </div>
          </div>
        </div>

        <div className={styles.panelWide}>
          <div className={styles.sectionHeader}>
            <div>
              <h2>Promotions</h2>
              <p className={styles.panelText}>Every promo the owner has ever configured, regardless of state.</p>
            </div>
            <div className={styles.filterBar}>
              {FILTER_OPTIONS.map((option) => (
                <button
                  className={`${styles.filterChip} ${filter === option.value ? styles.filterChipActive : ""}`}
                  key={option.value}
                  onClick={() => setFilter(option.value)}
                  type="button"
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
          {isLoading ? (
            <div className={styles.promoGrid} aria-label="Loading promos">
              {Array.from({ length: 4 }, (_, index) => (
                <div className={styles.loadingCard} key={index}>
                  Loading promo...
                </div>
              ))}
            </div>
          ) : visiblePromos.length === 0 ? (
            <div className={styles.emptyState}>
              {filter === "all" ? "No promos yet. Create one to get started." : `No ${filter} promos.`}
            </div>
          ) : (
            <div className={styles.promoGrid}>
              {visiblePromos.map((promo) => (
                <article
                  className={styles.promoCard}
                  key={promo.promo_id}
                  onClick={() => setSelectedPromo(promo)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelectedPromo(promo);
                    }
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <div className={styles.promoTopLine}>
                    <span className={`${styles.statusBadge} ${statusBadgeClassName(promo.status)}`}>
                      {promo.status}
                    </span>
                    <div className={styles.promoBadgeRow}>
                      <span className={styles.audienceBadge}>{audienceLabel(promo.audience)}</span>
                      <span className={styles.amountModeBadge}>
                        {promo.amount_mode === "exact" ? "Exact" : "Minimum"}
                      </span>
                    </div>
                  </div>
                  <h3>{promo.name}</h3>
                  <div className={styles.promoMeta}>
                    <span>
                      {promo.amount_mode === "minimum" ? "At least " : ""}
                      {formatMoney(promo.price)}
                    </span>
                    <strong>{formatRateDuration(promo.granted_seconds)}</strong>
                  </div>
                  <div className={styles.promoBadgeRow}>
                    <span className={styles.orderPill}>Priority {promo.priority}</span>
                    {promo.total_redemption_limit !== null ? (
                      <span className={styles.limitPill}>Limit {promo.total_redemption_limit}</span>
                    ) : null}
                    {promo.per_member_redemption_limit !== null ? (
                      <span className={styles.limitPill}>Per-member {promo.per_member_redemption_limit}</span>
                    ) : null}
                  </div>
                  <p className={styles.promoDates}>
                    {formatDateTime(promo.starts_at)} → {formatDateTime(promo.ends_at)}
                  </p>
                </article>
              ))}
            </div>
          )}
        </div>
      </section>

      {selectedPromo ? (
        <PromoDetailModal
          isTransitionPending={isTransitionPending}
          key={selectedPromo.promo_id}
          onClose={() => setSelectedPromo(null)}
          onEdit={(promo) => {
            editPromo(promo);
            setSelectedPromo(null);
          }}
          onRequestTransition={requestTransition}
          promo={selectedPromo}
        />
      ) : null}

      {confirmTransition ? (
        <PromoStateConfirmDialog
          action={confirmTransition.action}
          isLoading={isTransitionPending}
          onCancel={() => setConfirmTransition(null)}
          onConfirm={(endsAtIso) => void confirmTransitionAction(endsAtIso)}
          promo={confirmTransition.promo}
        />
      ) : null}
    </>
  );
}

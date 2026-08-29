"use client";

import { useEffect, useRef, useState } from "react";
import { fetchPromoAudit, fetchPromoRateApplications, fetchPromoRedemptions } from "@/lib/api";
import { availableTransitionActions, formatDaysOfWeek, type PromoTransitionAction } from "@/lib/promoForm";
import { formatDateTime, formatMoney, formatRateDuration } from "@/lib/time";
import type { Promo, PromoAuditEvent, PromoRedemption, TimeBasedRateApplication } from "@/lib/types";
import { CloseButton } from "./CloseButton";
import { useModalFocus } from "./MembersManager";
import dialogStyles from "@/app/page.module.css";
import styles from "@/app/promotions/promotions.module.css";

type PromoDetailTab = "overview" | "audit" | "redemptions" | "rateApplications";

interface PromoDetailModalProps {
  promo: Promo;
  onClose: () => void;
  onEdit: (promo: Promo) => void;
  onRequestTransition: (promo: Promo, action: PromoTransitionAction) => void;
  isTransitionPending: boolean;
}

const TRANSITION_LABEL: Record<PromoTransitionAction, string> = {
  enable: "Enable",
  disable: "Disable",
  end: "End",
  reactivate: "Reactivate"
};

function actionButtonClass(action: PromoTransitionAction): string {
  if (action === "disable") {
    return styles.pinkButton;
  }
  if (action === "end") {
    return styles.dangerButton;
  }
  return styles.button; // enable / reactivate
}

function audienceLabel(audience: Promo["audience"]): string {
  switch (audience) {
    case "standard":
      return "Standard";
    case "vip":
      return "VIP";
    case "member_topup":
      return "Member Top-up";
    case "new_member":
      return "New Member";
    default:
      return audience;
  }
}

function statusBadgeClassName(status: Promo["status"]): string {
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

export function PromoDetailModal({
  promo,
  onClose,
  onEdit,
  onRequestTransition,
  isTransitionPending
}: PromoDetailModalProps) {
  const isTimeBased = promo.promo_type === "time_based_rate";
  const [activeTab, setActiveTab] = useState<PromoDetailTab>("overview");
  const [auditEvents, setAuditEvents] = useState<PromoAuditEvent[]>([]);
  const [redemptions, setRedemptions] = useState<PromoRedemption[]>([]);
  const [rateApplications, setRateApplications] = useState<TimeBasedRateApplication[]>([]);
  const [isLoadingAudit, setIsLoadingAudit] = useState(false);
  const [isLoadingRedemptions, setIsLoadingRedemptions] = useState(false);
  const [isLoadingRateApplications, setIsLoadingRateApplications] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const modalRef = useRef<HTMLElement | null>(null);

  useModalFocus(true, modalRef, onClose);

  // Deferred via setTimeout(..., 0), same convention as
  // PromotionsManager/RatesManager's initial-fetch effect -- avoids
  // calling setState synchronously inside the effect body. Resetting
  // local view-state when a different promo opens is handled by the
  // parent keying this component on promo.promo_id (a fresh mount gets
  // fresh initial state for free, no reset effect needed).
  useEffect(() => {
    const fetchId = window.setTimeout(() => {
      if (activeTab === "audit" && auditEvents.length === 0) {
        setIsLoadingAudit(true);
        fetchPromoAudit(promo.promo_id)
          .then(setAuditEvents)
          .catch((error) => setHistoryError(error instanceof Error ? error.message : "Could not load audit history."))
          .finally(() => setIsLoadingAudit(false));
      }
      if (activeTab === "redemptions" && redemptions.length === 0) {
        setIsLoadingRedemptions(true);
        fetchPromoRedemptions(promo.promo_id)
          .then(setRedemptions)
          .catch((error) => setHistoryError(error instanceof Error ? error.message : "Could not load redemption history."))
          .finally(() => setIsLoadingRedemptions(false));
      }
      if (activeTab === "rateApplications" && rateApplications.length === 0) {
        setIsLoadingRateApplications(true);
        fetchPromoRateApplications(promo.promo_id)
          .then(setRateApplications)
          .catch((error) =>
            setHistoryError(error instanceof Error ? error.message : "Could not load rate application history.")
          )
          .finally(() => setIsLoadingRateApplications(false));
      }
    }, 0);
    return () => window.clearTimeout(fetchId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, promo.promo_id]);

  const tabs: Array<{ id: PromoDetailTab; label: string }> = [
    { id: "overview", label: "Overview" },
    { id: "audit", label: "Audit" },
    isTimeBased
      ? { id: "rateApplications", label: "Rate applications" }
      : { id: "redemptions", label: "Redemptions" }
  ];
  const activeCount = redemptions.filter((redemption) => redemption.voided_at === null).length;
  const transitionActions = availableTransitionActions(promo);

  return (
    <div className={dialogStyles.memberModalLayer} role="presentation">
      <button
        aria-label="Close promo detail backdrop"
        className={dialogStyles.memberModalBackdrop}
        onClick={onClose}
        type="button"
      />
      <section
        aria-label="Promo detail"
        aria-modal="true"
        className={dialogStyles.memberDetailPopout}
        ref={modalRef}
        role="dialog"
      >
        <div className={dialogStyles.memberModalChrome}>
          <div className={dialogStyles.memberPopoutHeader}>
            <div>
              <h2>{promo.name}</h2>
              <p>
                {audienceLabel(promo.audience)} · {promo.status}
              </p>
            </div>
            <CloseButton label="Close promo detail" onClick={onClose} />
          </div>
          <div
            aria-label="Promo detail sections"
            className={dialogStyles.memberDetailTabs}
            onKeyDown={(event) => {
              if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
                return;
              }
              event.preventDefault();
              const currentIndex = tabs.findIndex((tab) => tab.id === activeTab);
              const delta = event.key === "ArrowRight" ? 1 : -1;
              const nextIndex = (currentIndex + delta + tabs.length) % tabs.length;
              setActiveTab(tabs[nextIndex].id);
            }}
            role="tablist"
          >
            {tabs.map((tab) => (
              <button
                aria-selected={activeTab === tab.id}
                className={`${dialogStyles.memberDetailTab} ${
                  activeTab === tab.id ? dialogStyles.memberDetailTabActive : ""
                }`}
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                role="tab"
                type="button"
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        <div className={dialogStyles.memberModalContent}>
          {activeTab === "overview" ? (
            <div role="tabpanel">
              <section className={dialogStyles.memberDetailCard}>
                <div className={`${dialogStyles.memberCardHeader} ${styles.centeredCardHeader}`}>
                  <h3>Configuration</h3>
                </div>
                <div className={styles.detailRows}>
                  <div className={styles.detailRow}>
                    <span>Status</span>
                    <span>
                      <span className={`${styles.statusBadge} ${statusBadgeClassName(promo.status)}`}>
                        {promo.status}
                      </span>
                    </span>
                  </div>
                  <div className={styles.detailRow}>
                    <span>Type</span>
                    <span>{isTimeBased ? "Time-Based Rate" : "Purchase"}</span>
                  </div>
                  <div className={styles.detailRow}>
                    <span>Audience</span>
                    <span>{audienceLabel(promo.audience)}</span>
                  </div>
                  {isTimeBased ? (
                    <>
                      <div className={styles.detailRow}>
                        <span>Promotional rate</span>
                        <span>
                          {formatMoney(promo.price)} = {formatRateDuration(promo.granted_seconds)}
                        </span>
                      </div>
                      <div className={styles.detailRow}>
                        <span>Days</span>
                        <span>{formatDaysOfWeek(promo.days_of_week)}</span>
                      </div>
                      <div className={styles.detailRow}>
                        <span>Window (local)</span>
                        <span>
                          {promo.window_start_local_time} → {promo.window_end_local_time}
                        </span>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className={styles.detailRow}>
                        <span>Amount</span>
                        <span>
                          {promo.amount_mode === "minimum" ? "At least " : ""}
                          {formatMoney(promo.price)}
                        </span>
                      </div>
                      <div className={styles.detailRow}>
                        <span>Granted time</span>
                        <span>{formatRateDuration(promo.granted_seconds)}</span>
                      </div>
                      {promo.station_scope ? (
                        <div className={styles.detailRow}>
                          <span>Station scope</span>
                          <span>{promo.station_scope === "vip" ? "VIP" : "Standard"}</span>
                        </div>
                      ) : null}
                      {promo.applies_to ? (
                        <div className={styles.detailRow}>
                          <span>Applies to</span>
                          <span>
                            {promo.applies_to === "start_session"
                              ? "Start Session"
                              : promo.applies_to === "add_time"
                                ? "Add Time"
                                : "Start Session & Add Time"}
                          </span>
                        </div>
                      ) : null}
                    </>
                  )}
                  <div className={styles.detailRow}>
                    <span>Starts</span>
                    <span>{formatDateTime(promo.starts_at)}</span>
                  </div>
                  <div className={styles.detailRow}>
                    <span>Ends</span>
                    <span>{formatDateTime(promo.ends_at)}</span>
                  </div>
                  {isTimeBased ? null : (
                    <>
                      <div className={styles.detailRow}>
                        <span>Priority</span>
                        <span>{promo.priority}</span>
                      </div>
                      <div className={styles.detailRow}>
                        <span>Per-member limit</span>
                        <span>{promo.per_member_redemption_limit ?? "None"}</span>
                      </div>
                      <div className={styles.detailRow}>
                        <span>Total redemption limit</span>
                        <span>{promo.total_redemption_limit ?? "None"}</span>
                      </div>
                    </>
                  )}
                  <div className={styles.detailRow}>
                    <span>Created</span>
                    <span>{formatDateTime(promo.created_at)}</span>
                  </div>
                  <div className={styles.detailRow}>
                    <span>Last updated</span>
                    <span>{formatDateTime(promo.updated_at)}</span>
                  </div>
                </div>
              </section>

              <section className={dialogStyles.memberDetailCard} style={{ marginTop: 16 }}>
                <div className={`${dialogStyles.memberCardHeader} ${styles.centeredCardHeader}`}>
                  <h3>Actions</h3>
                </div>
                <div className={styles.cardActions}>
                  <button className={styles.secondaryButton} onClick={() => onEdit(promo)} type="button">
                    Edit
                  </button>
                  {transitionActions.map((action) => (
                    <button
                      className={actionButtonClass(action)}
                      disabled={isTransitionPending}
                      key={action}
                      onClick={() => onRequestTransition(promo, action)}
                      type="button"
                    >
                      {TRANSITION_LABEL[action]}
                    </button>
                  ))}
                </div>
              </section>
            </div>
          ) : null}

          {activeTab === "audit" ? (
            <div role="tabpanel">
              {historyError ? <div className={styles.errorMessage}>{historyError}</div> : null}
              {isLoadingAudit ? (
                <div className={styles.loadingCard}>Loading audit history...</div>
              ) : auditEvents.length === 0 ? (
                <div className={styles.emptyState}>No audit events yet.</div>
              ) : (
                <div className={styles.historyList}>
                  {auditEvents.map((event) => (
                    <article className={styles.historyItem} key={event.event_id}>
                      <div className={styles.historyItemHeader}>
                        <span>{event.action}</span>
                        <span>{formatDateTime(event.created_at)}</span>
                      </div>
                      <div className={styles.historyItemMeta}>
                        <span>Performed by: <code>{event.performed_by_user_id ?? "unknown"}</code></span>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </div>
          ) : null}

          {activeTab === "redemptions" ? (
            <div role="tabpanel">
              {historyError ? <div className={styles.errorMessage}>{historyError}</div> : null}
              {isLoadingRedemptions ? (
                <div className={styles.loadingCard}>Loading redemption history...</div>
              ) : redemptions.length === 0 ? (
                <div className={styles.emptyState}>No redemptions yet.</div>
              ) : (
                <>
                  <p style={{ color: "var(--muted)", fontSize: 13, marginBottom: 12 }}>
                    {activeCount} active
                    {promo.total_redemption_limit !== null ? ` of ${promo.total_redemption_limit} total limit` : ""},{" "}
                    {redemptions.length} total recorded.
                  </p>
                  <div className={styles.historyList}>
                    {redemptions.map((redemption) => (
                      <article className={styles.historyItem} key={redemption.redemption_id}>
                        <div className={styles.historyItemHeader}>
                          <span>{formatMoney(redemption.price_snapshot)} · {formatRateDuration(redemption.granted_seconds_snapshot)}</span>
                          {redemption.voided_at !== null ? (
                            <span className={styles.voidedPill}>Voided</span>
                          ) : (
                            <span>{formatDateTime(redemption.created_at)}</span>
                          )}
                        </div>
                        <div className={styles.historyItemMeta}>
                          <span>Payment: <code>{redemption.payment_id}</code></span>
                          {redemption.member_id ? (
                            <span>Member: <code>{redemption.member_id}</code></span>
                          ) : null}
                          {redemption.session_id ? (
                            <span>Session: <code>{redemption.session_id}</code></span>
                          ) : null}
                          {redemption.pc_name_snapshot ? <span>PC: {redemption.pc_name_snapshot}</span> : null}
                          {redemption.income_group_name_snapshot ? (
                            <span>Income group: {redemption.income_group_name_snapshot}</span>
                          ) : null}
                          {redemption.voided_at !== null ? (
                            <span>
                              Voided {formatDateTime(redemption.voided_at)}
                              {redemption.void_reason ? ` — ${redemption.void_reason}` : ""}
                            </span>
                          ) : null}
                        </div>
                      </article>
                    ))}
                  </div>
                </>
              )}
            </div>
          ) : null}

          {activeTab === "rateApplications" ? (
            <div role="tabpanel">
              {historyError ? <div className={styles.errorMessage}>{historyError}</div> : null}
              {isLoadingRateApplications ? (
                <div className={styles.loadingCard}>Loading rate application history...</div>
              ) : rateApplications.length === 0 ? (
                <div className={styles.emptyState}>
                  This rate has not been applied to a session yet.
                </div>
              ) : (
                <>
                  <p style={{ color: "var(--muted)", fontSize: 13, marginBottom: 12 }}>
                    {rateApplications.length} time{rateApplications.length === 1 ? "" : "s"} this promotional
                    rate replaced the normal rate. This is a usage log, not a redemption — there is no limit.
                  </p>
                  <div className={styles.historyList}>
                    {rateApplications.map((application) => (
                      <article className={styles.historyItem} key={application.application_id}>
                        <div className={styles.historyItemHeader}>
                          <span>
                            {formatMoney(application.amount)} ·{" "}
                            {formatRateDuration(application.normal_seconds)} →{" "}
                            {formatRateDuration(application.promo_seconds)}
                          </span>
                          <span>{formatDateTime(application.created_at)}</span>
                        </div>
                        <div className={styles.historyItemMeta}>
                          <span>
                            {application.action === "start_session" ? "Start Session" : "Add Time"}
                          </span>
                          <span>Payment: <code>{application.payment_id}</code></span>
                          {application.session_id ? (
                            <span>Session: <code>{application.session_id}</code></span>
                          ) : null}
                          {application.pc_name_snapshot ? <span>PC: {application.pc_name_snapshot}</span> : null}
                          {application.income_group_name_snapshot ? (
                            <span>Income group: {application.income_group_name_snapshot}</span>
                          ) : null}
                        </div>
                      </article>
                    ))}
                  </div>
                </>
              )}
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}

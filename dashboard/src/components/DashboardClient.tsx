"use client";

import { Megaphone } from "lucide-react";
import { type CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "@/app/page.module.css";
import {
  broadcastAnnouncement,
  cancelPreparedSession,
  endSession,
  fetchOperatorSale,
  fetchPcs,
  fetchCurrentUser,
  fetchIncomeGroups,
  fetchPcLayouts,
  fetchRates,
  forceEndSession,
  getPollingIntervalMs,
  recordPayment,
  prepareStartupSession,
  retryPreparedWake,
  updatePcLayout,
  deletePcLayout,
  startMemberSession,
  startPrepaidSession,
  addMemberTime,
  addPrepaidTime
} from "@/lib/api";
import { useMonitorFrames } from "@/hooks/useMonitorFrames";
import { getPackageRatesForPc } from "@/lib/packageEligibility";
import { getPcActionAvailability, sortPcsByStationNumber, summarizePcs } from "@/lib/pcStatus";
import { calculateOpenTimeEstimate, secondsFromScheduledEnd } from "@/lib/time";
import type { CurrentUser, IncomeGroup, MessageSeverity, OperatorSaleSummary, PcLayoutSlot, PcSummary, RateProfile } from "@/lib/types";
import { DashboardSummary } from "./DashboardSummary";
import { EndSessionDialog } from "./EndSessionDialog";
import { PageTitleIcon } from "./PageTitleIcon";
import { PcDetailsPanel } from "./PcDetailsPanel";
import { PcMonitorTile } from "./PcMonitorTile";
import { PaymentDialog } from "./PaymentDialog";
import { SecretSelect, type SecretSelectOption } from "./SecretSelect";
import { SessionDialog } from "./SessionDialog";
import { StationAssignmentManager } from "./StationAssignmentManager";
import { ActivityDrawerTrigger } from "./ActivityFeed";
import { ConfirmActionDialog } from "./ConfirmActionDialog";
import { useToast } from "./ToastProvider";

type DialogState =
  | { type: "start"; pc: PcSummary }
  | { type: "add"; pc: PcSummary }
  | { type: "end"; pc: PcSummary }
  | { type: "resolve"; pc: PcSummary }
  | { type: "payment"; pc: PcSummary }
  | { type: "announcement" }
  | null;

// Sentinel area label the backend writes when a client registers with a
// pc_name that has no owner-configured layout row yet (server/main.py
// upsert_client_display_name). These are surfaced in their own
// "Unassigned / Auto-detected" zone, not mixed into the real shop areas.
const AUTO_DETECTED_AREA = "CLIENT";

function displayAreaLabel(label: string | null): string {
  if (label === "SECRET GROUND SHOP") {
    return "SECRET GROUNDS SHOP";
  }
  if (label === AUTO_DETECTED_AREA) {
    return "UNASSIGNED / AUTO-DETECTED";
  }
  return label ?? "UNASSIGNED";
}

export function DashboardClient() {
  const [pcs, setPcs] = useState<PcSummary[]>([]);
  const [rates, setRates] = useState<RateProfile[]>([]);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [operatorSale, setOperatorSale] = useState<OperatorSaleSummary | null>(null);
  const [layoutSlots, setLayoutSlots] = useState<PcLayoutSlot[]>([]);
  const [incomeGroups, setIncomeGroups] = useState<IncomeGroup[]>([]);
  const [showSetupLayout, setShowSetupLayout] = useState(false);
  const [showRegisterPcInfo, setShowRegisterPcInfo] = useState(false);
  const [stationRefreshToken, setStationRefreshToken] = useState(0);
  const [isConnected, setIsConnected] = useState(false);
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogState>(null);
  const [selectedPcName, setSelectedPcName] = useState<string | null>(null);
  const [isMutating, setIsMutating] = useState(false);
  const [now, setNow] = useState<Date | null>(null);
  const isPollingRef = useRef(false);
  const pollIntervalMs = getPollingIntervalMs();
  const { pushToast } = useToast();
  const { statusByPcName, frameUrlByPcName, frameErrorByPcName } = useMonitorFrames(selectedPcName);

  const refresh = useCallback(async () => {
    if (isPollingRef.current) {
      return;
    }

    isPollingRef.current = true;
    try {
      const [nextPcs, nextRates, nextUser] = await Promise.all([
        fetchPcs(),
        fetchRates(),
        fetchCurrentUser()
      ]);
      fetchOperatorSale()
        .then(setOperatorSale)
        .catch(() => setOperatorSale(null));
      setPcs(nextPcs);
      setRates(nextRates);
      setCurrentUser(nextUser);
      if (nextUser.role !== "owner") {
        setShowSetupLayout(false);
        setLayoutSlots([]);
      }
      setIsConnected(true);
      setHasLoadedOnce(true);
      setMessage(null);
    } catch (error) {
      setIsConnected(false);
      setHasLoadedOnce(true);
      setMessage(
        error instanceof Error
          ? error.message
          : "Server unavailable. Showing the most recent successful data."
      );
    } finally {
      isPollingRef.current = false;
    }
  }, []);

  useEffect(() => {
    const initialFetchId = window.setTimeout(() => void refresh(), 0);
    const intervalId = window.setInterval(() => void refresh(), pollIntervalMs);
    return () => {
      window.clearTimeout(initialFetchId);
      window.clearInterval(intervalId);
    };
  }, [pollIntervalMs, refresh]);

  useEffect(() => {
    const updateClock = () => setNow(new Date());
    const initialClockId = window.setTimeout(updateClock, 0);
    const intervalId = window.setInterval(updateClock, 1000);
    return () => {
      window.clearTimeout(initialClockId);
      window.clearInterval(intervalId);
    };
  }, []);

  const livePcs = useMemo(
    () =>
      pcs.map((pc) => {
        const nowMs = now?.getTime();
        if (
          !["active", "grace_period", "shutting_down"].includes(pc.session_status) ||
          nowMs === undefined
        ) {
          return pc;
        }
        if (pc.session_status === "active" && pc.session_mode === "open_time") {
          const estimate = calculateOpenTimeEstimate(
            pc.started_at,
            nowMs,
            pc.metered_rate_duration_seconds_snapshot,
            pc.metered_rate_price_snapshot,
            pc.total_paid
          );
          if (estimate === null) {
            return pc;
          }

          return {
            ...pc,
            actual_elapsed_seconds: estimate.actualElapsedSeconds,
            billed_seconds: estimate.billedSeconds,
            estimated_total_amount: estimate.estimatedTotalAmount,
            estimated_balance_due: estimate.estimatedBalanceDue,
            total_amount: estimate.estimatedTotalAmount,
            balance_due: estimate.estimatedBalanceDue
          };
        }

        return {
          ...pc,
          remaining_seconds: secondsFromScheduledEnd(
            pc.session_status === "grace_period" || pc.session_status === "shutting_down"
              ? pc.shutdown_at
              : pc.scheduled_end_at,
            nowMs
          )
        };
      }),
    [pcs, now]
  );
  const summary = summarizePcs(livePcs);
  const selectedPc = useMemo(
    () => livePcs.find((pc) => pc.pc_name === selectedPcName) ?? null,
    [livePcs, selectedPcName]
  );
  // The operational PC grid is one unified panel in strict numeric station
  // order -- income group/area is accounting-only data (Sales reports,
  // backend attribution) and must never split this grid into sections.
  // Setup/Layout below is a separate, owner-only configuration view and
  // keeps its own area grouping (groupedLayoutSlots) unchanged.
  const orderedPcs = useMemo(() => sortPcsByStationNumber(livePcs), [livePcs]);
  const registeredPcNames = useMemo(() => new Set(livePcs.map((pc) => pc.pc_name)), [livePcs]);
  // Real shop areas (top zone), grouped by area label. Auto-detected
  // sentinel slots are pulled out into their own zone below.
  const groupedLayoutSlots = useMemo(() => {
    const groups = new Map<string, PcLayoutSlot[]>();
    for (const slot of layoutSlots) {
      if (slot.area_label === AUTO_DETECTED_AREA) {
        continue;
      }
      const label = displayAreaLabel(slot.area_label);
      groups.set(label, [...(groups.get(label) ?? []), slot]);
    }
    return Array.from(groups.entries());
  }, [layoutSlots]);

  // Slots a client auto-created by registering with an unrecognised
  // pc_name: leftover test stations, or a genuinely new PC waiting to be
  // placed into a real area.
  const unassignedLayoutSlots = useMemo(
    () => layoutSlots.filter((slot) => slot.area_label === AUTO_DETECTED_AREA),
    [layoutSlots]
  );

  async function runAction(action: () => Promise<unknown>, successMessage: string) {
    setIsMutating(true);
    try {
      await action();
      setMessage(successMessage);
      pushToast(successMessage, "success");
      setDialog(null);
      await refresh();
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Action failed. Please try again.";
      setMessage(errorMessage);
      pushToast(errorMessage, "error");
    } finally {
      setIsMutating(false);
    }
  }

  async function retryWakeAction(pc: PcSummary) {
    if (!pc.prepared_session_id) {
      setMessage("No prepared startup is waiting for this PC.");
      return;
    }
    void runAction(
      () => retryPreparedWake(pc.prepared_session_id as string),
      `Wake signal sent again for ${pc.display_name ?? pc.pc_name}.`
    );
  }

  async function cancelPreparedAction(pc: PcSummary) {
    if (!pc.prepared_session_id) {
      setMessage("No prepared startup is waiting for this PC.");
      return;
    }
    void runAction(
      () => cancelPreparedSession(pc.prepared_session_id as string),
      `${pc.display_name ?? pc.pc_name} prepared startup cancelled. No charge was created.`
    );
  }

  async function loadSetupLayout() {
    setIsMutating(true);
    try {
      const [slots, groups] = await Promise.all([fetchPcLayouts(true), fetchIncomeGroups()]);
      setLayoutSlots(slots);
      setIncomeGroups(groups);
      setShowSetupLayout(true);
      setStationRefreshToken((token) => token + 1);
      setMessage("Setup/Layout mode is showing configured slots. Unregistered slots are not counted in operations.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load layout setup.");
    } finally {
      setIsMutating(false);
    }
  }

  async function hideLayoutSlot(slot: PcLayoutSlot) {
    setIsMutating(true);
    try {
      await updatePcLayout(slot.pc_name, {
        display_name: slot.display_name,
        area_label: slot.area_label,
        layout_order: slot.layout_order,
        is_vip: slot.is_vip,
        is_enabled: false,
        mac_address: slot.mac_address
      });
      const slots = await fetchPcLayouts(true);
      setLayoutSlots(slots);
      setMessage(`${slot.display_name} hidden from setup layout.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not hide this layout slot.");
    } finally {
      setIsMutating(false);
    }
  }

  async function removeLayoutSlot(slot: PcLayoutSlot) {
    if (
      !window.confirm(
        `Permanently remove "${slot.display_name}" (${slot.pc_name})?\n\n` +
          "This deletes the auto-detected slot entirely. It cannot be undone. " +
          "A real station that later reconnects will simply reappear here."
      )
    ) {
      return;
    }
    setIsMutating(true);
    try {
      await deletePcLayout(slot.pc_name);
      const slots = await fetchPcLayouts(true);
      setLayoutSlots(slots);
      const message = `${slot.display_name} removed from the layout.`;
      setMessage(message);
      pushToast(message, "success");
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Could not remove this slot.";
      setMessage(errorMessage);
      pushToast(errorMessage, "error");
    } finally {
      setIsMutating(false);
    }
  }

  async function changeStationType(slot: PcLayoutSlot, isVip: boolean) {
    if (slot.is_vip === isVip) {
      return;
    }
    setIsMutating(true);
    try {
      await updatePcLayout(slot.pc_name, {
        display_name: slot.display_name,
        area_label: slot.area_label,
        layout_order: slot.layout_order,
        is_vip: isVip,
        is_enabled: slot.is_enabled,
        mac_address: slot.mac_address,
        income_group_id: slot.income_group_id
      });
      const slots = await fetchPcLayouts(true);
      setLayoutSlots(slots);
      const message = `${slot.display_name} is now classified as ${isVip ? "VIP" : "Standard"}. This determines which rates and member-balance rules apply to this station.`;
      setMessage(message);
      pushToast(message, "success");
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : "Could not change this station's computer type.";
      setMessage(errorMessage);
      pushToast(errorMessage, "error");
    } finally {
      setIsMutating(false);
    }
  }

  async function changeIncomeGroup(slot: PcLayoutSlot, incomeGroupId: string | null) {
    if (slot.income_group_id === incomeGroupId) {
      return;
    }
    setIsMutating(true);
    try {
      await updatePcLayout(slot.pc_name, {
        display_name: slot.display_name,
        area_label: slot.area_label,
        layout_order: slot.layout_order,
        is_vip: slot.is_vip,
        is_enabled: slot.is_enabled,
        mac_address: slot.mac_address,
        income_group_id: incomeGroupId
      });
      const slots = await fetchPcLayouts(true);
      setLayoutSlots(slots);
      const groupName = incomeGroups.find((group) => group.income_group_id === incomeGroupId)?.name ?? "no group";
      const message = `${slot.display_name} income is now attributed to ${groupName}.`;
      setMessage(message);
      pushToast(message, "success");
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : "Could not change this station's income group.";
      setMessage(errorMessage);
      pushToast(errorMessage, "error");
    } finally {
      setIsMutating(false);
    }
  }

  async function endSessionAction(pc: PcSummary) {
    setIsMutating(true);
    try {
      const response = await endSession(pc.active_session_id as string);
      if (pc.session_mode === "open_time") {
        const settlementPc: PcSummary = {
          ...pc,
          active_session_id: response.session.session_id,
          session_status: response.session.status === "settlement_pending" ? "settlement_pending" : "active",
          total_amount: response.session.total_amount,
          total_paid: response.session.total_paid,
          balance_due: response.session.balance_due,
          payment_status: response.session.payment_status,
          billed_seconds: response.session.billed_seconds,
          final_computed_amount: response.session.final_computed_amount,
          actual_elapsed_seconds: response.session.actual_elapsed_seconds,
          settlement_pending: response.session.settlement_pending
        };
        setMessage(`${pc.display_name ?? pc.pc_name} open-time session ended. Settlement is pending.`);
        pushToast(`${pc.display_name ?? pc.pc_name} open-time session ended. Settlement is pending.`, "warning");
        setDialog({ type: "payment", pc: settlementPc });
      } else {
        setMessage(`${pc.display_name ?? pc.pc_name} session ended.`);
        pushToast(`${pc.display_name ?? pc.pc_name} session ended.`, "success");
        setDialog(null);
      }
      await refresh();
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Action failed. Please try again.";
      setMessage(errorMessage);
      pushToast(errorMessage, "error");
    } finally {
      setIsMutating(false);
    }
  }

  async function forceEndSessionAction(pc: PcSummary) {
    if (!pc.active_session_id) {
      setMessage("This PC no longer has an active session.");
      setDialog(null);
      return;
    }

    setIsMutating(true);
    try {
      const response = await forceEndSession(pc.active_session_id);
      if (response.session.session_mode === "open_time" && response.session.settlement_pending) {
        const settlementPc: PcSummary = {
          ...pc,
          active_session_id: response.session.session_id,
          session_status: response.session.status === "settlement_pending" ? "settlement_pending" : "active",
          total_amount: response.session.total_amount,
          total_paid: response.session.total_paid,
          balance_due: response.session.balance_due,
          payment_status: response.session.payment_status,
          billed_seconds: response.session.billed_seconds,
          final_computed_amount: response.session.final_computed_amount,
          actual_elapsed_seconds: response.session.actual_elapsed_seconds,
          settlement_pending: response.session.settlement_pending,
          recovery_required: false,
          recovery_reason: null
        };
        setMessage(`${pc.display_name ?? pc.pc_name} recovery session force-ended. Settlement is pending.`);
        pushToast(`${pc.display_name ?? pc.pc_name} recovery session force-ended. Settlement is pending.`, "warning");
        setDialog({ type: "payment", pc: settlementPc });
      } else {
        setMessage(`${pc.display_name ?? pc.pc_name} recovery session resolved.`);
        pushToast(`${pc.display_name ?? pc.pc_name} recovery session resolved.`, "success");
        setDialog(null);
      }
      await refresh();
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Recovery action failed.";
      setMessage(errorMessage);
      pushToast(errorMessage, "error");
    } finally {
      setIsMutating(false);
    }
  }

  function findCurrentPc(pc: PcSummary): PcSummary {
    const currentPc = livePcs.find((item) => item.pc_name === pc.pc_name);
    if (pc.active_session_id && !currentPc?.active_session_id) {
      return pc;
    }

    return currentPc ?? pc;
  }

  function openDetails(pc: PcSummary) {
    setSelectedPcName(pc.pc_name);
    setDialog(null);
  }

  function renderLayoutSlotCard(slot: PcLayoutSlot, options?: { showRemove?: boolean }) {
    const isRegistered = registeredPcNames.has(slot.pc_name);
    return (
      <div
        className={`${styles.monitorCard} ${styles.setupSlotCard} ${
          !slot.is_enabled ? styles.disabledSetupSlot : ""
        }`}
        key={slot.pc_name}
      >
        <div className={styles.pcAreaRow}>
          <span>{displayAreaLabel(slot.area_label)}</span>
          {slot.is_vip ? (
            <span className={styles.vipBadge}>VIP</span>
          ) : (
            <span className={styles.standardBadge}>Standard</span>
          )}
        </div>
        <div className={styles.monitorFace}>
          <h3>{slot.display_name}</h3>
          <span className={isRegistered ? styles.installedStatus : styles.notInstalledStatus}>
            {isRegistered ? "Registered" : "Not Installed"}
          </span>
        </div>
        <div className={styles.computerTypeControl}>
          <span className={styles.computerTypeLabel}>Computer Type</span>
          <div className={styles.computerTypeToggle} role="group" aria-label={`${slot.display_name} computer type`}>
            <button
              aria-pressed={!slot.is_vip}
              className={!slot.is_vip ? styles.computerTypeOptionActive : styles.computerTypeOption}
              disabled={isMutating}
              onClick={() => void changeStationType(slot, false)}
              type="button"
            >
              Standard
            </button>
            <button
              aria-pressed={slot.is_vip}
              className={slot.is_vip ? styles.computerTypeOptionActive : styles.computerTypeOption}
              disabled={isMutating}
              onClick={() => void changeStationType(slot, true)}
              type="button"
            >
              VIP
            </button>
          </div>
          <p className={styles.computerTypeHint}>
            This determines which rates and member-balance rules apply to this station.
          </p>
        </div>
        <div className={styles.incomeGroupControl}>
          <label className={styles.incomeGroupLabel} htmlFor={`income-group-${slot.pc_name}`}>
            Income Group
          </label>
          <select
            className={styles.incomeGroupSelect}
            disabled={isMutating}
            id={`income-group-${slot.pc_name}`}
            onChange={(event) => void changeIncomeGroup(slot, event.target.value === "" ? null : event.target.value)}
            value={slot.income_group_id ?? ""}
          >
            <option value="">Not assigned</option>
            {incomeGroups.map((group) => (
              <option key={group.income_group_id} value={group.income_group_id}>
                {group.name}
              </option>
            ))}
          </select>
          <p className={styles.incomeGroupHint}>
            Determines which income group receives payments attributed to this station.
          </p>
        </div>
        <div className={styles.setupSlotActions}>
          {options?.showRemove ? (
            <button
              className={styles.dangerButton}
              disabled={isMutating}
              onClick={() => void removeLayoutSlot(slot)}
              type="button"
            >
              Remove
            </button>
          ) : (
            <button
              className={styles.secondaryButton}
              onClick={() => {
                setMessage("Use the PC details/settings flow to configure slot metadata.");
              }}
              type="button"
            >
              Configure
            </button>
          )}
          <button
            className={styles.dangerButton}
            disabled={isMutating || !slot.is_enabled}
            onClick={() => void hideLayoutSlot(slot)}
            type="button"
          >
            Hide Slot
          </button>
        </div>
      </div>
    );
  }

  const currentTime = now === null ? "Loading time..." : now.toLocaleString();

  return (
    <main className={styles.page}>
      <header className={styles.topBar}>
        <div className={`${styles.brandBlock} pageTitleLockup`}>
          <PageTitleIcon name="dashboard" />
          <div>
            <h1>Dashboard</h1>
            <p>Cashier session dashboard</p>
          </div>
        </div>
        <div className={styles.serverState}>
          <div className={styles.timestamp}>{currentTime}</div>
          <div
            className={`${styles.indicator} ${
              isConnected ? styles.connected : styles.disconnected
            }`}
          >
            Server {isConnected ? "Connected" : "Disconnected"}
          </div>
          <button className={styles.utilityButton} onClick={() => setDialog({ type: "announcement" })} type="button">
            <Megaphone aria-hidden="true" className={styles.utilityIcon} />
            Announcement
          </button>
          <ActivityDrawerTrigger icon={<ActivityPulseIcon />} />
          {currentUser?.role === "owner" ? (
            <button
              className={styles.utilityButton}
              disabled={isMutating}
              onClick={() => {
                if (showSetupLayout) {
                  setShowSetupLayout(false);
                  setMessage(null);
                } else {
                  void loadSetupLayout();
                }
              }}
              type="button"
            >
              <LayoutGridIcon />
              {showSetupLayout ? "Operational View" : "Setup/Layout"}
            </button>
          ) : null}
        </div>
      </header>

      <DashboardSummary
        currentOperator={currentUser?.display_name ?? "Loading"}
        summary={summary}
        operatorSale={operatorSale}
      />

      {message ? <div className={styles.statusMessage}>{message}</div> : null}

      <div className={styles.dashboardWorkspace}>
        <section className={styles.pcGrid} aria-label="PC grid">
          {!hasLoadedOnce
            ? Array.from({ length: 12 }, (_, index) => (
                <div className={styles.loadingCard} key={index}>
                  Loading station...
                </div>
              ))
            : null}
          {hasLoadedOnce && livePcs.length === 0 ? (
            <div className={styles.emptyState}>
              <div>
                <span className={styles.emptyStateGlyph} aria-hidden="true">
                  <svg viewBox="0 0 56 44" fill="none">
                    <rect x="2" y="2" width="24" height="16" rx="3" stroke="currentColor" strokeWidth="2.2" />
                    <rect x="30" y="2" width="24" height="16" rx="3" stroke="currentColor" strokeWidth="2.2" opacity="0.4" />
                    <rect x="2" y="24" width="24" height="16" rx="3" stroke="currentColor" strokeWidth="2.2" opacity="0.4" />
                    <circle className={styles.emptyStatePlusRing} cx="42" cy="32" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
                    <path d="M42 28v8M38 32h8" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
                  </svg>
                </span>
                <h2>No registered stations yet.</h2>
                <p>Install and register the first Secret Grounds client to begin operations.</p>
                <button
                  className={styles.emptyStateButton}
                  onClick={() => setShowRegisterPcInfo(true)}
                  type="button"
                >
                  Register First PC
                </button>
              </div>
            </div>
          ) : null}
          {hasLoadedOnce && livePcs.length > 0 ? (
            <div className={styles.monitorGrid}>
              {orderedPcs.map((pc) => (
                <PcMonitorTile
                  key={pc.pc_name}
                  isSelected={selectedPcName === pc.pc_name}
                  pc={pc}
                  onSelect={openDetails}
                />
              ))}
            </div>
          ) : null}
        </section>
        <aside className={styles.selectedPcRail} aria-label="Selected PC controls">
          {selectedPc ? (
            <PcDetailsPanel
              currentUserRole={currentUser?.role ?? null}
              currentUserIsProtected={currentUser?.is_protected ?? false}
              frameHasError={frameErrorByPcName[selectedPc.pc_name]}
              frameUrl={frameUrlByPcName[selectedPc.pc_name]}
              monitorStatus={statusByPcName[selectedPc.pc_name]}
              onActionComplete={() => void refresh()}
              onAddTime={(selectedPanelPc) => setDialog({ type: "add", pc: selectedPanelPc })}
              onCancelPrepared={(selectedPanelPc) => void cancelPreparedAction(selectedPanelPc)}
              pc={selectedPc}
              onClose={() => setSelectedPcName(null)}
              onEnd={(selectedPanelPc) => setDialog({ type: "end", pc: selectedPanelPc })}
              onPayment={(selectedPanelPc) => setDialog({ type: "payment", pc: selectedPanelPc })}
              onResolve={(selectedPanelPc) => setDialog({ type: "resolve", pc: selectedPanelPc })}
              onRetryWake={(selectedPanelPc) => void retryWakeAction(selectedPanelPc)}
              onStart={(selectedPanelPc) => setDialog({ type: "start", pc: selectedPanelPc })}
              variant="dock"
            />
          ) : (
            <div className={styles.noSelectionPanel}>
              <span className={styles.noSelectionGlyph} aria-hidden="true">
                <svg viewBox="0 0 48 48" fill="none">
                  <rect x="5" y="8" width="38" height="25" rx="4" stroke="currentColor" strokeWidth="2.4" />
                  <path d="M18 41h12M24 33v8" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
                  <circle
                    className={styles.noSelectionPing}
                    cx="24"
                    cy="20.5"
                    r="3"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  />
                  <circle cx="24" cy="20.5" r="2" fill="currentColor" />
                </svg>
              </span>
              <h2>Select a PC</h2>
              <p>Pick a station to view its session, billing, messaging, and power controls.</p>
            </div>
          )}
        </aside>
      </div>

      {showSetupLayout && currentUser?.role === "owner" ? (
        <StationAssignmentManager layoutSlots={layoutSlots} pcs={livePcs} refreshToken={stationRefreshToken} />
      ) : null}

      {showSetupLayout && currentUser?.role === "owner" ? (
        <section className={styles.setupLayoutSection} aria-label="Setup layout slots">
          <div className={styles.sectionHeader}>
            <div>
              <h2>Setup / Layout</h2>
              <p>Your shop stations, grouped by area. Slots marked Not Installed are excluded from dashboard counts.</p>
            </div>
          </div>
          <div className={styles.pcLayoutGroups}>
            {groupedLayoutSlots.map(([groupLabel, slots]) => (
              <section
                className={styles.pcLayoutGroup}
                key={groupLabel}
                aria-label={`${groupLabel} setup`}
                style={{ "--setup-cols": Math.min(slots.length, 5) } as CSSProperties}
              >
                <h2>{groupLabel}</h2>
                <div className={styles.monitorGrid}>
                  {slots.map((slot) => renderLayoutSlotCard(slot))}
                </div>
              </section>
            ))}
          </div>
          {unassignedLayoutSlots.length > 0 ? (
            <section
              className={`${styles.pcLayoutGroup} ${styles.unassignedLayoutGroup}`}
              aria-label="Unassigned auto-detected slots"
              style={{ "--setup-cols": Math.min(unassignedLayoutSlots.length, 5) } as CSSProperties}
            >
              <h2>Unassigned / Auto-detected</h2>
              <p className={styles.unassignedLayoutNote}>
                A client checked in with a station name the shop does not recognise. These are usually
                leftover test runs &ndash; use Remove to delete them for good. If it is a real new PC,
                set its Computer Type and Income Group, then give it an area and it moves up above.
                Remove is blocked for any slot that is online now or has session or payment history.
              </p>
              <div className={styles.monitorGrid}>
                {unassignedLayoutSlots.map((slot) => renderLayoutSlotCard(slot, { showRemove: true }))}
              </div>
            </section>
          ) : null}
        </section>
      ) : null}

      {dialog?.type === "start" ? (
        <SessionDialog
          isLoading={isMutating}
          mode="start"
          onCancel={() => setDialog(null)}
          onConfirm={(selection) =>
            void runAction(
              () =>
                "sessionMode" in selection
                  ? dialog.pc.connection_status === "offline"
                    ? prepareStartupSession(dialog.pc.pc_name, selection)
                    : startMemberSession(dialog.pc.pc_name, selection)
                  : dialog.pc.connection_status === "offline"
                    ? prepareStartupSession(dialog.pc.pc_name, {
                        ...selection,
                        operatorId: currentUser?.user_id
                      })
                    : startPrepaidSession(dialog.pc.pc_name, selection),
              dialog.pc.connection_status === "offline"
                ? `Wake signal sent for ${dialog.pc.display_name ?? dialog.pc.pc_name}. Session will start after the client connects.`
                : `${dialog.pc.display_name ?? dialog.pc.pc_name} session started.`
            )
          }
          pc={dialog.pc}
          packageRates={getPackageRatesForPc(rates, dialog.pc)}
          currentOperator={currentUser}
        />
      ) : null}

      {dialog?.type === "add" ? (
        <SessionDialog
          isLoading={isMutating}
          mode="add"
          onCancel={() => setDialog(null)}
          onConfirm={(selection) => {
            const currentPc = findCurrentPc(dialog.pc);
            if (!currentPc.active_session_id) {
              setMessage("This PC no longer has an active session.");
              setDialog(null);
              return;
            }
            void runAction(
              () =>
                "sessionMode" in selection
                  ? addMemberTime(currentPc.active_session_id as string, selection)
                  : addPrepaidTime(currentPc.active_session_id as string, selection),
              `${currentPc.pc_name} time updated.`
            );
          }}
          pc={dialog.pc}
          packageRates={getPackageRatesForPc(rates, dialog.pc)}
        />
      ) : null}

      {dialog?.type === "end" ? (
        <EndSessionDialog
          isLoading={isMutating}
          onCancel={() => setDialog(null)}
          onConfirm={() => {
            const currentPc = findCurrentPc(dialog.pc);
            const actions = getPcActionAvailability(currentPc);
            if (!actions.canEnd || !currentPc.active_session_id) {
              setMessage("This session is no longer available to end.");
              setDialog(null);
              return;
            }
            void endSessionAction(currentPc);
          }}
          pc={findCurrentPc(dialog.pc)}
        />
      ) : null}

      {dialog?.type === "resolve" ? (
        <EndSessionDialog
          isLoading={isMutating}
          onCancel={() => setDialog(null)}
          onConfirm={() => void forceEndSessionAction(findCurrentPc(dialog.pc))}
          pc={findCurrentPc(dialog.pc)}
          recoveryMode
        />
      ) : null}

      {dialog?.type === "payment" ? (
        <PaymentDialog
          isLoading={isMutating}
          onCancel={() => setDialog(null)}
          onConfirm={(input) => {
            const currentPc = findCurrentPc(dialog.pc);
            if (!currentPc.active_session_id) {
              setMessage("This PC no longer has an active session.");
              setDialog(null);
              return;
            }
            if (currentPc.session_mode === "open_time" && currentPc.session_status === "active") {
              setDialog({ type: "end", pc: currentPc });
              setMessage("End the Open Time session first so the final amount can be calculated.");
              return;
            }
            void runAction(
              () => recordPayment(currentPc.active_session_id as string, input),
              `${currentPc.pc_name} payment recorded.`
            );
          }}
          pc={findCurrentPc(dialog.pc)}
        />
      ) : null}

      {dialog?.type === "announcement" ? (
        <AnnouncementDialog
          isLoading={isMutating}
          onCancel={() => setDialog(null)}
          onConfirm={(input) =>
            void runAction(
              () => broadcastAnnouncement(input),
              "Announcement sent."
            )
          }
        />
      ) : null}
      {showRegisterPcInfo ? (
        <ConfirmActionDialog
          centered
          confirmLabel="Got it"
          lede="Install the Secret Grounds client on the gaming PC and choose Customer Computer during setup."
          note="Once it connects to this server, the station appears here automatically — there's no separate registration step in the dashboard."
          onCancel={() => setShowRegisterPcInfo(false)}
          onConfirm={() => setShowRegisterPcInfo(false)}
          title="Register a station"
          tone="info"
        />
      ) : null}
    </main>
  );
}

function ActivityPulseIcon() {
  return (
    <svg aria-hidden="true" className={styles.utilityIcon} fill="none" viewBox="0 0 24 24">
      <path d="M3 12h4l2.2-5 4 10 2.2-5H21" />
    </svg>
  );
}

function LayoutGridIcon() {
  return (
    <svg aria-hidden="true" className={styles.utilityIcon} fill="none" viewBox="0 0 24 24">
      <path d="M4 4h6v6H4z" />
      <path d="M14 4h6v6h-6z" />
      <path d="M4 14h6v6H4z" />
      <path d="M14 14h6v6h-6z" />
    </svg>
  );
}

const SEVERITY_OPTIONS: SecretSelectOption[] = [
  { value: "info", label: "Info" },
  { value: "warning", label: "Warning" },
  { value: "urgent", label: "Urgent" }
];

function AnnouncementDialog({
  isLoading,
  onCancel,
  onConfirm
}: {
  isLoading: boolean;
  onCancel: () => void;
  onConfirm: (input: {
    message: string;
    severity: MessageSeverity;
    duration_seconds?: number;
    require_acknowledgement: boolean;
  }) => void;
}) {
  const [message, setMessage] = useState("");
  const [severity, setSeverity] = useState<MessageSeverity>("info");
  const [duration, setDuration] = useState("30");

  return (
    <div className={styles.overlay} role="presentation">
      <section
        className={`${styles.dialog} ${styles.announcementDialog}`}
        role="dialog"
        aria-modal="true"
        aria-label="Broadcast announcement"
      >
        <div className={styles.announcementHeader}>
          <span className={styles.announcementIcon} aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 9v6h4l7 4V5L8 9H4z" />
              <path d="M18 8.5a4 4 0 0 1 0 7" />
            </svg>
          </span>
          <div>
            <h2>Broadcast Announcement</h2>
            <p>Push a message to every active screen right now.</p>
          </div>
        </div>

        <div className={styles.announcementTargets}>
          <span className={styles.announcementTargetsLabel}>Targets</span>
          <strong>All online PCs with active command connections.</strong>
          <span className={styles.announcementTargetsSub}>Shown to customers as SECRET GROUNDS.</span>
        </div>

        <label className={styles.field}>
          Message
          <textarea
            maxLength={500}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="Please save your work. The shop is closing soon."
            value={message}
          />
          <span className={styles.announcementCounter}>{message.length}/500</span>
        </label>

        <div className={styles.announcementRow}>
          <SecretSelect
            label="Severity"
            options={SEVERITY_OPTIONS}
            value={severity}
            onChange={(next) => setSeverity(next as MessageSeverity)}
          />
          <label className={styles.field}>
            Display seconds
            <input inputMode="numeric" onChange={(event) => setDuration(event.target.value)} value={duration} />
          </label>
        </div>

        {severity === "urgent" ? (
          <p className={styles.announcementNote}>
            Urgent messages require the customer to acknowledge before continuing.
          </p>
        ) : null}

        <div className={styles.dialogActions}>
          <button className={styles.secondaryButton} disabled={isLoading} onClick={onCancel} type="button">
            Cancel
          </button>
          <button
            className={styles.primaryButton}
            disabled={isLoading || !message.trim()}
            onClick={() =>
              onConfirm({
                message,
                severity,
                duration_seconds: Number(duration) || undefined,
                require_acknowledgement: severity === "urgent"
              })
            }
            type="button"
          >
            {isLoading ? "Sending..." : "Send Announcement"}
          </button>
        </div>
      </section>
    </div>
  );
}

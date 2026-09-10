"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "@/app/page.module.css";
import {
  assignStation,
  fetchAssignedStations,
  fetchUnclaimedStations,
  reassignStation,
  revokeStation
} from "@/lib/api";
import { formatDateTime } from "@/lib/time";
import type { PcLayoutSlot, PcSummary, StationAssignment } from "@/lib/types";
import { useToast } from "./ToastProvider";

interface StationAssignmentManagerProps {
  pcs: PcSummary[];
  layoutSlots: PcLayoutSlot[];
  refreshToken: number;
}

// Station Assignment answers "which physical NIC/MAC is PC-01" -- it never
// touches PcLayoutSlot (station type, income owner, display/layout
// configuration). Every mutation here calls the existing Stage 2
// /stations/* endpoints; nothing here talks to /pcs/{pc_name}/layout.
export function StationAssignmentManager({ pcs, layoutSlots, refreshToken }: StationAssignmentManagerProps) {
  const [unclaimed, setUnclaimed] = useState<StationAssignment[]>([]);
  const [assigned, setAssigned] = useState<StationAssignment[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isMutating, setIsMutating] = useState(false);
  const [assignSelections, setAssignSelections] = useState<Record<string, string>>({});
  const [reassignSelections, setReassignSelections] = useState<Record<string, string>>({});
  const { pushToast } = useToast();

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      const [nextUnclaimed, nextAssigned] = await Promise.all([fetchUnclaimedStations(), fetchAssignedStations()]);
      setUnclaimed(nextUnclaimed);
      setAssigned(nextAssigned);
    } catch (error) {
      pushToast(error instanceof Error ? error.message : "Could not load station assignments.", "error");
    } finally {
      setIsLoading(false);
    }
  }, [pushToast]);

  useEffect(() => {
    void load();
    // refreshToken deliberately triggers a reload on every Setup/Layout
    // open -- newly-detected computers appear here without a page reload.
  }, [load, refreshToken]);

  const onlineByPcName = useMemo(
    () => new Map(pcs.map((pc) => [pc.pc_name, pc.connection_status === "online"] as const)),
    [pcs]
  );

  const assignedPcNames = useMemo(
    () => new Set(assigned.map((station) => station.pc_name).filter((name): name is string => name !== null)),
    [assigned]
  );

  const availableSlots = useMemo(
    () =>
      [...layoutSlots]
        .filter((slot) => !assignedPcNames.has(slot.pc_name))
        .sort((a, b) => a.layout_order - b.layout_order),
    [layoutSlots, assignedPcNames]
  );

  function displayNameFor(pcName: string | null): string {
    if (!pcName) {
      return "";
    }
    return layoutSlots.find((slot) => slot.pc_name === pcName)?.display_name ?? pcName;
  }

  async function handleAssign(station: StationAssignment) {
    const targetPcName = assignSelections[station.mac_address];
    if (!targetPcName) {
      pushToast("Choose a station name before assigning this computer.", "error");
      return;
    }
    setIsMutating(true);
    try {
      await assignStation(station.mac_address, targetPcName);
      const message = `Assigned as ${displayNameFor(targetPcName) || targetPcName}. That gaming PC will connect automatically -- no restart needed.`;
      pushToast(message, "success");
      setAssignSelections((current) => {
        const next = { ...current };
        delete next[station.mac_address];
        return next;
      });
      await load();
    } catch (error) {
      pushToast(error instanceof Error ? error.message : "Could not assign this computer.", "error");
    } finally {
      setIsMutating(false);
    }
  }

  async function handleReassign(station: StationAssignment) {
    if (!station.pc_name) {
      return;
    }
    const newMacAddress = reassignSelections[station.pc_name];
    if (!newMacAddress) {
      pushToast("Choose which newly-detected computer should replace this one.", "error");
      return;
    }
    const label = displayNameFor(station.pc_name);
    if (
      !window.confirm(
        `Reassign ${label}?\n\n` +
          `${label} is currently ${station.mac_address}. It will be moved to ${newMacAddress} instead, ` +
          `and ${station.mac_address} will no longer be ${label}.\n\nThis does not affect session, billing, or Setup/Layout history.`
      )
    ) {
      return;
    }
    setIsMutating(true);
    try {
      await reassignStation(newMacAddress, station.pc_name);
      pushToast(`${label} reassigned to ${newMacAddress}.`, "success");
      setReassignSelections((current) => {
        const next = { ...current };
        delete next[station.pc_name as string];
        return next;
      });
      await load();
    } catch (error) {
      pushToast(error instanceof Error ? error.message : "Could not reassign this station.", "error");
    } finally {
      setIsMutating(false);
    }
  }

  async function handleRevoke(station: StationAssignment) {
    if (!station.pc_name) {
      return;
    }
    const label = displayNameFor(station.pc_name);
    if (
      !window.confirm(
        `Unassign ${label} from ${station.mac_address}?\n\n` +
          "The next time this computer starts, it will wait for a new station assignment instead of connecting as " +
          `${label}. This does not delete any session, billing, or Setup/Layout history.`
      )
    ) {
      return;
    }
    setIsMutating(true);
    try {
      await revokeStation(station.mac_address);
      pushToast(`${label} unassigned from ${station.mac_address}.`, "success");
      await load();
    } catch (error) {
      pushToast(error instanceof Error ? error.message : "Could not unassign this station.", "error");
    } finally {
      setIsMutating(false);
    }
  }

  const disabled = isMutating || isLoading;

  return (
    <section className={styles.setupLayoutSection} aria-label="Station assignment">
      <div className={styles.sectionHeader}>
        <div>
          <h2>Station Assignment</h2>
          <p>
            Which physical gaming PC is PC-01, PC-02, and so on. Diskless PCs detect themselves automatically --
            no MAC addresses to type in, and nothing here changes station type, income owner, or display settings.
            A PC set up to adopt its computer name binds itself the first time it starts, as long as a station with
            that name already exists under Setup / Layout; it shows an <strong>Auto</strong> tag below.
          </p>
        </div>
      </div>

      <section className={styles.pcLayoutGroup} aria-label="Newly detected computers">
        <h2>Newly Detected Computers</h2>
        {!isLoading && unclaimed.length === 0 ? (
          <p className={styles.computerTypeHint}>
            No newly-detected computers waiting right now. A diskless PC appears here automatically the first time
            it starts and cannot yet connect as a station.
          </p>
        ) : null}
        <div className={styles.monitorGrid}>
          {unclaimed.map((station) => (
            <div className={`${styles.monitorCard} ${styles.setupSlotCard}`} key={station.mac_address}>
              <div className={styles.monitorFace}>
                <h3>New computer detected</h3>
                <span className={styles.notInstalledStatus}>Waiting for assignment</span>
              </div>
              <dl className={styles.stationMetaList}>
                <div>
                  <dt>MAC</dt>
                  <dd className={styles.macAddress}>{station.mac_address}</dd>
                </div>
                {station.last_hostname ? (
                  <div>
                    <dt>Computer name</dt>
                    <dd>{station.last_hostname}</dd>
                  </div>
                ) : null}
                <div>
                  <dt>Last seen IP</dt>
                  <dd>{station.last_seen_ip ?? "Unknown"}</dd>
                </div>
                <div>
                  <dt>First seen</dt>
                  <dd>{formatDateTime(station.first_seen_at)}</dd>
                </div>
                <div>
                  <dt>Last seen</dt>
                  <dd>{formatDateTime(station.last_seen_at)}</dd>
                </div>
              </dl>
              {station.last_hostname ? (
                <p className={styles.computerTypeHint}>
                  This PC calls itself <strong>{station.last_hostname}</strong>. To let it bind automatically, add a
                  station with that exact name under Setup / Layout.
                </p>
              ) : null}
              <div className={styles.incomeGroupControl}>
                <label className={styles.incomeGroupLabel} htmlFor={`assign-${station.mac_address}`}>
                  Assign as
                </label>
                <select
                  className={styles.incomeGroupSelect}
                  disabled={disabled || availableSlots.length === 0}
                  id={`assign-${station.mac_address}`}
                  onChange={(event) =>
                    setAssignSelections((current) => ({ ...current, [station.mac_address]: event.target.value }))
                  }
                  value={assignSelections[station.mac_address] ?? ""}
                >
                  <option value="">Choose a station...</option>
                  {availableSlots.map((slot) => (
                    <option key={slot.pc_name} value={slot.pc_name}>
                      {slot.display_name} ({slot.pc_name})
                    </option>
                  ))}
                </select>
                {availableSlots.length === 0 ? (
                  <p className={styles.incomeGroupHint}>
                    Every configured station already has a computer assigned. Add a new station under Setup / Layout
                    first, or reassign an existing one below.
                  </p>
                ) : null}
              </div>
              <div className={styles.setupSlotActions}>
                <button
                  className={styles.primaryButton}
                  disabled={disabled || !assignSelections[station.mac_address]}
                  onClick={() => void handleAssign(station)}
                  type="button"
                >
                  Assign
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className={styles.pcLayoutGroup} aria-label="Assigned stations">
        <h2>Assigned Stations</h2>
        <div className={styles.monitorGrid}>
          {assigned.map((station) => {
            const isOnline = station.pc_name !== null && onlineByPcName.get(station.pc_name) === true;
            return (
              <div className={`${styles.monitorCard} ${styles.setupSlotCard}`} key={station.mac_address}>
                <div className={styles.monitorFace}>
                  <h3>{displayNameFor(station.pc_name)}</h3>
                  <span className={isOnline ? styles.installedStatus : styles.notInstalledStatus}>
                    {isOnline ? "Online" : "Offline"}
                  </span>
                </div>
                <dl className={styles.stationMetaList}>
                  <div>
                    <dt>Bound by</dt>
                    <dd>
                      {station.assignment_source === "auto-hostname"
                        ? `Auto (adopted from computer name${station.last_hostname ? ` "${station.last_hostname}"` : ""})`
                        : "Assigned in dashboard"}
                    </dd>
                  </div>
                  <div>
                    <dt>MAC</dt>
                    <dd className={styles.macAddress}>{station.mac_address}</dd>
                  </div>
                  <div>
                    <dt>Last seen IP</dt>
                    <dd>{station.last_seen_ip ?? "Unknown"}</dd>
                  </div>
                  <div>
                    <dt>Last seen</dt>
                    <dd>{formatDateTime(station.last_seen_at)}</dd>
                  </div>
                  <div>
                    <dt>Assigned</dt>
                    <dd>{formatDateTime(station.assigned_at)}</dd>
                  </div>
                </dl>
                <div className={styles.incomeGroupControl}>
                  <label className={styles.incomeGroupLabel} htmlFor={`reassign-${station.mac_address}`}>
                    Replace with newly-detected computer
                  </label>
                  <select
                    className={styles.incomeGroupSelect}
                    disabled={disabled || unclaimed.length === 0}
                    id={`reassign-${station.mac_address}`}
                    onChange={(event) =>
                      setReassignSelections((current) => ({
                        ...current,
                        [station.pc_name as string]: event.target.value
                      }))
                    }
                    value={(station.pc_name && reassignSelections[station.pc_name]) ?? ""}
                  >
                    <option value="">Choose a computer...</option>
                    {unclaimed.map((candidate) => (
                      <option key={candidate.mac_address} value={candidate.mac_address}>
                        {candidate.mac_address}
                      </option>
                    ))}
                  </select>
                  {unclaimed.length === 0 ? (
                    <p className={styles.incomeGroupHint}>
                      No newly-detected computer available yet. Have the replacement PC start and connect first --
                      it will appear above once it does.
                    </p>
                  ) : null}
                </div>
                <div className={styles.setupSlotActions}>
                  <button
                    className={styles.secondaryButton}
                    disabled={disabled || !station.pc_name || !reassignSelections[station.pc_name ?? ""]}
                    onClick={() => void handleReassign(station)}
                    type="button"
                  >
                    Reassign
                  </button>
                  <button
                    className={styles.dangerButton}
                    disabled={disabled}
                    onClick={() => void handleRevoke(station)}
                    type="button"
                  >
                    Unassign
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </section>
    </section>
  );
}

import type { DashboardSummary, PcSummary } from "./types";

export type PcCardTone = "active" | "available" | "offline" | "warning";

export interface PcActionAvailability {
  canStart: boolean;
  canAddTime: boolean;
  canEnd: boolean;
  canCollectPayment: boolean;
  canViewDetails: boolean;
}

export interface PcMonitorPresentation {
  className:
    | "monitorTileAvailable"
    | "monitorTileActive"
    | "monitorTileLowTime"
    | "monitorTileWarning"
    | "monitorTileOffline"
    | "monitorTileWaking";
  label:
    | "Available"
    | "In Use"
    | "Grace Period"
    | "Low Time"
    | "Shutting Down"
    | "Shutdown Failed"
    | "Powered Off"
    | "Waking"
    | "Settlement"
    | "Recovery";
}

export function getPcMonitorPresentation(pc: PcSummary): PcMonitorPresentation {
  if (pc.recovery_required || pc.session_status === "unknown") {
    return { className: "monitorTileWarning", label: "Recovery" };
  }

  if (pc.startup_status !== null || pc.power_state === "waking" || pc.power_state === "starting") {
    return { className: "monitorTileWaking", label: "Waking" };
  }

  if (pc.power_state === "shutdown_failed") {
    return { className: "monitorTileWarning", label: "Shutdown Failed" };
  }

  if (pc.power_state === "shutting_down" || pc.session_status === "shutting_down") {
    return { className: "monitorTileWarning", label: "Shutting Down" };
  }

  if (pc.session_status === "settlement_pending" || pc.session_status === "ending") {
    return { className: "monitorTileWarning", label: "Settlement" };
  }

  if (pc.session_status === "grace_period") {
    return { className: "monitorTileWarning", label: "Grace Period" };
  }

  if (pc.session_status === "active") {
    if (
      pc.session_mode !== "open_time" &&
      pc.remaining_seconds !== null &&
      pc.remaining_seconds <= 300
    ) {
      return { className: "monitorTileLowTime", label: "Low Time" };
    }

    return { className: "monitorTileActive", label: "In Use" };
  }

  if (pc.connection_status === "offline") {
    return { className: "monitorTileOffline", label: "Powered Off" };
  }

  return { className: "monitorTileAvailable", label: "Available" };
}

export function getPcCardTone(pc: PcSummary): PcCardTone {
  if (pc.connection_status === "offline") {
    return pc.session_status === "unknown" ||
      pc.session_status === "settlement_pending" ||
      pc.startup_status !== null
      ? "warning"
      : "offline";
  }

  if (pc.power_state === "shutdown_failed" || pc.power_state === "shutting_down") {
    return "warning";
  }

  if (
    pc.session_status === "settlement_pending" ||
    pc.session_status === "ending" ||
    pc.session_status === "grace_period" ||
    pc.session_status === "shutting_down"
  ) {
    return "warning";
  }

  if (pc.session_status === "active") {
    return "active";
  }

  if (pc.session_status === "unknown") {
    return "warning";
  }

  return "available";
}

export function getPcActionAvailability(pc: PcSummary): PcActionAvailability {
  const isOnline = pc.connection_status === "online";
  const isPoweredOffStartable =
    pc.connection_status === "offline" &&
    pc.session_status === "idle" &&
    pc.mac_address !== null &&
    pc.startup_status === null;
  const isActive = pc.session_status === "active" && pc.active_session_id !== null;
  const isGrace = pc.session_status === "grace_period" && pc.active_session_id !== null;
  const isSettlementPending =
    pc.session_status === "settlement_pending" && pc.active_session_id !== null;

  return {
    canStart:
      (isOnline && pc.session_status === "idle" && pc.startup_status === null) ||
      isPoweredOffStartable,
    canAddTime: isOnline && (isActive || isGrace),
    canEnd: isOnline && (isActive || isGrace),
    canCollectPayment: isSettlementPending,
    canViewDetails: true
  };
}

export function summarizePcs(pcs: PcSummary[]): DashboardSummary {
  return {
    total: pcs.length,
    inUse: pcs.filter((pc) => pc.session_status === "active").length,
    gracePeriod: pcs.filter((pc) => pc.session_status === "grace_period").length,
    shuttingDown: pcs.filter(
      (pc) => pc.session_status === "shutting_down" || pc.power_state === "shutting_down"
    ).length,
    settlementPending: pcs.filter((pc) => pc.session_status === "settlement_pending").length,
    available: pcs.filter(
      (pc) => pc.connection_status === "online" && pc.session_status === "idle"
    ).length,
    offline: pcs.filter(
      (pc) => pc.connection_status === "offline" && pc.startup_status === null
    ).length,
    poweredOff: pcs.filter((pc) => pc.power_state === "powered_off").length
  };
}

import type { DashboardSummary, PcSummary } from "./types";

export type PcCardTone = "active" | "available" | "offline" | "warning";

export interface PcActionAvailability {
  canStart: boolean;
  canAddTime: boolean;
  canEnd: boolean;
  canCollectPayment: boolean;
  canViewDetails: boolean;
}

export function getPcCardTone(pc: PcSummary): PcCardTone {
  if (pc.connection_status === "offline") {
    return pc.session_status === "unknown" || pc.session_status === "settlement_pending"
      ? "warning"
      : "offline";
  }

  if (pc.session_status === "settlement_pending" || pc.session_status === "ending") {
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
  const isActive = pc.session_status === "active" && pc.active_session_id !== null;
  const isSettlementPending =
    pc.session_status === "settlement_pending" && pc.active_session_id !== null;

  return {
    canStart: isOnline && pc.session_status === "idle",
    canAddTime: isOnline && isActive,
    canEnd: isOnline && isActive,
    canCollectPayment: isSettlementPending,
    canViewDetails: true
  };
}

export function summarizePcs(pcs: PcSummary[]): DashboardSummary {
  return {
    total: pcs.length,
    inUse: pcs.filter(
      (pc) => pc.session_status === "active" || pc.session_status === "settlement_pending"
    ).length,
    available: pcs.filter(
      (pc) => pc.connection_status === "online" && pc.session_status === "idle"
    ).length,
    offline: pcs.filter((pc) => pc.connection_status === "offline").length
  };
}

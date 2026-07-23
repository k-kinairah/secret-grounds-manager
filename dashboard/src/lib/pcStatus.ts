import type { DashboardSummary, PcSummary } from "./types";

export type PcCardTone = "active" | "available" | "offline" | "warning";

export interface PcActionAvailability {
  canStart: boolean;
  canAddTime: boolean;
  canEnd: boolean;
  canViewDetails: boolean;
}

export function getPcCardTone(pc: PcSummary): PcCardTone {
  if (pc.connection_status === "offline") {
    return pc.session_status === "unknown" ? "warning" : "offline";
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

  return {
    canStart: isOnline && pc.session_status === "idle",
    canAddTime: isOnline && isActive,
    canEnd: isOnline && isActive,
    canViewDetails: true
  };
}

export function summarizePcs(pcs: PcSummary[]): DashboardSummary {
  return {
    total: pcs.length,
    inUse: pcs.filter((pc) => pc.session_status === "active").length,
    available: pcs.filter(
      (pc) => pc.connection_status === "online" && pc.session_status === "idle"
    ).length,
    offline: pcs.filter((pc) => pc.connection_status === "offline").length
  };
}

export type ConnectionStatus = "online" | "offline";
export type DashboardSessionStatus = "idle" | "active" | "unknown";
export type SessionStatus =
  | "active"
  | "completed"
  | "ended_manually"
  | "cancelled"
  | "failed";

export interface PcSummary {
  pc_name: string;
  ip_address: string;
  connection_status: ConnectionStatus;
  last_seen: string;
  session_status: DashboardSessionStatus;
  active_session_id: string | null;
  scheduled_end_at: string | null;
  remaining_seconds: number | null;
}

export interface SessionRecord {
  session_id: string;
  pc_name: string;
  status: SessionStatus;
  started_at: string;
  scheduled_end_at: string;
  ended_at: string | null;
  original_seconds: number;
  added_seconds: number;
  total_seconds: number;
  amount_paid: number | null;
  ended_reason: string | null;
  created_at: string;
  rate_id: string | null;
  rate_name_snapshot: string | null;
  base_amount: string;
  additional_amount: string;
  total_amount: string;
}

export interface SessionCommandResponse {
  session: SessionRecord;
  acknowledgements: Array<{
    command_id: string;
    status: "received" | "completed" | "failed";
    message: string | null;
  }>;
}

export interface DashboardSummary {
  total: number;
  inUse: number;
  available: number;
  offline: number;
}

export interface RateProfile {
  rate_id: string;
  name: string;
  duration_seconds: number;
  price: string;
  is_active: boolean;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface RateProfileInput {
  name: string;
  duration_seconds: number;
  price: string;
  sort_order: number;
  is_active?: boolean;
}

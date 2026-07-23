export type ConnectionStatus = "online" | "offline";
export type DashboardSessionStatus = "idle" | "active" | "unknown";
export type SessionStatus =
  | "active"
  | "completed"
  | "ended_manually"
  | "cancelled"
  | "failed";
export type PaymentStatus = "completed" | "voided";
export type PaymentMethod = "cash" | "gcash" | "maya" | "other";
export type BillingPaymentStatus = "unpaid" | "partially_paid" | "paid" | "overpaid";
export type SessionMode = "prepaid" | "open_time";

export interface PcSummary {
  pc_name: string;
  ip_address: string;
  connection_status: ConnectionStatus;
  last_seen: string;
  session_status: DashboardSessionStatus;
  active_session_id: string | null;
  scheduled_end_at: string | null;
  remaining_seconds: number | null;
  rate_name_snapshot: string | null;
  base_amount: string;
  additional_amount: string;
  total_amount: string;
  total_paid: string;
  balance_due: string;
  payment_status: BillingPaymentStatus;
  session_mode: SessionMode;
  actual_elapsed_seconds: number | null;
  billed_seconds: number | null;
  final_computed_amount: string | null;
  settlement_pending: boolean;
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
  total_paid: string;
  balance_due: string;
  payment_status: BillingPaymentStatus;
  session_mode: SessionMode;
  actual_elapsed_seconds: number | null;
  billed_seconds: number | null;
  final_computed_amount: string | null;
  settlement_pending: boolean;
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
  rate_type: "metered" | "package";
  is_active: boolean;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface RateProfileInput {
  name: string;
  duration_seconds: number;
  price: string;
  rate_type?: "metered" | "package";
  sort_order: number;
  is_active?: boolean;
}

export interface CalculatedTimeResponse {
  amount: string;
  duration_seconds: number;
  rate_name: string;
  rate_snapshot: string;
}

export interface PaymentRecord {
  payment_id: string;
  session_id: string;
  amount: string;
  payment_method: PaymentMethod;
  cash_received: string | null;
  change_given: string | null;
  reference_number: string | null;
  notes: string | null;
  status: PaymentStatus;
  created_at: string;
}

export interface PaymentInput {
  payment_method: PaymentMethod;
  amount: string;
  cash_received?: string;
  reference_number?: string;
  notes?: string;
}

export interface PrepaidSessionSelection {
  amount?: string;
  durationSeconds?: number;
  rateId?: string;
  payment: PaymentInput;
}

export interface BillingSummary {
  session: SessionRecord;
  base_amount: string;
  additional_amount: string;
  total_amount: string;
  total_paid: string;
  balance_due: string;
  payment_status: BillingPaymentStatus;
  payments: PaymentRecord[];
}

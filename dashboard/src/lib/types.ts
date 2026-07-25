export type ConnectionStatus = "online" | "offline";
export type DashboardSessionStatus = "idle" | "active" | "ending" | "settlement_pending" | "unknown";
export type SessionStatus =
  | "active"
  | "ending"
  | "settlement_pending"
  | "completed"
  | "ended_manually"
  | "cancelled"
  | "failed";
export type PaymentStatus = "completed" | "voided";
export type PaymentMethod = "cash" | "gcash" | "maya" | "other";
export type BillingPaymentStatus = "unpaid" | "partially_paid" | "paid" | "overpaid";
export type SessionMode = "prepaid" | "open_time";
export type ExtendedSessionMode = SessionMode | "member_balance";
export type UserRole = "owner" | "admin" | "cashier";

export interface PcSummary {
  pc_name: string;
  ip_address: string;
  connection_status: ConnectionStatus;
  last_seen: string;
  session_status: DashboardSessionStatus;
  active_session_id: string | null;
  started_at: string | null;
  scheduled_end_at: string | null;
  remaining_seconds: number | null;
  rate_name_snapshot: string | null;
  base_amount: string;
  additional_amount: string;
  total_amount: string;
  total_paid: string;
  balance_due: string;
  payment_status: BillingPaymentStatus;
  session_mode: ExtendedSessionMode;
  actual_elapsed_seconds: number | null;
  billed_seconds: number | null;
  final_computed_amount: string | null;
  metered_rate_price_snapshot: string | null;
  metered_rate_duration_seconds_snapshot: number | null;
  estimated_total_amount: string | null;
  estimated_balance_due: string | null;
  settlement_pending: boolean;
  recovery_required: boolean;
  recovery_reason: string | null;
  member_id: string | null;
  member_code_snapshot: string | null;
  member_name_snapshot: string | null;
  member_balance_before_seconds: number | null;
  member_balance_after_seconds: number | null;
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
  session_mode: ExtendedSessionMode;
  actual_elapsed_seconds: number | null;
  billed_seconds: number | null;
  final_computed_amount: string | null;
  metered_rate_price_snapshot: string | null;
  metered_rate_duration_seconds_snapshot: number | null;
  estimated_total_amount: string | null;
  estimated_balance_due: string | null;
  settlement_pending: boolean;
  member_id: string | null;
  member_code_snapshot: string | null;
  member_name_snapshot: string | null;
  member_balance_before_seconds: number | null;
  member_balance_after_seconds: number | null;
}

export interface SessionCommandResponse {
  session: SessionRecord;
  acknowledgements: Array<{
    command_id: string;
    status: "received" | "completed" | "failed";
    message: string | null;
  }>;
}

export interface ForceEndSessionResponse {
  session: SessionRecord;
  command: {
    command_id: string;
    type: string;
    payload: Record<string, unknown>;
    created_at: string;
  } | null;
  acknowledgements: Array<{
    command_id: string;
    status: "received" | "completed" | "failed";
    message: string | null;
  }>;
  forced: boolean;
  reason: string;
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
  shift_id: string | null;
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

export interface MemberSessionSelection {
  sessionMode: "member_balance";
  memberId: string;
  durationSeconds?: number;
  memberPin?: string;
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

export interface CurrentUser {
  user_id: string;
  username: string;
  display_name: string;
  role: UserRole;
}

export interface UserAdminRecord extends CurrentUser {
  is_active: boolean;
  created_at: string;
  updated_at: string;
  last_login_at: string | null;
}

export interface UserCreateInput {
  username: string;
  display_name: string;
  password: string;
  role: UserRole;
}

export interface UserUpdateInput {
  username: string;
  display_name: string;
  role: UserRole;
}

export type ShiftStatus = "open" | "closed";

export interface ShiftTotals {
  expected_cash: string;
  gross_session_sales: string;
  cash_payments: string;
  gcash_payments: string;
  maya_payments: string;
  other_payments: string;
  voided_payments: string;
  payment_count: number;
  sessions_handled: number;
  outstanding_unsettled_balance: string;
  review_required_payments: number;
  settlement_pending_sessions: number;
}

export interface CashierShiftRecord {
  shift_id: string;
  user_id: string;
  cashier_name: string | null;
  status: ShiftStatus;
  opened_at: string;
  closed_at: string | null;
  opening_cash: string;
  expected_cash: string | null;
  actual_cash: string | null;
  difference: string | null;
  closing_notes: string | null;
  force_close_reason: string | null;
  opened_by_user_id: string;
  closed_by_user_id: string | null;
  gross_session_sales: string;
  cash_payments: string;
  gcash_payments: string;
  maya_payments: string;
  other_payments: string;
  voided_payments: string;
  payment_count: number;
  sessions_handled: number;
  outstanding_unsettled_balance: string;
  review_required_payments: number;
  settlement_pending_sessions: number;
}

export interface CashierShiftDetail {
  shift: CashierShiftRecord;
  totals: ShiftTotals;
  payments: PaymentRecord[];
}

export interface MemberRecord {
  member_id: string;
  member_code: string;
  full_name: string;
  phone_number: string | null;
  has_pin: boolean;
  status: "active" | "inactive";
  time_balance_seconds: number;
  joined_at: string;
  expires_at: string | null;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface MembershipProduct {
  product_id: string;
  name: string;
  price: string;
  time_seconds: number;
  product_type: "initial_membership" | "member_topup";
  is_active: boolean;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface MemberTimeTransaction {
  transaction_id: string;
  member_id: string;
  transaction_type:
    | "membership_purchase"
    | "promo_topup"
    | "session_reservation_created"
    | "session_usage_finalized"
    | "session_reservation_released"
    | "session_reservation"
    | "session_refund"
    | "manual_adjustment"
    | "expiration_adjustment";
  seconds_delta: number;
  balance_before_seconds: number;
  balance_after_seconds: number;
  amount_paid: string | null;
  related_session_id: string | null;
  related_payment_id: string | null;
  notes: string | null;
  performed_by_user_id: string | null;
  created_at: string;
}

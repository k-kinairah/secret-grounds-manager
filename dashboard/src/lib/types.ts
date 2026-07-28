export type ConnectionStatus = "online" | "offline";
export type DashboardSessionStatus =
  | "idle"
  | "active"
  | "grace_period"
  | "shutting_down"
  | "ending"
  | "settlement_pending"
  | "unknown";
export type PowerState =
  | "online"
  | "powered_off"
  | "shutting_down"
  | "shutdown_failed"
  | "waking"
  | "starting"
  | "connection_problem"
  | "recovery_required";
export type PreparedSessionStatus =
  | "prepared"
  | "waking"
  | "waiting_for_client"
  | "starting"
  | "active"
  | "failed"
  | "cancelled";
export type SessionStatus =
  | "active"
  | "grace_period"
  | "shutting_down"
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
export type RemoteCommandType =
  | "shutdown"
  | "restart"
  | "sleep"
  | "show_message"
  | "show_announcement"
  | "wake_on_lan";
export type RemoteCommandStatus =
  | "pending"
  | "received"
  | "completed"
  | "failed"
  | "rejected"
  | "timed_out";
export type MessageSeverity = "info" | "warning" | "urgent";

export interface PcSummary {
  pc_name: string;
  display_name: string | null;
  area_label: string | null;
  layout_order: number;
  is_vip: boolean;
  is_enabled: boolean;
  ip_address: string;
  connection_status: ConnectionStatus;
  has_command_connection: boolean;
  power_state: PowerState;
  last_seen: string;
  session_status: DashboardSessionStatus;
  startup_status: PreparedSessionStatus | null;
  prepared_session_id: string | null;
  startup_elapsed_seconds: number | null;
  startup_expires_at: string | null;
  startup_failure_reason: string | null;
  active_session_id: string | null;
  started_at: string | null;
  scheduled_end_at: string | null;
  grace_started_at: string | null;
  shutdown_at: string | null;
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
  mac_address: string | null;
}

export interface PcLayoutSlot {
  pc_name: string;
  display_name: string;
  area_label: string;
  layout_order: number;
  is_vip: boolean;
  is_enabled: boolean;
  mac_address: string | null;
  updated_at: string;
}

export interface SessionRecord {
  session_id: string;
  pc_name: string;
  status: SessionStatus;
  started_at: string;
  scheduled_end_at: string;
  grace_started_at: string | null;
  shutdown_at: string | null;
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
  paymentDue: number;
  settlementDueAmount: string;
}

export interface RemoteCommandRecord {
  command_id: string;
  pc_name: string;
  command_type: RemoteCommandType;
  payload_json: string | null;
  status: RemoteCommandStatus;
  requested_by_user_id: string | null;
  requested_by_display_name: string | null;
  requested_at: string;
  received_at: string | null;
  completed_at: string | null;
  failed_at: string | null;
  failure_reason: string | null;
  client_response: string | null;
  forced: boolean;
  force_reason: string | null;
}

export interface RemoteCommandResponse {
  command: RemoteCommandRecord;
  acknowledgements: Array<{
    command_id: string;
    status: "received" | "completed" | "failed";
    message: string | null;
  }>;
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

export interface PreparedSessionSelection extends PrepaidSessionSelection {
  sessionMode?: "prepaid";
  operatorId?: string;
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

export interface OperatorAccess {
  operator_id: string;
  display_name: string;
  password_required: boolean;
  user: CurrentUser | null;
}

export interface UserAdminRecord extends CurrentUser {
  is_active: boolean;
  created_at: string;
  updated_at: string;
  last_login_at: string | null;
}

export interface OperatorManagementRecord extends CurrentUser {
  operator_id: string;
  access_method: "quick" | "password";
  password_required: boolean;
  is_active: boolean;
  is_protected: boolean;
  active_session_count: number;
  archived_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  last_login_at: string | null;
}

export interface OperatorCreateInput {
  username: string;
  display_name: string;
  access_method: "quick" | "password";
  password?: string;
  is_active: boolean;
}

export interface OperatorUpdateInput {
  username: string;
  display_name: string;
  access_method: "quick" | "password";
  is_active: boolean;
}

export interface OperatorActivityRecord {
  timestamp: string;
  operator_id: string | null;
  operator_name: string;
  action: string;
  category: string;
  pc_name: string | null;
  customer: string | null;
  amount: string | null;
  payment_method: string | null;
  result: string;
}

export type SalesPeriod = "today" | "yesterday" | "this_week" | "this_month" | "custom";
export type SalesGrouping = "hour" | "day";
export type SalesType = "pc_session" | "member_purchase" | "other";

export interface SalesSummary {
  total_sales: string;
  cash_total: string;
  gcash_total: string;
  maya_total: string;
  other_total: string;
  payment_count: number;
  outstanding_amount: string;
  session_revenue: string;
  member_purchase_revenue: string;
  other_revenue: string;
  average_transaction_value: string;
}

export interface SalesPaymentMethodBreakdown {
  payment_method: PaymentMethod;
  amount: string;
  payment_count: number;
}

export interface SalesTypeBreakdown {
  sales_type: SalesType;
  label: string;
  amount: string;
  payment_count: number;
}

export interface SalesOperatorBreakdown {
  operator_id: string | null;
  operator_name: string;
  total_amount: string;
  payment_count: number;
  cash_total: string;
  gcash_total: string;
  maya_total: string;
  other_total: string;
}

export interface SalesPCBreakdown {
  pc_name: string;
  total_amount: string;
  payment_count: number;
  paid_session_count: number;
  billable_seconds: number | null;
}

export interface SalesTimeseriesPoint {
  label: string;
  start_at: string;
  amount: string;
  payment_count: number;
}

export interface SalesRecentPayment {
  payment_id: string;
  timestamp: string;
  operator_name: string;
  customer: string | null;
  pc_name: string | null;
  sales_type: SalesType;
  payment_method: PaymentMethod;
  amount: string;
  status: PaymentStatus;
  related_reference: string | null;
}

export interface SalesOutstandingSettlement {
  session_id: string;
  pc_name: string;
  customer: string | null;
  session_end_time: string | null;
  amount_due: string;
  status: SessionStatus;
  payment_status: BillingPaymentStatus;
}

export interface SalesReport {
  period: SalesPeriod;
  start_at: string;
  end_at: string;
  grouping: SalesGrouping;
  summary: SalesSummary;
  payment_methods: SalesPaymentMethodBreakdown[];
  sales_types: SalesTypeBreakdown[];
  operators: SalesOperatorBreakdown[];
  pcs: SalesPCBreakdown[];
  timeseries: SalesTimeseriesPoint[];
  recent_payments: SalesRecentPayment[];
  outstanding_settlements: SalesOutstandingSettlement[];
}

export type ManualEndBehavior = "lock_only" | "lock_and_settle";
export type OverlayPosition = "top-right" | "top-left" | "bottom-right" | "bottom-left";
export type SettingsSalesPeriod = "today" | "yesterday" | "this_week" | "this_month";

export interface AdminSettings {
  general: {
    shop_display_name: string;
    shop_subtitle: string | null;
    pc_naming_prefix: string;
    timezone_display: string;
  };
  sessions: {
    expiration_grace_seconds: number;
    automatic_shutdown_on_expiration: boolean;
    reminder_thresholds_seconds: number[];
    manual_end_default_behavior: ManualEndBehavior;
  };
  customer_client: {
    reminder_sounds_enabled: boolean;
    final_warning_sound_enabled: boolean;
    active_overlay_enabled: boolean;
    overlay_position: OverlayPosition;
    customer_branding_text: string;
  };
  wake_power: {
    wake_on_lan_enabled: boolean;
    shutdown_on_expiration_enabled: boolean;
    expected_shutdown_timeout_seconds: number;
    power_command_safety_status: string;
  };
  members: {
    default_member_pin_policy: "optional";
  };
  reports: {
    default_sales_period: SettingsSalesPeriod;
    csv_timezone_display: string;
  };
}

export interface AdminSettingsResponse {
  settings: AdminSettings;
  environment_only: Record<string, string>;
  read_only: Record<string, string>;
  restart_required_fields: string[];
  updated_at: string | null;
}

export type SystemStatus = "Healthy" | "Warning" | "Offline" | "Error";

export interface SystemBackupRecord {
  filename: string;
  created_at: string;
  size_bytes: number;
  age_seconds: number;
  verified: boolean;
}

export interface SystemHealth {
  server_status: SystemStatus;
  database_status: SystemStatus;
  websocket_status: SystemStatus;
  connected_clients: number;
  offline_clients: number;
  current_version: string;
  database_size_bytes: number;
  last_backup: SystemBackupRecord | null;
  system_uptime_seconds: number;
  storage_status: SystemStatus;
}

export interface SystemDatabaseHealth {
  status: SystemStatus;
  file_size_bytes: number;
  last_modified_at: string | null;
  estimated_row_counts: Record<string, number>;
  integrity: string;
  database_version: number;
  path: string;
}

export interface SystemStorage {
  status: SystemStatus;
  available_bytes: number;
  total_bytes: number;
  backup_folder_size_bytes: number;
  largest_backup: SystemBackupRecord | null;
  backup_folder: string;
  writable: boolean;
}

export interface SystemInfo {
  secret_grounds_version: string;
  python_version: string;
  operating_system: string;
  database_version: number;
  server_start_time: string;
  server_uptime_seconds: number;
  application_mode: string;
}

export interface SystemLogEntry {
  level: "Info" | "Warning" | "Error";
  timestamp: string;
  event: string;
  message: string;
}

export interface SystemClientHealth {
  pc_name: string;
  display_name: string;
  registered: boolean;
  connected: boolean;
  last_heartbeat: string | null;
  version: string | null;
  current_state: string;
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

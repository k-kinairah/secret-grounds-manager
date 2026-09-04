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
  customer_locked: boolean;
}

export type ScreenFrameStatus = "live" | "stale" | "offline" | "no_image";

// PC screen monitoring: read-only projection of a station's latest cached
// screen frame. Deliberately separate from PcSummary (operational session
// state) -- see server/schemas.py's ScreenMonitorPCStatus, which this
// mirrors field-for-field.
export interface ScreenMonitorStatus {
  pc_name: string;
  has_frame: boolean;
  frame_received_at: string | null;
  frame_age_seconds: number | null;
  frame_status: ScreenFrameStatus;
}

export interface PcLayoutSlot {
  pc_name: string;
  display_name: string;
  area_label: string;
  layout_order: number;
  is_vip: boolean;
  is_enabled: boolean;
  mac_address: string | null;
  income_group_id: string | null;
  updated_at: string;
}

export interface IncomeGroup {
  income_group_id: string;
  name: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export type StationAssignmentStatus = "unclaimed" | "assigned" | "revoked";

// Physical-machine identity (mac_address -> pc_name), separate from
// PcLayoutSlot's station type/income-owner/display configuration.
export interface StationAssignment {
  mac_address: string;
  pc_name: string | null;
  status: StationAssignmentStatus;
  first_seen_at: string;
  last_seen_at: string;
  last_seen_ip: string | null;
  assigned_at: string | null;
  assigned_by_user_id: string | null;
  previous_pc_name: string | null;
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
  station_type: "standard" | "vip";
  vip_seconds_consumed: number | null;
  standard_seconds_consumed_at_vip_rate: number | null;
  conversion_applied: boolean;
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

export type PackageStationScope = "standard" | "vip" | "any";

export interface RateProfile {
  rate_id: string;
  name: string;
  duration_seconds: number;
  price: string;
  rate_type: "metered" | "package";
  is_active: boolean;
  sort_order: number;
  station_scope: PackageStationScope;
  guest_only: boolean;
  member_eligible: boolean;
  eligible_start_local_time: string | null;
  eligible_end_local_time: string | null;
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
  station_scope?: PackageStationScope;
}

export type PromoAudience = "standard" | "vip" | "member_topup" | "new_member";
// "purchase" = the original fixed-price/fixed-grant promos.
// "time_based_rate" = temporarily REPLACES the Standard linear
// amount->time conversion for a recurring daily local-time window; never
// limit-accounted (no redemption rows), Standard-only for now.
export type PromoType = "purchase" | "time_based_rate";
export type PromoOwnerState = "draft" | "enabled" | "disabled" | "ended";
export type PromoDisplayStatus = "Draft" | "Scheduled" | "Active" | "Ended" | "Disabled";
export type PromoAmountMode = "exact" | "minimum";
export type PromoStationScope = "standard" | "vip";
export type PromoAppliesTo = "start_session" | "add_time" | "both";
export type PromoAuditAction = "created" | "updated" | "enabled" | "disabled" | "ended" | "reactivated";

// Mirrors server/schemas.py's Promo exactly -- status is always derived
// server-side (server/promo_status.py); the dashboard never recomputes it.
export interface Promo {
  promo_id: string;
  name: string;
  audience: PromoAudience;
  promo_type: PromoType;
  owner_state: PromoOwnerState;
  status: PromoDisplayStatus;
  amount_mode: PromoAmountMode;
  price: string;
  granted_seconds: number;
  station_scope: PromoStationScope | null;
  applies_to: PromoAppliesTo | null;
  starts_at: string;
  ends_at: string;
  priority: number;
  per_member_redemption_limit: number | null;
  total_redemption_limit: number | null;
  // Time-Based Rate only (null for every "purchase" promo). days_of_week
  // uses 0=Monday..6=Sunday and is the day the window OPENS.
  days_of_week: number[] | null;
  window_start_local_time: string | null;
  window_end_local_time: string | null;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
}

// Mirrors server/schemas.py's PromoCreate/PromoUpdate (both PromoFields) --
// created_by_user_id/owner_state/status are deliberately absent; the
// server derives/assigns all of those, never the client.
export interface PromoInput {
  name: string;
  audience: PromoAudience;
  promo_type: PromoType;
  amount_mode: PromoAmountMode;
  price: string;
  granted_seconds: number;
  station_scope: PromoStationScope | null;
  applies_to: PromoAppliesTo | null;
  starts_at: string;
  ends_at: string;
  priority: number;
  per_member_redemption_limit: number | null;
  total_redemption_limit: number | null;
  days_of_week: number[] | null;
  window_start_local_time: string | null;
  window_end_local_time: string | null;
}

// Mirrors server/schemas.py's PromoStateTransition. ends_at is required
// only when reactivating (owner_state="enabled" while currently "ended")
// -- the server is the sole authority on that rule; this type just
// carries the optional field through.
export interface PromoStateTransitionInput {
  owner_state: "enabled" | "disabled" | "ended";
  ends_at?: string;
}

export interface PromoAuditEvent {
  event_id: string;
  promo_id: string;
  action: PromoAuditAction;
  old_values: Record<string, unknown> | null;
  new_values: Record<string, unknown> | null;
  performed_by_user_id: string | null;
  created_at: string;
}

// Mirrors server/schemas.py's PromoRedemption 1:1 -- raw ids only (no
// member/PC name enrichment; that is explicitly out of scope for this
// stage, see server/main.py's GET /promos/{promo_id}/redemptions).
export interface PromoRedemption {
  redemption_id: string;
  promo_id: string;
  promo_name_snapshot: string;
  audience_snapshot: PromoAudience;
  amount_mode_snapshot: PromoAmountMode;
  price_snapshot: string;
  granted_seconds_snapshot: number;
  station_scope_snapshot: PromoStationScope | null;
  applies_to_snapshot: PromoAppliesTo | null;
  starts_at_snapshot: string;
  ends_at_snapshot: string;
  member_id: string | null;
  session_id: string | null;
  payment_id: string;
  base_transaction_id: string | null;
  bonus_transaction_id: string | null;
  pc_name_snapshot: string | null;
  income_group_id_snapshot: string | null;
  income_group_name_snapshot: string | null;
  operator_user_id: string | null;
  voided_at: string | null;
  void_reason: string | null;
  created_at: string;
}

// Mirrors server/schemas.py's TimeBasedRateApplication 1:1 -- the
// per-session/payment usage log for a Time-Based Rate promo. NOT a
// redemption: no limits, no voided_at.
export interface TimeBasedRateApplication {
  application_id: string;
  promo_id: string;
  promo_name_snapshot: string;
  price_snapshot: string;
  granted_seconds_snapshot: number;
  days_of_week_snapshot: number[];
  window_start_snapshot: string;
  window_end_snapshot: string;
  session_id: string | null;
  payment_id: string;
  action: "start_session" | "add_time";
  normal_seconds: number;
  promo_seconds: number;
  amount: string;
  pc_name_snapshot: string | null;
  income_group_id_snapshot: string | null;
  income_group_name_snapshot: string | null;
  operator_user_id: string | null;
  created_at: string;
}

export interface CalculatedTimeResponse {
  amount: string;
  duration_seconds: number;
  rate_name: string;
  rate_snapshot: string;
}

export interface PaymentRecord {
  payment_id: string;
  session_id: string | null;
  amount: string;
  payment_method: PaymentMethod;
  cash_received: string | null;
  change_given: string | null;
  reference_number: string | null;
  notes: string | null;
  idempotency_key: string | null;
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
  idempotency_key?: string;
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
  memberUsername: string;
  memberId?: string;
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
  // The singular protected Admin account. Gates the System-admin tier
  // (System, Remote Commands) on top of the role-based admin gate.
  is_protected: boolean;
}

export interface OperatorAccess {
  operator_id: string;
  display_name: string;
  password_required: boolean;
  user: CurrentUser | null;
}

export interface OperatorManagementRecord extends CurrentUser {
  operator_id: string;
  access_method: "quick" | "password";
  password_required: boolean;
  is_active: boolean;
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
  role: UserRole;
}

export interface OperatorUpdateInput {
  username: string;
  display_name: string;
  access_method: "quick" | "password";
  is_active: boolean;
  role: UserRole;
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
  gross_sales: string;
  refund_total: string;
  net_sales: string;
  cash_received_total: string;
  change_given_total: string;
  non_cash_collected: string;
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

export interface SalesOperatorTransaction {
  payment_id: string;
  timestamp: string;
  customer: string | null;
  pc_name: string | null;
  sales_type: SalesType;
  payment_method: PaymentMethod;
  amount_due: string;
  cash_received: string | null;
  change_given: string | null;
  reference_number: string | null;
  status: PaymentStatus;
  recorded_by_operator: string;
  voided_by_operator: string | null;
  voided_at: string | null;
  void_reason: string | null;
  related_reference: string | null;
  balance_type: "standard" | "vip" | null;
}

export interface SalesOperatorCollection {
  operator_id: string | null;
  operator_name: string;
  transaction_count: number;
  gross_sales: string;
  refunds_voids: string;
  net_sales: string;
  cash_received: string;
  change_given: string;
  expected_cash: string;
  non_cash_collected: string;
  outstanding: string;
  transactions: SalesOperatorTransaction[];
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
  balance_type: "standard" | "vip" | null;
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

export interface SalesIncomeGroupBreakdown {
  income_group_id: string | null;
  income_group_name: string;
  amount: string;
  payment_count: number;
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
  operator_collections: SalesOperatorCollection[];
  pcs: SalesPCBreakdown[];
  timeseries: SalesTimeseriesPoint[];
  recent_payments: SalesRecentPayment[];
  outstanding_settlements: SalesOutstandingSettlement[];
  income_groups: SalesIncomeGroupBreakdown[];
  pending_attribution_amount: string;
  pending_attribution_count: number;
}

export type OverlayPosition = "top-right" | "top-left" | "bottom-right" | "bottom-left";

// Mirrors server/schemas.py's ClientSafeSettings -- the payload of the public
// (unauthenticated) GET /client-settings. The customer client already reads
// shop_display_name from here; the dashboard header uses it too.
export interface ClientSafeSettings {
  shop_display_name: string;
  shop_subtitle: string | null;
  reminder_thresholds_seconds: number[];
  reminder_sounds_enabled: boolean;
  final_warning_sound_enabled: boolean;
  active_overlay_enabled: boolean;
  overlay_position: OverlayPosition;
  customer_branding_text: string;
}

export interface AdminSettings {
  general: {
    shop_display_name: string;
    shop_subtitle: string | null;
    timezone_display: string;
  };
  sessions: {
    expiration_grace_seconds: number;
    automatic_shutdown_on_expiration: boolean;
    reminder_thresholds_seconds: number[];
  };
  customer_client: {
    reminder_sounds_enabled: boolean;
    final_warning_sound_enabled: boolean;
    active_overlay_enabled: boolean;
    overlay_position: OverlayPosition;
    customer_branding_text: string;
  };
}

export interface AdminSettingsResponse {
  settings: AdminSettings;
  environment_only: Record<string, string>;
  read_only: Record<string, string>;
  restart_required_fields: string[];
  updated_at: string | null;
}

// Curated Rates & Pricing editor (Admin -> Settings). Backed by fixed
// rate_profiles / membership_products rows via GET/PUT /admin/pricing --
// not a generic rate CRUD.
export interface PricingMoneyTime {
  price: string;
  seconds: number;
}

export interface PricingVipTierView {
  rate_id: string;
  multiple: number;
  price: string;
  seconds: number;
}

export interface AdminPricingSnapshot {
  standard_guest: PricingMoneyTime;
  standard_member_new_account: PricingMoneyTime;
  standard_member_topup_small: PricingMoneyTime;
  standard_member_topup_large: PricingMoneyTime;
  vip_guest_hourly: PricingMoneyTime;
  promo: PricingMoneyTime;
  vip_guest_tiers: PricingVipTierView[];
  vip_member_conversion_ratio: number;
}

export interface AdminPricingUpdate {
  standard_guest: PricingMoneyTime;
  standard_member_new_account: PricingMoneyTime;
  standard_member_topup_small: PricingMoneyTime;
  standard_member_topup_large: PricingMoneyTime;
  vip_guest_hourly: PricingMoneyTime;
  promo: PricingMoneyTime;
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

export interface OperatorSaleSummary {
  has_open_shift: boolean;
  operator_user_id: string | null;
  operator_name: string | null;
  shift_id: string | null;
  operator_sale: string;
  cash_payments: string;
  gcash_payments: string;
  maya_payments: string;
  other_payments: string;
  voided_payments: string;
  payment_count: number;
}

export interface MemberRecord {
  member_id: string;
  member_code: string;
  username: string | null;
  display_name: string | null;
  full_name: string;
  phone_number: string | null;
  has_pin: boolean;
  requires_account_setup: boolean;
  status: "active" | "inactive";
  time_balance_seconds: number;
  vip_balance_seconds: number;
  joined_at: string;
  expires_at: string | null;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
  // Present only on the response from creating a member with a paid
  // membership fee (see MembersManager create flow).
  creation_payment?: PaymentRecord | null;
  creation_base_seconds?: number | null;
  creation_benefit_seconds?: number | null;
}

export interface MembershipProduct {
  product_id: string;
  name: string;
  price: string;
  time_seconds: number;
  product_type: "initial_membership" | "member_topup";
  balance_type: "standard" | "vip";
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
    | "purchase_refund"
    | "session_reservation_created"
    | "session_usage_finalized"
    | "session_reservation_released"
    | "session_reservation"
    | "session_refund"
    | "manual_adjustment"
    | "expiration_adjustment";
  // Historical rows recorded before Stage 0 have no balance_type metadata
  // at all in the API response's absence-sense -- but the backend always
  // returns 'standard' for them (their fixed, correct backfilled value,
  // never guessed client-side), so this is never optional here.
  balance_type: "standard" | "vip";
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

export interface MemberVipPlayablePreview {
  is_vip_station: boolean;
  playable_seconds: number;
  vip_seconds: number;
  standard_seconds: number;
  standard_real_seconds_equivalent: number;
}

export interface SessionBalanceSourceProjection {
  session_id: string;
  is_vip_station: boolean;
  balance_source: "standard" | "vip" | null;
  vip_reserved_seconds: number;
  standard_reserved_seconds: number;
  standard_reserved_vip_equivalent_seconds: number;
  vip_consumed_estimate_seconds: number;
  standard_consumed_estimate_seconds: number;
}

export interface MemberPurchaseResponse {
  member: MemberRecord;
  product: MembershipProduct;
  payment: PaymentRecord;
  transaction: MemberTimeTransaction;
  // Real play-seconds the top-up auto-applied to the member's live session
  // (0 / absent when they had none or it could not be applied).
  applied_to_active_session_seconds?: number;
}

export interface MemberPurchaseRefundResponse {
  member: MemberRecord;
  payment: PaymentRecord;
  reversal_transaction: MemberTimeTransaction;
}

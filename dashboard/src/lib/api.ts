import type {
  BillingSummary,
  CashierShiftDetail,
  CashierShiftRecord,
  AdminSettings,
  AdminSettingsResponse,
  CurrentUser,
  ForceEndSessionResponse,
  IncomeGroup,
  MemberRecord,
  MemberPurchaseRefundResponse,
  MemberPurchaseResponse,
  MembershipProduct,
  MemberSessionSelection,
  MemberTimeTransaction,
  MemberVipPlayablePreview,
  OperatorAccess,
  OperatorActivityRecord,
  OperatorCreateInput,
  OperatorManagementRecord,
  OperatorSaleSummary,
  OperatorUpdateInput,
  PcLayoutSlot,
  PcSummary,
  PaymentInput,
  PreparedSessionSelection,
  PrepaidSessionSelection,
  RemoteCommandRecord,
  RemoteCommandResponse,
  MessageSeverity,
  SalesPeriod,
  SalesReport,
  SystemBackupRecord,
  SystemClientHealth,
  SystemDatabaseHealth,
  SystemHealth,
  SystemInfo,
  SystemLogEntry,
  SystemStorage,
  UserAdminRecord,
  UserCreateInput,
  UserUpdateInput,
  RateProfile,
  RateProfileInput,
  SessionBalanceSourceProjection,
  SessionCommandResponse,
  SessionRecord,
  StationAssignment
} from "./types";

const DEVELOPMENT_BASE_URL = "http://localhost:8000";
const PRODUCTION_BASE_URL = "http://127.0.0.1:18001";

export function getApiBaseUrl(): string {
  const configuredUrl = process.env.NEXT_PUBLIC_FASTAPI_BASE_URL;
  if (configuredUrl) {
    return configuredUrl.replace(/\/$/, "");
  }
  return (process.env.NODE_ENV === "production" ? PRODUCTION_BASE_URL : DEVELOPMENT_BASE_URL).replace(/\/$/, "");
}

export function getPollingIntervalMs(): number {
  const rawValue = Number(process.env.NEXT_PUBLIC_POLL_INTERVAL_MS ?? "4000");
  return Number.isFinite(rawValue) && rawValue >= 3000 ? rawValue : 4000;
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") {
        message = payload.detail;
      } else if (Array.isArray(payload.detail)) {
        message = payload.detail
          .map((item) => {
            if (item && typeof item === "object" && "msg" in item) {
              return String((item as { msg: unknown }).msg);
            }
            return String(item);
          })
          .join(" ");
      }
    } catch {
      // Keep the generic status message when the server does not return JSON.
    }
    console.error("Secret Grounds API request failed", {
      status: response.status,
      statusText: response.statusText,
      url: response.url,
      message
    });
    throw new Error(toCashierMessage(message));
  }

  return response.json() as Promise<T>;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  try {
    return await parseResponse<T>(
      await fetch(url, {
        ...init,
        credentials: "include"
      })
    );
  } catch (error) {
    if (error instanceof TypeError || (error instanceof Error && error.message === "Failed to fetch")) {
      console.error("Secret Grounds API network failure", {
        url,
        method: init?.method ?? "GET",
        error
      });
      throw new Error(
        "Could not reach the Secret Grounds server. Check that FastAPI is running and CORS allows this dashboard."
      );
    }
    throw error;
  }
}

export async function login(username: string, password: string): Promise<CurrentUser> {
  return requestJson<CurrentUser>(`${getApiBaseUrl()}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  });
}

export async function fetchLoginOperators(): Promise<OperatorAccess[]> {
  return requestJson<OperatorAccess[]>(`${getApiBaseUrl()}/auth/operators`, {
    cache: "no-store"
  });
}

export async function quickOperatorLogin(operatorId: string): Promise<CurrentUser> {
  return requestJson<CurrentUser>(`${getApiBaseUrl()}/auth/operator/quick`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ operator_id: operatorId })
  });
}

export async function adminOperatorLogin(password: string): Promise<CurrentUser> {
  return requestJson<CurrentUser>(`${getApiBaseUrl()}/auth/operator/admin`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password })
  });
}

export async function passwordOperatorLogin(operatorId: string, password: string): Promise<CurrentUser> {
  return requestJson<CurrentUser>(`${getApiBaseUrl()}/auth/operator/password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ operator_id: operatorId, password })
  });
}

export async function logout(): Promise<void> {
  await requestJson<{ message: string }>(`${getApiBaseUrl()}/auth/logout`, {
    method: "POST"
  });
}

export async function fetchCurrentUser(): Promise<CurrentUser> {
  return requestJson<CurrentUser>(`${getApiBaseUrl()}/auth/me`, {
    cache: "no-store"
  });
}

export async function fetchAdminSettings(): Promise<AdminSettingsResponse> {
  return requestJson<AdminSettingsResponse>(`${getApiBaseUrl()}/settings`, {
    cache: "no-store"
  });
}

export async function updateAdminSettings(settings: AdminSettings): Promise<AdminSettingsResponse> {
  return requestJson<AdminSettingsResponse>(`${getApiBaseUrl()}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings)
  });
}

export async function resetAdminSettings(category: keyof AdminSettings | "all"): Promise<AdminSettingsResponse> {
  return requestJson<AdminSettingsResponse>(`${getApiBaseUrl()}/settings/reset`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ category })
  });
}

export async function fetchSystemHealth(): Promise<SystemHealth> {
  return requestJson<SystemHealth>(`${getApiBaseUrl()}/system/health`, { cache: "no-store" });
}

export async function fetchSystemDatabase(): Promise<SystemDatabaseHealth> {
  return requestJson<SystemDatabaseHealth>(`${getApiBaseUrl()}/system/database`, { cache: "no-store" });
}

export async function fetchSystemBackups(): Promise<SystemBackupRecord[]> {
  return requestJson<SystemBackupRecord[]>(`${getApiBaseUrl()}/system/backups`, { cache: "no-store" });
}

export async function createSystemBackup(): Promise<{
  message: string;
  backup: SystemBackupRecord;
  validation: { valid: boolean; status: string };
}> {
  return requestJson(`${getApiBaseUrl()}/system/backup`, { method: "POST" });
}

export async function restoreSystemBackup(filename: string): Promise<{ message: string }> {
  return requestJson<{ message: string }>(`${getApiBaseUrl()}/system/restore`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, confirm: true })
  });
}

export async function deleteSystemBackup(filename: string): Promise<{ message: string }> {
  return requestJson<{ message: string }>(
    `${getApiBaseUrl()}/system/backups/${encodeURIComponent(filename)}?confirm=true`,
    { method: "DELETE" }
  );
}

export function getSystemBackupDownloadUrl(filename: string): string {
  return `${getApiBaseUrl()}/system/backups/${encodeURIComponent(filename)}/download`;
}

export async function fetchSystemStorage(): Promise<SystemStorage> {
  return requestJson<SystemStorage>(`${getApiBaseUrl()}/system/storage`, { cache: "no-store" });
}

export async function fetchSystemInfo(): Promise<SystemInfo> {
  return requestJson<SystemInfo>(`${getApiBaseUrl()}/system/info`, { cache: "no-store" });
}

export async function fetchSystemLogs(): Promise<SystemLogEntry[]> {
  return requestJson<SystemLogEntry[]>(`${getApiBaseUrl()}/system/logs`, { cache: "no-store" });
}

export async function fetchSystemClients(): Promise<SystemClientHealth[]> {
  return requestJson<SystemClientHealth[]>(`${getApiBaseUrl()}/system/clients`, { cache: "no-store" });
}

export async function fetchCurrentShift(): Promise<CashierShiftDetail | null> {
  return requestJson<CashierShiftDetail | null>(`${getApiBaseUrl()}/shifts/current`, {
    cache: "no-store"
  });
}

export async function openShift(openingCash: string): Promise<CashierShiftDetail> {
  return requestJson<CashierShiftDetail>(`${getApiBaseUrl()}/shifts/open`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ opening_cash: openingCash })
  });
}

export async function closeShift(
  shiftId: string,
  input: { actualCash: string; closingNotes?: string; forceCloseReason?: string }
): Promise<CashierShiftDetail> {
  return requestJson<CashierShiftDetail>(`${getApiBaseUrl()}/shifts/${shiftId}/close`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      actual_cash: input.actualCash,
      closing_notes: input.closingNotes,
      force_close_reason: input.forceCloseReason
    })
  });
}

export async function fetchShifts(): Promise<CashierShiftRecord[]> {
  return requestJson<CashierShiftRecord[]>(`${getApiBaseUrl()}/shifts`, {
    cache: "no-store"
  });
}

export async function fetchShift(shiftId: string): Promise<CashierShiftDetail> {
  return requestJson<CashierShiftDetail>(`${getApiBaseUrl()}/shifts/${shiftId}`, {
    cache: "no-store"
  });
}

export async function fetchUsers(): Promise<UserAdminRecord[]> {
  return requestJson<UserAdminRecord[]>(`${getApiBaseUrl()}/users`, {
    cache: "no-store"
  });
}

export async function fetchAdminOperators(): Promise<OperatorManagementRecord[]> {
  return requestJson<OperatorManagementRecord[]>(`${getApiBaseUrl()}/admin/operators`, {
    cache: "no-store"
  });
}

export async function createAdminOperator(payload: OperatorCreateInput): Promise<OperatorManagementRecord> {
  return requestJson<OperatorManagementRecord>(`${getApiBaseUrl()}/admin/operators`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

export async function updateAdminOperator(
  operatorId: string,
  payload: OperatorUpdateInput
): Promise<OperatorManagementRecord> {
  return requestJson<OperatorManagementRecord>(`${getApiBaseUrl()}/admin/operators/${encodeURIComponent(operatorId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

export async function enableAdminOperator(operatorId: string): Promise<OperatorManagementRecord> {
  return requestJson<OperatorManagementRecord>(`${getApiBaseUrl()}/admin/operators/${encodeURIComponent(operatorId)}/enable`, {
    method: "POST"
  });
}

export async function disableAdminOperator(operatorId: string): Promise<OperatorManagementRecord> {
  return requestJson<OperatorManagementRecord>(`${getApiBaseUrl()}/admin/operators/${encodeURIComponent(operatorId)}/disable`, {
    method: "POST"
  });
}

export async function resetAdminOperatorPassword(operatorId: string, password: string): Promise<OperatorManagementRecord> {
  return requestJson<OperatorManagementRecord>(
    `${getApiBaseUrl()}/admin/operators/${encodeURIComponent(operatorId)}/reset-password`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password })
    }
  );
}

export async function signOutAdminOperator(operatorId: string): Promise<OperatorManagementRecord> {
  return requestJson<OperatorManagementRecord>(
    `${getApiBaseUrl()}/admin/operators/${encodeURIComponent(operatorId)}/sign-out`,
    { method: "POST" }
  );
}

export async function archiveAdminOperator(operatorId: string): Promise<OperatorManagementRecord> {
  return requestJson<OperatorManagementRecord>(`${getApiBaseUrl()}/admin/operators/${encodeURIComponent(operatorId)}`, {
    method: "DELETE"
  });
}

export async function fetchOperatorActivity(params: {
  dateScope?: "today" | "all";
  operatorId?: string;
  category?: string;
} = {}): Promise<OperatorActivityRecord[]> {
  const search = new URLSearchParams();
  search.set("date_scope", params.dateScope ?? "today");
  if (params.operatorId) {
    search.set("operator_id", params.operatorId);
  }
  if (params.category) {
    search.set("category", params.category);
  }
  return requestJson<OperatorActivityRecord[]>(
    `${getApiBaseUrl()}/operator-activity?${search.toString()}`,
    { cache: "no-store" }
  );
}

export async function fetchLiveActivity(limit = 60): Promise<OperatorActivityRecord[]> {
  return requestJson<OperatorActivityRecord[]>(
    `${getApiBaseUrl()}/activity?limit=${encodeURIComponent(String(limit))}`,
    { cache: "no-store" }
  );
}

export interface SalesReportFilters {
  period: SalesPeriod;
  startDate?: string;
  endDate?: string;
}

function appendSalesFilters(search: URLSearchParams, filters: SalesReportFilters) {
  search.set("period", filters.period);
  if (filters.period === "custom") {
    if (filters.startDate) {
      search.set("start_date", filters.startDate);
    }
    if (filters.endDate) {
      search.set("end_date", filters.endDate);
    }
  }
}

export async function fetchSalesReport(filters: SalesReportFilters): Promise<SalesReport> {
  const search = new URLSearchParams();
  appendSalesFilters(search, filters);
  return requestJson<SalesReport>(`${getApiBaseUrl()}/sales/report?${search.toString()}`, {
    cache: "no-store"
  });
}

export async function fetchDashboardTodaySales(): Promise<string> {
  return requestJson<string>(`${getApiBaseUrl()}/dashboard/today-sales`, {
    cache: "no-store"
  });
}

export async function fetchOperatorSale(): Promise<OperatorSaleSummary> {
  return requestJson<OperatorSaleSummary>(`${getApiBaseUrl()}/dashboard/operator-sale`, {
    cache: "no-store"
  });
}

export function getSalesExportUrl(filters: SalesReportFilters): string {
  const search = new URLSearchParams();
  appendSalesFilters(search, filters);
  return `${getApiBaseUrl()}/sales/export.csv?${search.toString()}`;
}

export async function fetchPcLayouts(includeDisabled = false): Promise<PcLayoutSlot[]> {
  const query = includeDisabled ? "?include_disabled=true" : "";
  return requestJson<PcLayoutSlot[]>(`${getApiBaseUrl()}/pcs/layout${query}`, {
    cache: "no-store"
  });
}

export async function updatePcLayout(
  pcName: string,
  input: {
    display_name: string;
    area_label: string;
    layout_order: number;
    is_vip: boolean;
    is_enabled: boolean;
    mac_address?: string | null;
    income_group_id?: string | null;
  }
): Promise<PcLayoutSlot> {
  return requestJson<PcLayoutSlot>(`${getApiBaseUrl()}/pcs/${pcName}/layout`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
}

// Station Assignment: physical mac_address -> pc_name identity, kept
// deliberately separate from PcLayoutSlot (station type/income owner/
// display configuration) above. Reuses the existing Stage 2 station
// endpoints/repository -- no second assignment system.
export async function fetchUnclaimedStations(): Promise<StationAssignment[]> {
  return requestJson<StationAssignment[]>(`${getApiBaseUrl()}/stations/unclaimed`, {
    cache: "no-store"
  });
}

export async function fetchAssignedStations(): Promise<StationAssignment[]> {
  return requestJson<StationAssignment[]>(`${getApiBaseUrl()}/stations/assigned`, {
    cache: "no-store"
  });
}

export async function assignStation(macAddress: string, pcName: string): Promise<StationAssignment> {
  return requestJson<StationAssignment>(`${getApiBaseUrl()}/stations/${encodeURIComponent(macAddress)}/assign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pc_name: pcName })
  });
}

export async function reassignStation(macAddress: string, pcName: string): Promise<StationAssignment> {
  return requestJson<StationAssignment>(`${getApiBaseUrl()}/stations/${encodeURIComponent(macAddress)}/reassign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pc_name: pcName })
  });
}

export async function revokeStation(macAddress: string): Promise<StationAssignment> {
  return requestJson<StationAssignment>(`${getApiBaseUrl()}/stations/${encodeURIComponent(macAddress)}/revoke`, {
    method: "POST"
  });
}

export async function fetchIncomeGroups(): Promise<IncomeGroup[]> {
  return requestJson<IncomeGroup[]>(`${getApiBaseUrl()}/income-groups`, {
    cache: "no-store"
  });
}

export async function createUser(input: UserCreateInput): Promise<UserAdminRecord> {
  return requestJson<UserAdminRecord>(`${getApiBaseUrl()}/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
}

export async function updateUser(
  userId: string,
  input: UserUpdateInput
): Promise<UserAdminRecord> {
  return requestJson<UserAdminRecord>(`${getApiBaseUrl()}/users/${userId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
}

export async function updateUserStatus(
  userId: string,
  isActive: boolean
): Promise<UserAdminRecord> {
  return requestJson<UserAdminRecord>(`${getApiBaseUrl()}/users/${userId}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_active: isActive })
  });
}

export async function resetUserPassword(
  userId: string,
  newPassword: string
): Promise<UserAdminRecord> {
  return requestJson<UserAdminRecord>(`${getApiBaseUrl()}/users/${userId}/reset-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: newPassword })
  });
}

export function toCashierMessage(message: string): string {
  const lowerMessage = message.toLowerCase();

  if (lowerMessage.includes("duplicate transaction key") || lowerMessage.includes("already been recorded")) {
    return "This transaction appears to have already been recorded.";
  }
  if (lowerMessage.includes("cash received is required")) {
    return "Enter the cash received.";
  }
  if (lowerMessage.includes("cash received") && lowerMessage.includes("lower")) {
    return "Cash received must be at least the payment amount.";
  }
  if (lowerMessage.includes("cash received") && lowerMessage.includes("only valid for cash")) {
    return "Cash received is only valid when the payment method is Cash.";
  }
  if (lowerMessage.includes("reference") && lowerMessage.includes("required")) {
    return "Enter the payment reference.";
  }
  if (lowerMessage.includes("member is inactive")) {
    return "This member account is disabled.";
  }
  if (lowerMessage.includes("membership product not found")) {
    return "The selected member package is no longer available.";
  }
  if (lowerMessage.includes("not authenticated") || lowerMessage.includes("unauthorized") || lowerMessage.includes("forbidden")) {
    return "You are not authorized to record this transaction.";
  }
  if (lowerMessage.includes("offline")) {
    return "This PC is offline. Session actions are disabled until it reconnects.";
  }
  if (lowerMessage.includes("active sessions require force")) {
    return "This PC has an active session. Owner/admin confirmation and a reason are required before power control.";
  }
  if (lowerMessage.includes("mac address")) {
    return "Save a valid MAC address before using Wake-on-LAN.";
  }
  if (lowerMessage.includes("power controls")) {
    return "You do not have permission to use PC power controls.";
  }
  if (lowerMessage.includes("already has an active session")) {
    return "This PC already has an active session.";
  }
  if (lowerMessage.includes("positive")) {
    return "Enter a valid positive duration.";
  }
  if (lowerMessage.includes("command") && lowerMessage.includes("failed")) {
    return "The client did not complete the command. Check the PC connection and try again.";
  }
  if (lowerMessage.includes("websocket")) {
    return "The PC is online but not ready for live commands yet.";
  }

  return message || "Something went wrong. Please try again.";
}

export async function fetchPcs(): Promise<PcSummary[]> {
  return requestJson<PcSummary[]>(`${getApiBaseUrl()}/pcs`, { cache: "no-store" });
}

export async function updatePcMacAddress(
  pcName: string,
  macAddress: string | null
): Promise<{ mac_address: string | null }> {
  return requestJson<{ mac_address: string | null }>(`${getApiBaseUrl()}/pcs/${encodeURIComponent(pcName)}/mac`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mac_address: macAddress })
  });
}

export async function sendPcMessage(
  pcName: string,
  input: {
    title: string;
    message: string;
    severity: MessageSeverity;
    duration_seconds?: number;
    require_acknowledgement: boolean;
  }
): Promise<RemoteCommandResponse> {
  return requestJson<RemoteCommandResponse>(`${getApiBaseUrl()}/pcs/${encodeURIComponent(pcName)}/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
}

export async function sendPowerCommand(
  pcName: string,
  command: "shutdown" | "restart" | "sleep",
  input: { force?: boolean; reason?: string; confirm?: boolean }
): Promise<RemoteCommandResponse> {
  return requestJson<RemoteCommandResponse>(
    `${getApiBaseUrl()}/pcs/${encodeURIComponent(pcName)}/power/${command}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    }
  );
}

export async function wakePc(pcName: string): Promise<{ message: string; command: RemoteCommandRecord }> {
  return requestJson<{ message: string; command: RemoteCommandRecord }>(
    `${getApiBaseUrl()}/pcs/${encodeURIComponent(pcName)}/wake`,
    { method: "POST" }
  );
}

export async function fetchOperators(): Promise<CurrentUser[]> {
  return requestJson<CurrentUser[]>(`${getApiBaseUrl()}/operators`, {
    cache: "no-store"
  });
}

export async function fetchCurrentOperator(): Promise<{ operator: CurrentUser | null }> {
  return requestJson<{ operator: CurrentUser | null }>(`${getApiBaseUrl()}/operators/current`, {
    cache: "no-store"
  });
}

export async function setCurrentOperator(operatorId: string): Promise<{ operator: CurrentUser | null }> {
  return requestJson<{ operator: CurrentUser | null }>(`${getApiBaseUrl()}/operators/current`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ operator_id: operatorId })
  });
}

export async function broadcastAnnouncement(input: {
  title: string;
  message: string;
  severity: MessageSeverity;
  duration_seconds?: number;
  require_acknowledgement: boolean;
  pc_names?: string[];
}): Promise<{
  commands: RemoteCommandRecord[];
  offline_targets: string[];
  failed_targets: Record<string, string>;
}> {
  return requestJson<{
    commands: RemoteCommandRecord[];
    offline_targets: string[];
    failed_targets: Record<string, string>;
  }>(`${getApiBaseUrl()}/commands/broadcast`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
}

export async function fetchRemoteCommands(): Promise<RemoteCommandRecord[]> {
  return requestJson<RemoteCommandRecord[]>(`${getApiBaseUrl()}/remote-commands`, {
    cache: "no-store"
  });
}

export async function fetchMembers(query = ""): Promise<MemberRecord[]> {
  const search = query.trim() ? `?q=${encodeURIComponent(query.trim())}` : "";
  return requestJson<MemberRecord[]>(`${getApiBaseUrl()}/members${search}`, {
    cache: "no-store"
  });
}

export async function createMember(input: {
  username: string;
  display_name?: string;
  pin: string;
  confirm_pin: string;
  phone_number?: string;
}): Promise<MemberRecord> {
  return requestJson<MemberRecord>(`${getApiBaseUrl()}/members`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
}

export async function updateMember(
  memberId: string,
  input: {
    username: string;
    display_name?: string;
    phone_number?: string;
  }
): Promise<MemberRecord> {
  return requestJson<MemberRecord>(`${getApiBaseUrl()}/members/${memberId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
}

export async function updateMemberStatus(
  memberId: string,
  status: "active" | "inactive"
): Promise<MemberRecord> {
  return requestJson<MemberRecord>(`${getApiBaseUrl()}/members/${memberId}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status })
  });
}

export async function resetMemberPin(
  memberId: string,
  pin: string,
  confirmPin: string
): Promise<MemberRecord> {
  return requestJson<MemberRecord>(`${getApiBaseUrl()}/members/${memberId}/pin`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pin, confirm_pin: confirmPin })
  });
}

export async function fetchMemberLedger(memberId: string): Promise<MemberTimeTransaction[]> {
  return requestJson<MemberTimeTransaction[]>(`${getApiBaseUrl()}/members/${memberId}/ledger`, {
    cache: "no-store"
  });
}

export async function fetchMemberVipPreview(
  memberId: string,
  pcName: string,
  durationSeconds?: number
): Promise<MemberVipPlayablePreview> {
  const query = new URLSearchParams({ pc_name: pcName });
  if (durationSeconds !== undefined) {
    query.set("duration_seconds", String(durationSeconds));
  }
  return requestJson<MemberVipPlayablePreview>(
    `${getApiBaseUrl()}/members/${memberId}/vip-preview?${query.toString()}`,
    { cache: "no-store" }
  );
}

export async function fetchSessionBalanceSource(sessionId: string): Promise<SessionBalanceSourceProjection> {
  return requestJson<SessionBalanceSourceProjection>(
    `${getApiBaseUrl()}/sessions/${sessionId}/balance-source`,
    { cache: "no-store" }
  );
}

export async function fetchMembershipProducts(): Promise<MembershipProduct[]> {
  return requestJson<MembershipProduct[]>(`${getApiBaseUrl()}/membership-products`, {
    cache: "no-store"
  });
}

export async function purchaseMemberTime(
  memberId: string,
  input: {
    product_id: string;
    payment_method: PaymentInput["payment_method"];
    cash_received?: string;
    reference_number?: string;
    notes?: string;
    idempotency_key?: string;
  }
): Promise<MemberPurchaseResponse> {
  return requestJson<MemberPurchaseResponse>(`${getApiBaseUrl()}/members/${memberId}/purchase`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
}

export async function refundMemberPurchase(
  paymentId: string,
  input: { reason: string; notes?: string }
): Promise<MemberPurchaseRefundResponse> {
  return requestJson<MemberPurchaseRefundResponse>(`${getApiBaseUrl()}/member-purchases/${paymentId}/refund`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
}

export async function fetchHistory(): Promise<SessionRecord[]> {
  return requestJson<SessionRecord[]>(`${getApiBaseUrl()}/sessions/history`, {
    cache: "no-store"
  });
}

export async function fetchBilling(sessionId: string): Promise<BillingSummary> {
  return requestJson<BillingSummary>(`${getApiBaseUrl()}/sessions/${sessionId}/billing`, {
    cache: "no-store"
  });
}

export async function recordPayment(
  sessionId: string,
  input: PaymentInput
): Promise<BillingSummary> {
  return requestJson<BillingSummary>(`${getApiBaseUrl()}/sessions/${sessionId}/payments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
}

export async function voidPayment(
  paymentId: string,
  reason: string
): Promise<BillingSummary> {
  return requestJson<BillingSummary>(`${getApiBaseUrl()}/payments/${paymentId}/void`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason })
  });
}

export async function fetchRates(includeInactive = false): Promise<RateProfile[]> {
  const query = includeInactive ? "?include_inactive=true" : "";
  return requestJson<RateProfile[]>(`${getApiBaseUrl()}/rates${query}`, {
    cache: "no-store"
  });
}

export async function startSession(
  pcName: string,
  payload: { amount?: string; durationSeconds?: number; rateId?: string }
): Promise<SessionCommandResponse> {
  return requestJson<SessionCommandResponse>(
    `${getApiBaseUrl()}/sessions/start`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pc_name: pcName,
        amount: payload.amount,
        duration_seconds: payload.durationSeconds,
        rate_id: payload.rateId
      })
    }
  );
}

export async function startPrepaidSession(
  pcName: string,
  payload: PrepaidSessionSelection
): Promise<SessionCommandResponse> {
  return requestJson<SessionCommandResponse>(`${getApiBaseUrl()}/sessions/start-prepaid`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      pc_name: pcName,
      amount: payload.amount,
      duration_seconds: payload.durationSeconds,
      rate_id: payload.rateId,
      payment: payload.payment
    })
  });
}

export async function prepareStartupSession(
  pcName: string,
  payload: PreparedSessionSelection | MemberSessionSelection
): Promise<{ prepared_session: unknown; message: string }> {
  if ("sessionMode" in payload && payload.sessionMode === "member_balance") {
    return requestJson<{ prepared_session: unknown; message: string }>(
      `${getApiBaseUrl()}/sessions/prepare-startup`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pc_name: pcName,
          session_mode: "member_balance",
          member_username: payload.memberUsername,
          duration_seconds: payload.durationSeconds,
          member_pin: payload.memberPin
        })
      }
    );
  }

  return requestJson<{ prepared_session: unknown; message: string }>(
    `${getApiBaseUrl()}/sessions/prepare-startup`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pc_name: pcName,
        session_mode: "prepaid",
        amount: payload.amount,
        duration_seconds: payload.durationSeconds,
        rate_id: payload.rateId,
        payment: payload.payment,
        operator_id: payload.operatorId
      })
    }
  );
}

export async function retryPreparedWake(preparedSessionId: string): Promise<{ message: string }> {
  return requestJson<{ message: string }>(
    `${getApiBaseUrl()}/prepared-sessions/${preparedSessionId}/retry-wake`,
    { method: "POST" }
  );
}

export async function cancelPreparedSession(preparedSessionId: string): Promise<{ message: string }> {
  return requestJson<{ message: string }>(
    `${getApiBaseUrl()}/prepared-sessions/${preparedSessionId}/cancel`,
    { method: "POST" }
  );
}

export async function startMemberSession(
  pcName: string,
  payload: MemberSessionSelection
): Promise<SessionCommandResponse> {
  return requestJson<SessionCommandResponse>(`${getApiBaseUrl()}/sessions/start-member`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      pc_name: pcName,
      member_username: payload.memberUsername,
      duration_seconds: payload.durationSeconds,
      member_pin: payload.memberPin
    })
  });
}

export async function addTime(
  sessionId: string,
  payload: { amount?: string; seconds?: number; rateId?: string }
): Promise<SessionCommandResponse> {
  return requestJson<SessionCommandResponse>(
    `${getApiBaseUrl()}/sessions/${sessionId}/add-time`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        amount: payload.amount,
        seconds: payload.seconds,
        rate_id: payload.rateId
      })
    }
  );
}

export async function addPrepaidTime(
  sessionId: string,
  payload: PrepaidSessionSelection
): Promise<SessionCommandResponse> {
  return requestJson<SessionCommandResponse>(
    `${getApiBaseUrl()}/sessions/${sessionId}/add-time-prepaid`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        amount: payload.amount,
        seconds: payload.durationSeconds,
        rate_id: payload.rateId,
        payment: payload.payment
      })
    }
  );
}

export async function addMemberTime(
  sessionId: string,
  payload: MemberSessionSelection
): Promise<SessionCommandResponse> {
  return requestJson<SessionCommandResponse>(
    `${getApiBaseUrl()}/sessions/${sessionId}/add-time-member`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        member_id: payload.memberId,
        seconds: payload.durationSeconds
      })
    }
  );
}

export async function endSession(sessionId: string): Promise<SessionCommandResponse> {
  return requestJson<SessionCommandResponse>(
    `${getApiBaseUrl()}/sessions/${sessionId}/end`,
    {
      method: "POST"
    }
  );
}

export async function clearCustomerLock(sessionId: string): Promise<SessionCommandResponse> {
  return requestJson<SessionCommandResponse>(
    `${getApiBaseUrl()}/sessions/${sessionId}/clear-customer-lock`,
    {
      method: "POST"
    }
  );
}

export async function forceEndSession(sessionId: string): Promise<ForceEndSessionResponse> {
  return requestJson<ForceEndSessionResponse>(
    `${getApiBaseUrl()}/sessions/${sessionId}/force-end`,
    {
      method: "POST"
    }
  );
}

export async function createRate(input: RateProfileInput): Promise<RateProfile> {
  return requestJson<RateProfile>(
    `${getApiBaseUrl()}/rates`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    }
  );
}

export async function updateRate(
  rateId: string,
  input: RateProfileInput
): Promise<RateProfile> {
  return requestJson<RateProfile>(
    `${getApiBaseUrl()}/rates/${rateId}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    }
  );
}

export async function updateRateStatus(
  rateId: string,
  isActive: boolean
): Promise<RateProfile> {
  return requestJson<RateProfile>(
    `${getApiBaseUrl()}/rates/${rateId}/status`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_active: isActive })
    }
  );
}

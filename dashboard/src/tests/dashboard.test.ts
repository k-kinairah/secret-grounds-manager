import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  addTime,
  adminOperatorLogin,
  assignStation,
  createUser,
  createRate,
  fetchAdminSettings,
  fetchAssignedStations,
  fetchCurrentUser,
  fetchDashboardTodaySales,
  fetchLoginOperators,
  fetchLiveActivity,
  fetchPcLayouts,
  fetchRates,
  fetchSalesReport,
  fetchSystemBackups,
  fetchSystemClients,
  fetchSystemDatabase,
  fetchSystemHealth,
  fetchSystemInfo,
  fetchSystemLogs,
  fetchSystemStorage,
  fetchUnclaimedStations,
  fetchUsers,
  forceEndSession,
  getApiBaseUrl,
  getSalesExportUrl,
  getSystemBackupDownloadUrl,
  login,
  logout,
  quickOperatorLogin,
  reassignStation,
  recordPayment,
  resetMemberPin,
  resetAdminSettings,
  resetUserPassword,
  revokeStation,
  createSystemBackup,
  deleteSystemBackup,
  restoreSystemBackup,
  sendPcMessage,
  sendPowerCommand,
  startMemberSession,
  startSession,
  toCashierMessage,
  updateAdminSettings,
  updateRate,
  updatePcLayout,
  updateRateStatus,
  updateUser,
  updateUserStatus
} from "../lib/api";
import {
  activityKey,
  nextUnreadCount,
  shouldToastForActivity
} from "../lib/activity";
import { calculateCashChange, isPaymentInputValid } from "../lib/paymentSelection";
import {
  getPcActionAvailability,
  getPcCardTone,
  getPcMonitorPresentation,
  summarizePcs
} from "../lib/pcStatus";
import { getPackageRatesForPc, isRateEligibleNow } from "../lib/packageEligibility";
import { saveRateProfile } from "../lib/rateActions";
import { rateFormToInput, rateToForm } from "../lib/rateForm";
import {
  calculateOpenTimeEstimate,
  formatDuration,
  formatMoney,
  formatNaturalDuration,
  formatRateDuration,
  formatTimer,
  minutesToSeconds,
  secondsFromStartedAt
} from "../lib/time";
import type { OperatorActivityRecord, PcSummary, RateProfile, RateProfileInput } from "../lib/types";

function makePc(overrides: Partial<PcSummary> = {}): PcSummary {
  return {
    pc_name: "PC-01",
    display_name: "PC 1",
    area_label: "SECRET GROUND SHOP",
    layout_order: 10,
    is_vip: false,
    is_enabled: true,
    ip_address: "127.0.0.1",
    connection_status: "online",
    has_command_connection: true,
    power_state: "online",
    last_seen: "2026-07-23T00:00:00+00:00",
    session_status: "idle",
    startup_status: null,
    prepared_session_id: null,
    startup_elapsed_seconds: null,
    startup_expires_at: null,
    startup_failure_reason: null,
    active_session_id: null,
    started_at: null,
    scheduled_end_at: null,
    grace_started_at: null,
    shutdown_at: null,
    remaining_seconds: null,
    rate_name_snapshot: null,
    base_amount: "0.00",
    additional_amount: "0.00",
    total_amount: "0.00",
    total_paid: "0.00",
    balance_due: "0.00",
    payment_status: "unpaid",
    session_mode: "prepaid",
    actual_elapsed_seconds: null,
    billed_seconds: null,
    final_computed_amount: null,
    metered_rate_price_snapshot: null,
    metered_rate_duration_seconds_snapshot: null,
    estimated_total_amount: null,
    estimated_balance_due: null,
    settlement_pending: false,
    recovery_required: false,
    recovery_reason: null,
    member_id: null,
    member_code_snapshot: null,
    member_name_snapshot: null,
    member_balance_before_seconds: null,
    member_balance_after_seconds: null,
    mac_address: null,
    customer_locked: false,
    ...overrides
  };
}

function makeActivity(overrides: Partial<OperatorActivityRecord> = {}): OperatorActivityRecord {
  return {
    timestamp: "2026-07-28T05:00:00+00:00",
    operator_id: "operator_admin",
    operator_name: "Admin",
    action: "Collected payment",
    category: "payment",
    pc_name: "PC-01",
    customer: null,
    amount: "20.00",
    payment_method: "cash",
    result: "Completed",
    ...overrides
  };
}

assert.equal(minutesToSeconds(30), 1800);
assert.equal(minutesToSeconds(0.5), 30);
assert.throws(() => minutesToSeconds(0));
assert.equal(formatDuration(90), "1m 30s");
assert.equal(formatDuration(null), "Unknown");
assert.equal(formatTimer(1985), "33:05");
assert.equal(formatTimer(6138), "1:42:18");
assert.equal(formatRateDuration(1800), "30 min");
assert.equal(formatNaturalDuration(45), "45 sec");
assert.equal(formatNaturalDuration(720), "12 min");
assert.equal(formatNaturalDuration(3600), "1 hr");
assert.equal(formatNaturalDuration(16620), "4 hr 37 min");
assert.equal(formatMoney("12.30"), "\u20b112.30");
assert.equal(formatMoney("0.00"), "\u20b10.00");

assert.equal(
  secondsFromStartedAt(
    "2026-07-23T00:00:00.000Z",
    Date.parse("2026-07-23T00:00:01.000Z")
  ),
  1
);
assert.equal(
  secondsFromStartedAt(
    "2026-07-23T00:00:00.000Z",
    Date.parse("2026-07-23T00:00:05.000Z")
  ),
  5
);
assert.deepEqual(
  calculateOpenTimeEstimate(
    "2026-07-23T00:00:00.000Z",
    Date.parse("2026-07-23T00:00:01.000Z"),
    300,
    "1.00",
    "0.00"
  ),
  {
    actualElapsedSeconds: 1,
    billedSeconds: 300,
    estimatedTotalAmount: "1.00",
    estimatedBalanceDue: "1.00"
  }
);
assert.deepEqual(
  calculateOpenTimeEstimate(
    "2026-07-23T00:00:00.000Z",
    Date.parse("2026-07-23T00:05:01.000Z"),
    300,
    "1.00",
    "1.00"
  ),
  {
    actualElapsedSeconds: 301,
    billedSeconds: 600,
    estimatedTotalAmount: "2.00",
    estimatedBalanceDue: "1.00"
  }
);

const offlinePc = makePc({ connection_status: "offline", power_state: "powered_off" });
assert.equal(getPcCardTone(offlinePc), "offline");
assert.deepEqual(getPcActionAvailability(offlinePc), {
  canStart: false,
  canAddTime: false,
  canEnd: false,
  canCollectPayment: false,
  canViewDetails: true,
  canClearCustomerLock: false
});

const wakeReadyPc = makePc({
  connection_status: "offline",
  power_state: "powered_off",
  mac_address: "AA:BB:CC:DD:EE:FF"
});
assert.equal(getPcActionAvailability(wakeReadyPc).canStart, true);

const wakingPc = makePc({
  connection_status: "offline",
  power_state: "waking",
  startup_status: "waiting_for_client",
  prepared_session_id: "prepared-1",
  mac_address: "AA:BB:CC:DD:EE:FF"
});
assert.equal(getPcActionAvailability(wakingPc).canStart, false);
assert.deepEqual(getPcMonitorPresentation(wakingPc), {
  className: "monitorTileWaking",
  label: "Waking"
});

const activePc = makePc({
  session_status: "active",
  active_session_id: "session-1",
  remaining_seconds: 600
});
assert.equal(getPcCardTone(activePc), "active");
assert.equal(getPcActionAvailability(activePc).canAddTime, true);
assert.equal(getPcActionAvailability(activePc).canEnd, true);
assert.deepEqual(getPcMonitorPresentation(activePc), {
  className: "monitorTileActive",
  label: "In Use"
});

const lowTimePc = makePc({
  session_status: "active",
  active_session_id: "session-low",
  remaining_seconds: 60
});
assert.deepEqual(getPcMonitorPresentation(lowTimePc), {
  className: "monitorTileLowTime",
  label: "Low Time"
});

const customerLockedPc = makePc({
  session_status: "active",
  session_mode: "member_balance",
  active_session_id: "session-locked",
  remaining_seconds: 600,
  customer_locked: true
});
// A temporary member lock is a badge override, not a new session_status --
// the station must still count as "In Use" everywhere else (KPI summary),
// same precedent as recovery_required/startup_status.
assert.equal(getPcCardTone(customerLockedPc), "active");
assert.deepEqual(getPcMonitorPresentation(customerLockedPc), {
  className: "monitorTileLocked",
  label: "Customer Locked"
});
assert.equal(getPcActionAvailability(customerLockedPc).canClearCustomerLock, true);
assert.equal(
  summarizePcs([customerLockedPc]).inUse,
  1,
  "a customer-locked PC must still count as in-use, never available"
);

const unlockedMemberPc = makePc({
  session_status: "active",
  session_mode: "member_balance",
  active_session_id: "session-unlocked",
  remaining_seconds: 600,
  customer_locked: false
});
assert.equal(
  getPcActionAvailability(unlockedMemberPc).canClearCustomerLock,
  false,
  "Clear Customer Lock must not be available when the PC isn't locked"
);

const settlementPc = makePc({
  session_status: "settlement_pending",
  active_session_id: "session-settle",
  session_mode: "open_time",
  total_amount: "20.00",
  balance_due: "20.00",
  settlement_pending: true
});
assert.equal(getPcCardTone(settlementPc), "warning");
assert.equal(getPcActionAvailability(settlementPc).canStart, false);
assert.equal(getPcActionAvailability(settlementPc).canEnd, false);
assert.equal(getPcActionAvailability(settlementPc).canCollectPayment, true);
assert.deepEqual(getPcMonitorPresentation(settlementPc), {
  className: "monitorTileSettlement",
  label: "Settlement"
});

const gracePc = makePc({
  session_status: "grace_period",
  active_session_id: "session-grace",
  remaining_seconds: 90
});
assert.equal(getPcActionAvailability(gracePc).canAddTime, true);
assert.deepEqual(getPcMonitorPresentation(gracePc), {
  className: "monitorTileGrace",
  label: "Grace Period"
});

const shuttingDownPc = makePc({
  session_status: "shutting_down",
  power_state: "shutting_down",
  active_session_id: "session-shutdown"
});
assert.deepEqual(getPcMonitorPresentation(shuttingDownPc), {
  className: "monitorTileDanger",
  label: "Shutting Down"
});

const recoveryPc = makePc({
  session_status: "unknown",
  recovery_required: true,
  recovery_reason: "server-only active session"
});
assert.deepEqual(getPcMonitorPresentation(recoveryPc), {
  className: "monitorTileDanger",
  label: "Recovery"
});

const availablePc = makePc();
assert.equal(getPcCardTone(availablePc), "available");
assert.equal(getPcActionAvailability(availablePc).canStart, true);
assert.deepEqual(getPcMonitorPresentation(availablePc), {
  className: "monitorTileAvailable",
  label: "Available"
});
assert.deepEqual(getPcMonitorPresentation(offlinePc), {
  className: "monitorTileOffline",
  label: "Powered Off"
});

assert.deepEqual(summarizePcs([availablePc, activePc, gracePc, shuttingDownPc, settlementPc, offlinePc]), {
  total: 6,
  inUse: 3,
  available: 1,
  paymentDue: 1,
  settlementDueAmount: "20.00"
});

assert.equal(
  toCashierMessage("PC-01 is offline"),
  "This PC is offline. Session actions are disabled until it reconnects."
);
assert.equal(
  toCashierMessage("Start session command failed: timeout"),
  "The client did not complete the command. Check the PC connection and try again."
);

const initialActivity = makeActivity();
const laterActivity = makeActivity({
  action: "Started session",
  amount: null,
  category: "session",
  timestamp: "2026-07-28T05:01:00+00:00"
});
assert.equal(
  nextUnreadCount({
    hasLoaded: false,
    isOpen: false,
    previousKeys: new Set(),
    nextActivity: [initialActivity],
    unreadCount: 0
  }),
  0
);
assert.equal(
  nextUnreadCount({
    hasLoaded: true,
    isOpen: false,
    previousKeys: new Set([activityKey(initialActivity)]),
    nextActivity: [laterActivity, initialActivity],
    unreadCount: 1
  }),
  2
);
assert.equal(
  nextUnreadCount({
    hasLoaded: true,
    isOpen: true,
    previousKeys: new Set([activityKey(initialActivity)]),
    nextActivity: [laterActivity, initialActivity],
    unreadCount: 3
  }),
  0
);
assert.equal(shouldToastForActivity(initialActivity), true);
assert.equal(shouldToastForActivity(makeActivity({ action: "Operator changed", amount: null, category: "operator" })), false);

const renderedRate = {
  name: "1 Hour",
  duration: formatRateDuration(3600),
  price: formatMoney("100.10")
};
assert.deepEqual(renderedRate, {
  name: "1 Hour",
  duration: "1 hr",
  price: "\u20b1100.10"
});

const sampleRate: RateProfile = {
  rate_id: "rate-30",
  name: "30 Minutes",
  duration_seconds: 1800,
  price: "50.00",
  rate_type: "package",
  is_active: true,
  sort_order: 10,
  station_scope: "standard",
  guest_only: false,
  member_eligible: true,
  eligible_start_local_time: null,
  eligible_end_local_time: null,
  created_at: "2026-07-23T00:00:00+00:00",
  updated_at: "2026-07-23T00:00:00+00:00"
};

assert.deepEqual(rateToForm(sampleRate), {
  name: "30 Minutes",
  minutes: "30",
  price: "50.00",
  rateType: "package",
  sortOrder: "10",
  stationScope: "standard"
});
assert.deepEqual(
  rateFormToInput({
    name: "  Night Pack ",
    minutes: "90",
    price: "75.25",
    rateType: "package",
    sortOrder: "12",
    stationScope: "vip"
  }),
  {
    name: "Night Pack",
    duration_seconds: 5400,
    price: "75.25",
    rate_type: "package",
    sort_order: 12,
    station_scope: "vip"
  }
);

const standardGuestRate: RateProfile = {
  ...sampleRate,
  rate_id: "rate-standard-guest-5",
  name: "Standard Guest",
  station_scope: "standard"
};
const vipGuestRate: RateProfile = {
  ...sampleRate,
  rate_id: "rate-vip-guest",
  name: "VIP Guest",
  station_scope: "vip"
};
const midnightRate: RateProfile = {
  ...sampleRate,
  rate_id: "rate-midnight",
  name: "Standard Midnight Promo",
  station_scope: "standard",
  guest_only: true,
  member_eligible: false,
  eligible_start_local_time: "00:00",
  eligible_end_local_time: "05:00"
};
const anyStationRate: RateProfile = {
  ...sampleRate,
  rate_id: "rate-any",
  name: "Any Station Pass",
  station_scope: "any"
};

const allTestRates = [standardGuestRate, vipGuestRate, midnightRate, anyStationRate];
assert.deepEqual(
  getPackageRatesForPc(allTestRates, { is_vip: false }, new Date("2026-01-01T02:00:00")).map((rate) => rate.rate_id),
  ["rate-standard-guest-5", "rate-midnight", "rate-any"],
  "Standard PC should only see Standard-scoped and Any-scoped packages, midnight included inside its window"
);
assert.deepEqual(
  getPackageRatesForPc(allTestRates, { is_vip: false }, new Date("2026-01-01T14:00:00")).map((rate) => rate.rate_id),
  ["rate-standard-guest-5", "rate-any"],
  "Midnight promo must disappear outside its eligible window"
);
assert.deepEqual(
  getPackageRatesForPc(allTestRates, { is_vip: true }, new Date("2026-01-01T02:00:00")).map((rate) => rate.rate_id),
  ["rate-vip-guest", "rate-any"],
  "VIP PC must never see Standard-only or midnight packages"
);
assert.equal(isRateEligibleNow(midnightRate, new Date("2026-01-01T00:00:00")), true, "00:00 is inclusive");
assert.equal(isRateEligibleNow(midnightRate, new Date("2026-01-01T04:59:00")), true);
assert.equal(isRateEligibleNow(midnightRate, new Date("2026-01-01T05:00:00")), false, "05:00 is exclusive");
assert.equal(isRateEligibleNow(midnightRate, new Date("2026-01-01T23:59:00")), false);
assert.equal(isRateEligibleNow(standardGuestRate, new Date("2026-01-01T14:00:00")), true, "no window means always eligible");

assert.equal(calculateCashChange("20.00", "50.00"), 30);
assert.equal(isPaymentInputValid("cash", "20.00", "20.00", "50.00"), true);
assert.equal(isPaymentInputValid("cash", "20.00", "20.00", "10.00"), false);
assert.equal(isPaymentInputValid("gcash", "25.00", "20.00", ""), false);
assert.equal(isPaymentInputValid("gcash", "5.00", "20.00", ""), true);

const originalBaseUrl = process.env.NEXT_PUBLIC_FASTAPI_BASE_URL;
const originalNodeEnv = process.env.NODE_ENV;
const originalFetch = globalThis.fetch;
const originalConsoleError = console.error;
process.env.NEXT_PUBLIC_FASTAPI_BASE_URL = "http://localhost:8000/";
assert.equal(getApiBaseUrl(), "http://localhost:8000");
delete process.env.NEXT_PUBLIC_FASTAPI_BASE_URL;
Object.defineProperty(process.env, "NODE_ENV", { configurable: true, enumerable: true, writable: true, value: "development" });
assert.equal(getApiBaseUrl(), "http://localhost:8000");
Object.defineProperty(process.env, "NODE_ENV", { configurable: true, enumerable: true, writable: true, value: "production" });
assert.equal(getApiBaseUrl(), "http://127.0.0.1:18001");
process.env.NEXT_PUBLIC_FASTAPI_BASE_URL = "http://localhost:8000/";
Object.defineProperty(process.env, "NODE_ENV", { configurable: true, enumerable: true, writable: true, value: originalNodeEnv });

async function runAsyncAssertions() {
  try {
    const fetchCalls: Array<{
      body: unknown;
      credentials: RequestCredentials | undefined;
      method: string;
      url: string;
    }> = [];
    globalThis.fetch = async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      fetchCalls.push({
        body: init?.body ? JSON.parse(String(init.body)) : null,
        credentials: init?.credentials,
        method: init?.method ?? "GET",
        url
      });
      return new Response(JSON.stringify([sampleRate]), {
        headers: { "Content-Type": "application/json" },
        status: 200
      });
    };

    await login("owner", "password123");
    await fetchLoginOperators();
    await quickOperatorLogin("operator_mama");
    await adminOperatorLogin("password123");
    await fetchCurrentUser();
    await fetchUsers();
    await fetchSalesReport({ period: "today" });
    await fetchDashboardTodaySales();
    await fetchAdminSettings();
    await updateAdminSettings({
      general: {
        shop_display_name: "Secret Grounds",
        shop_subtitle: "Computer Shop",
        pc_naming_prefix: "PC",
        timezone_display: "Asia/Manila"
      },
      sessions: {
        expiration_grace_seconds: 120,
        automatic_shutdown_on_expiration: false,
        reminder_thresholds_seconds: [900, 300, 60],
        manual_end_default_behavior: "lock_only"
      },
      customer_client: {
        reminder_sounds_enabled: true,
        final_warning_sound_enabled: true,
        active_overlay_enabled: true,
        overlay_position: "top-right",
        customer_branding_text: "Ask the cashier to add more time to continue."
      },
      wake_power: {
        wake_on_lan_enabled: true,
        shutdown_on_expiration_enabled: false,
        expected_shutdown_timeout_seconds: 60,
        power_command_safety_status: "Test mode active"
      },
      members: {
        default_member_pin_policy: "optional"
      },
      reports: {
        default_sales_period: "today",
        csv_timezone_display: "Asia/Manila"
      }
    });
    await resetAdminSettings("general");
    await fetchPcLayouts(true);
    await createUser({
      username: "cashier",
      display_name: "Cashier",
      password: "password123",
      role: "cashier"
    });
    await updateUser("user-1", {
      username: "cashier",
      display_name: "Cashier One",
      role: "admin"
    });
    await updateUserStatus("user-1", false);
    await resetUserPassword("user-1", "new-password");
    await logout();
    await resetMemberPin("member-1", "1234", "1234");
    await fetchRates(true);
    await createRate({
      name: "Temp",
      duration_seconds: 600,
      price: "12.30",
      rate_type: "package",
      sort_order: 99,
      is_active: true
    });
    await updateRate("rate-30", {
      name: "30 Minutes",
      duration_seconds: 1800,
      price: "50.00",
      rate_type: "package",
      sort_order: 10
    });
    await updateRateStatus("rate-30", false);
    await updatePcLayout("PC-03", {
      display_name: "PC 3",
      area_label: "SECRET GROUND SHOP",
      layout_order: 30,
      is_vip: false,
      is_enabled: false,
      mac_address: null
    });
    await startMemberSession("PC-01", {
      sessionMode: "member_balance",
      memberUsername: "ada.player",
      durationSeconds: 3600,
      memberPin: "1234"
    });
    await startSession("PC-01", { amount: "20.00" });
    await sendPcMessage("PC-01", {
      title: "Secret Grounds",
      message: "Please return to the cashier.",
      severity: "info",
      require_acknowledgement: false
    });
    await sendPowerCommand("PC-01", "sleep", {});
    await addTime("session-1", { amount: "5.00" });
    await forceEndSession("session-1");
    await recordPayment("session-1", {
      payment_method: "cash",
      amount: "20.00",
      cash_received: "50.00"
    });
    await fetchSystemHealth();
    await fetchSystemDatabase();
    await fetchSystemBackups();
    await createSystemBackup();
    await restoreSystemBackup("secretgrounds_2026-07-29_143500.db");
    await deleteSystemBackup("secretgrounds_2026-07-29_143500.db");
    await fetchSystemStorage();
    await fetchSystemInfo();
    await fetchSystemLogs();
    await fetchSystemClients();
    await fetchLiveActivity();
    await fetchUnclaimedStations();
    await fetchAssignedStations();
    await assignStation("AA:BB:CC:DD:EE:FF", "PC-11");
    await reassignStation("AA:BB:CC:DD:EE:01", "PC-11");
    await revokeStation("AA:BB:CC:DD:EE:FF");

    assert.deepEqual(
      fetchCalls.map((call) => ({ method: call.method, url: call.url })),
      [
        { method: "POST", url: "http://localhost:8000/auth/login" },
        { method: "GET", url: "http://localhost:8000/auth/operators" },
        { method: "POST", url: "http://localhost:8000/auth/operator/quick" },
        { method: "POST", url: "http://localhost:8000/auth/operator/admin" },
        { method: "GET", url: "http://localhost:8000/auth/me" },
        { method: "GET", url: "http://localhost:8000/users" },
        { method: "GET", url: "http://localhost:8000/sales/report?period=today" },
        { method: "GET", url: "http://localhost:8000/dashboard/today-sales" },
        { method: "GET", url: "http://localhost:8000/settings" },
        { method: "PUT", url: "http://localhost:8000/settings" },
        { method: "POST", url: "http://localhost:8000/settings/reset" },
        { method: "GET", url: "http://localhost:8000/pcs/layout?include_disabled=true" },
        { method: "POST", url: "http://localhost:8000/users" },
        { method: "PUT", url: "http://localhost:8000/users/user-1" },
        { method: "PATCH", url: "http://localhost:8000/users/user-1/status" },
        { method: "POST", url: "http://localhost:8000/users/user-1/reset-password" },
        { method: "POST", url: "http://localhost:8000/auth/logout" },
        { method: "POST", url: "http://localhost:8000/members/member-1/pin" },
        { method: "GET", url: "http://localhost:8000/rates?include_inactive=true" },
        { method: "POST", url: "http://localhost:8000/rates" },
        { method: "PUT", url: "http://localhost:8000/rates/rate-30" },
        { method: "PATCH", url: "http://localhost:8000/rates/rate-30/status" },
        { method: "PUT", url: "http://localhost:8000/pcs/PC-03/layout" },
        { method: "POST", url: "http://localhost:8000/sessions/start-member" },
        { method: "POST", url: "http://localhost:8000/sessions/start" },
        { method: "POST", url: "http://localhost:8000/pcs/PC-01/message" },
        { method: "POST", url: "http://localhost:8000/pcs/PC-01/power/sleep" },
        { method: "POST", url: "http://localhost:8000/sessions/session-1/add-time" },
        { method: "POST", url: "http://localhost:8000/sessions/session-1/force-end" },
        { method: "POST", url: "http://localhost:8000/sessions/session-1/payments" },
        { method: "GET", url: "http://localhost:8000/system/health" },
        { method: "GET", url: "http://localhost:8000/system/database" },
        { method: "GET", url: "http://localhost:8000/system/backups" },
        { method: "POST", url: "http://localhost:8000/system/backup" },
        { method: "POST", url: "http://localhost:8000/system/restore" },
        { method: "DELETE", url: "http://localhost:8000/system/backups/secretgrounds_2026-07-29_143500.db?confirm=true" },
        { method: "GET", url: "http://localhost:8000/system/storage" },
        { method: "GET", url: "http://localhost:8000/system/info" },
        { method: "GET", url: "http://localhost:8000/system/logs" },
        { method: "GET", url: "http://localhost:8000/system/clients" },
        { method: "GET", url: "http://localhost:8000/activity?limit=60" },
        { method: "GET", url: "http://localhost:8000/stations/unclaimed" },
        { method: "GET", url: "http://localhost:8000/stations/assigned" },
        { method: "POST", url: "http://localhost:8000/stations/AA%3ABB%3ACC%3ADD%3AEE%3AFF/assign" },
        { method: "POST", url: "http://localhost:8000/stations/AA%3ABB%3ACC%3ADD%3AEE%3A01/reassign" },
        { method: "POST", url: "http://localhost:8000/stations/AA%3ABB%3ACC%3ADD%3AEE%3AFF/revoke" }
      ]
    );
    assert.equal(fetchCalls.every((call) => call.credentials === "include"), true);
    assert.deepEqual(fetchCalls[43].body, { pc_name: "PC-11" });
    assert.deepEqual(fetchCalls[44].body, { pc_name: "PC-11" });
    assert.deepEqual(fetchCalls[45].body, null);
    assert.deepEqual(fetchCalls[0].body, {
      username: "owner",
      password: "password123"
    });
    assert.deepEqual(fetchCalls[2].body, {
      operator_id: "operator_mama"
    });
    assert.deepEqual(fetchCalls[3].body, {
      password: "password123"
    });
    assert.deepEqual(fetchCalls[15].body, {
      password: "new-password"
    });
    assert.deepEqual(fetchCalls[17].body, {
      pin: "1234",
      confirm_pin: "1234"
    });
    assert.equal((fetchCalls[9].body as { general: { shop_display_name: string } }).general.shop_display_name, "Secret Grounds");
    assert.deepEqual(fetchCalls[10].body, {
      category: "general"
    });
    assert.equal((fetchCalls[19].body as RateProfileInput).price, "12.30");
    assert.equal((fetchCalls[20].body as RateProfileInput).price, "50.00");
    assert.equal((fetchCalls[20].body as RateProfileInput).duration_seconds, 1800);
    assert.deepEqual(fetchCalls[22].body, {
      display_name: "PC 3",
      area_label: "SECRET GROUND SHOP",
      layout_order: 30,
      is_vip: false,
      is_enabled: false,
      mac_address: null
    });
    assert.deepEqual(fetchCalls[23].body, {
      pc_name: "PC-01",
      member_username: "ada.player",
      duration_seconds: 3600,
      member_pin: "1234"
    });
    assert.deepEqual(fetchCalls[24].body, {
      pc_name: "PC-01",
      amount: "20.00"
    });
    assert.deepEqual(fetchCalls[25].body, {
      title: "Secret Grounds",
      message: "Please return to the cashier.",
      severity: "info",
      require_acknowledgement: false
    });
    assert.deepEqual(fetchCalls[26].body, {});
    assert.deepEqual(fetchCalls[27].body, {
      amount: "5.00"
    });
    assert.deepEqual(fetchCalls[28].body, null);
    assert.deepEqual(fetchCalls[29].body, {
      payment_method: "cash",
      amount: "20.00",
      cash_received: "50.00"
    });
    assert.deepEqual(fetchCalls[34].body, {
      filename: "secretgrounds_2026-07-29_143500.db",
      confirm: true
    });
    assert.equal(
      getSystemBackupDownloadUrl("secretgrounds_2026-07-29_143500.db"),
      "http://localhost:8000/system/backups/secretgrounds_2026-07-29_143500.db/download"
    );
    assert.equal(
      getSalesExportUrl({ period: "custom", startDate: "2026-07-01", endDate: "2026-07-31" }),
      "http://localhost:8000/sales/export.csv?period=custom&start_date=2026-07-01&end_date=2026-07-31"
    );

    const saveEvents: string[] = [];
    const updatedMessage = await saveRateProfile({
      editingRateId: "rate-30",
      form: {
        name: "30 Minutes",
        minutes: "30",
        price: "50.00",
        rateType: "package",
        sortOrder: "10",
        stationScope: "standard"
      },
      createRateProfile: async () => {
        saveEvents.push("unexpected-create");
        return sampleRate;
      },
      updateRateProfile: async (rateId, input) => {
        saveEvents.push(`put:${rateId}:${input.price}`);
        return sampleRate;
      },
      refreshRates: async () => {
        saveEvents.push("refresh");
      }
    });
    assert.equal(updatedMessage, "Rate updated.");
    assert.deepEqual(saveEvents, ["put:rate-30:50.00", "refresh"]);

    const createEvents: string[] = [];
    const createdMessage = await saveRateProfile({
      editingRateId: null,
      form: {
        name: "Temp",
        minutes: "15",
        price: "25.00",
        rateType: "package",
        sortOrder: "40",
        stationScope: "standard"
      },
      createRateProfile: async (input) => {
        createEvents.push(`post:${input.price}:${input.is_active}`);
        return sampleRate;
      },
      updateRateProfile: async () => {
        createEvents.push("unexpected-update");
        return sampleRate;
      },
      refreshRates: async () => {
        createEvents.push("refresh");
      }
    });
    assert.equal(createdMessage, "Rate created.");
    assert.deepEqual(createEvents, ["post:25.00:true", "refresh"]);

    const ratesCss = readFileSync("src/app/rates/rates.module.css", "utf8");
    assert.match(ratesCss, /\.field input::placeholder/);
    assert.match(ratesCss, /\.field input:-webkit-autofill/);
    assert.match(ratesCss, /\.errorMessage/);
    assert.doesNotMatch(ratesCss, /\.meteredCard/);
    assert.match(ratesCss, /color: #f8fafc/);

    const ratesManagerSource = readFileSync("src/components/RatesManager.tsx", "utf8");
    assert.doesNotMatch(ratesManagerSource, /Regular Metered Rate/);
    assert.doesNotMatch(ratesManagerSource, /Used when the cashier enters an amount/);
    assert.doesNotMatch(ratesManagerSource, /Edit Metered Rate/);
    assert.doesNotMatch(ratesManagerSource, /rate_type === "metered"/);
    assert.doesNotMatch(ratesManagerSource, /rateType === "metered"/);
    assert.match(ratesManagerSource, /Packages<\/h2>/);
    assert.match(ratesManagerSource, /Create Package/);
    assert.match(ratesManagerSource, /stationScope/);
    assert.match(ratesManagerSource, /stationScopeLabel/);
    assert.match(ratesManagerSource, />Standard</);
    assert.match(ratesManagerSource, />VIP</);
    assert.match(ratesManagerSource, />Any Station</);
    assert.match(ratesManagerSource, /guest_only/);
    assert.match(ratesManagerSource, /eligible_start_local_time/);

    const sessionDialogSourceForOpenTimeRetirement = readFileSync("src/components/SessionDialog.tsx", "utf8");
    assert.doesNotMatch(sessionDialogSourceForOpenTimeRetirement, /Enter Amount/);
    assert.doesNotMatch(sessionDialogSourceForOpenTimeRetirement, />Open Time</);
    assert.doesNotMatch(sessionDialogSourceForOpenTimeRetirement, /"open_time"/);
    assert.doesNotMatch(sessionDialogSourceForOpenTimeRetirement, /calculatePreviewSeconds/);
    assert.doesNotMatch(sessionDialogSourceForOpenTimeRetirement, /meteredRate/);
    assert.match(sessionDialogSourceForOpenTimeRetirement, /Packages\s*<\/button>/);
    assert.match(sessionDialogSourceForOpenTimeRetirement, /Member\s*<\/button>/);
    // Stage 4: VIP playable-time preview is server-computed (fetched from
    // the backend), never recomputed with 2:1 conversion logic in React.
    assert.match(sessionDialogSourceForOpenTimeRetirement, /fetchMemberVipPreview/);
    assert.match(sessionDialogSourceForOpenTimeRetirement, /Playable on VIP/);
    assert.doesNotMatch(sessionDialogSourceForOpenTimeRetirement, /STANDARD_SECONDS_PER_VIP_SECOND/);

    const monitorTileSource = readFileSync("src/components/PcMonitorTile.tsx", "utf8");
    assert.match(monitorTileSource, /monitorName/);
    assert.match(monitorTileSource, /monitorStatusLight/);
    assert.match(monitorTileSource, /monitorVipBadge/);
    assert.doesNotMatch(monitorTileSource, /Start Session|Add Time|End Session|Collect Payment|Start & Wake/);

    const dashboardCss = readFileSync("src/app/page.module.css", "utf8");
    assert.match(dashboardCss, /\.monitorTileSelected/);
    assert.match(dashboardCss, /\.monitorGrid/);
    assert.match(dashboardCss, /repeat\(auto-fill, minmax\(150px, 176px\)\)/);
    assert.match(dashboardCss, /justify-content: start/);
    assert.match(dashboardCss, /\.dashboardWorkspace/);
    assert.match(dashboardCss, /\.selectedPcRail/);
    assert.match(dashboardCss, /\.noSelectionPanel/);
    assert.match(dashboardCss, /\.pcControlDock/);
    assert.match(dashboardCss, /\.monitorTileGrace/);
    assert.match(dashboardCss, /\.monitorTileSettlement/);
    assert.match(dashboardCss, /\.monitorTileDanger/);
    assert.match(dashboardCss, /@media \(max-width: 900px\)/);
    assert.match(dashboardCss, /max-height: calc\(100vh - 48px\)/);
    assert.match(dashboardCss, /\.pcControlModal/);
    assert.match(dashboardCss, /width: min\(980px, 92vw\)/);
    assert.match(dashboardCss, /max-height: 88vh/);
    assert.match(dashboardCss, /\.pcControlGrid/);
    assert.match(dashboardCss, /grid-template-columns: minmax\(0, 1fr\) minmax\(340px, 400px\)/);
    assert.match(dashboardCss, /backdrop-filter: none/);

    const pcDetailsSource = readFileSync("src/components/PcDetailsPanel.tsx", "utf8");
    assert.match(pcDetailsSource, /variant = "modal"/);
    assert.match(pcDetailsSource, /pcControlDock/);
    assert.match(pcDetailsSource, /role=\{variant === "modal" \? "dialog" : "region"\}/);
    assert.match(pcDetailsSource, /aria-modal=\{variant === "modal" \? "true" : undefined\}/);
    assert.match(pcDetailsSource, /event\.key === "Escape"/);
    assert.match(pcDetailsSource, /querySelectorAll<HTMLElement>/);
    assert.match(pcDetailsSource, /Session Actions/);
    assert.match(pcDetailsSource, /Messaging/);
    assert.match(pcDetailsSource, /Power Controls/);
    assert.match(pcDetailsSource, /Advanced \/ Technical Details/);
    assert.match(pcDetailsSource, /<Icon name="message" \/>[\s\S]*Send Message/);
    assert.match(pcDetailsSource, /<Icon name="power" \/>[\s\S]*Wake PC/);
    assert.match(pcDetailsSource, /<Icon name="moon" \/>[\s\S]*Sleep/);
    assert.match(pcDetailsSource, /<Icon name="rotate" \/>[\s\S]*Restart/);
    assert.match(pcDetailsSource, /<Icon name="powerOff" \/>[\s\S]*Shutdown/);
    assert.match(pcDetailsSource, /strokeWidth: 2\.15/);
    assert.match(pcDetailsSource, /M18\.4 6\.6a9 9 0 1 1-12\.8 0/);
    assert.match(pcDetailsSource, /M21 12\.8A8\.5 8\.5 0 1 1/);
    assert.match(pcDetailsSource, /M20 12a8 8 0 1 1-2\.35-5\.65L21 9/);
    assert.match(pcDetailsSource, /M3\.8 3\.8 20\.2 20\.2/);
    assert.match(pcDetailsSource, /<Icon name="save" \/>[\s\S]*Save MAC/);
    assert.match(pcDetailsSource, /Wake-on-LAN is unavailable until a MAC address is saved/);
    assert.match(pcDetailsSource, /MAC address[\s\S]*<input[\s\S]*Save MAC/);
    assert.ok(
      pcDetailsSource.lastIndexOf("MAC address") > pcDetailsSource.indexOf("Advanced / Technical Details")
    );

    const loginSource = readFileSync("src/components/LoginClient.tsx", "utf8");
    assert.match(loginSource, /Who&apos;s in charge right now\?/);
    assert.match(loginSource, /fetchLoginOperators/);
    assert.match(loginSource, /quickOperatorLogin/);
    assert.match(loginSource, /adminOperatorLogin/);
    assert.match(loginSource, /Show password/);
    assert.doesNotMatch(loginSource, /Username/);
    assert.doesNotMatch(loginSource, /placeholder="cashier"/);

    const dashboardSource = readFileSync("src/components/DashboardClient.tsx", "utf8");
    assert.doesNotMatch(dashboardSource, /Sino ang naka-bantay ngayon/);
    assert.doesNotMatch(dashboardSource, /Current on-duty operator/);
    // Stage 7: the main-dashboard summary card is server-authoritative
    // Operator's Sale (Stage 5), not Today's Sales -- /dashboard/today-sales
    // remains a valid endpoint for other consumers (see the dedicated
    // fetchDashboardTodaySales assertions further below), just not used here.
    assert.match(dashboardSource, /fetchOperatorSale/);
    assert.doesNotMatch(dashboardSource, /fetchDashboardTodaySales/);
    // Operator's Sale must reuse the existing single poll loop, not a
    // second/aggressive interval of its own -- exactly the same two
    // setInterval calls (data refresh + clock tick) as before Stage 7.
    assert.equal((dashboardSource.match(/window\.setInterval/g) ?? []).length, 2);
    assert.match(dashboardSource, /window\.setInterval\(\(\) => void refresh\(\), pollIntervalMs\)/);
    assert.match(dashboardSource, /setOperatorSale\)/);
    assert.match(dashboardSource, /catch\(\(\) => setOperatorSale\(null\)\)/);
    assert.match(dashboardSource, /displayAreaLabel/);
    assert.match(dashboardSource, /SECRET GROUNDS SHOP/);
    assert.match(dashboardSource, /Select a PC/);
    assert.match(dashboardSource, /Choose a station to view session, billing, messaging, and power controls\./);
    assert.match(dashboardSource, /Register First PC/);
    assert.match(dashboardSource, /variant="dock"/);
    assert.doesNotMatch(dashboardSource, /startOpenTimeSession/);
    assert.doesNotMatch(dashboardSource, /meteredRate/);
    // Historical open_time sessions (already active/settling before this
    // upgrade) must still display and settle correctly -- only the ability
    // to start a *new* one was removed.
    assert.match(dashboardSource, /pc\.session_mode === "open_time"/);
    assert.match(dashboardSource, /calculateOpenTimeEstimate/);

    const membersSource = readFileSync("src/components/MembersManager.tsx", "utf8");
    // Stage 3: both balances must be visible, and VIP purchases must never
    // be refundable through the UI (server independently enforces this too).
    assert.match(membersSource, />Standard Time</);
    assert.match(membersSource, />VIP Time</);
    assert.match(membersSource, /vip_balance_seconds/);
    assert.match(membersSource, /balance_type === "vip"/);
    assert.match(membersSource, /VIP promo purchases are non-refundable/);
    assert.match(membersSource, /transaction\.balance_type !== "vip"/);
    assert.match(membersSource, /Search by display name, username, or phone/);
    assert.match(membersSource, /useToast/);
    assert.match(membersSource, /pushToast/);
    assert.match(membersSource, /Member time added successfully/);
    assert.match(membersSource, /Unable to add member time/);
    assert.match(membersSource, /Profile saved/);
    assert.match(membersSource, /PIN reset successfully/);
    assert.match(membersSource, /PINs do not match/);
    assert.doesNotMatch(membersSource, /setMessage\("Member time added/);
    assert.doesNotMatch(membersSource, /setMessage\("Member PIN updated/);
    assert.doesNotMatch(membersSource, /Start a cashier shift before recording payments/);
    assert.match(membersSource, /setIsCreateOpen\(true\)/);
    assert.match(membersSource, /role="dialog"/);
    assert.match(membersSource, /Create Member/);
    assert.match(membersSource, /Username/);
    assert.match(membersSource, /Display name, optional/);
    assert.match(membersSource, /Confirm PIN/);
    assert.match(membersSource, /Account Summary/);
    assert.match(membersSource, /Balance Management/);
    assert.match(membersSource, /Security/);
    assert.match(membersSource, /role="tablist"/);
    assert.match(membersSource, /Overview/);
    assert.match(membersSource, /Activity/);
    assert.match(membersSource, /Reset Member PIN/);
    assert.match(membersSource, /activeMemberTab === "overview"/);
    assert.match(membersSource, /activeMemberTab === "balance"/);
    assert.match(membersSource, /activeMemberTab === "security"/);
    assert.match(membersSource, /activeMemberTab === "activity"/);
    assert.match(membersSource, /setActiveMemberTab\("overview"\)/);
    assert.match(membersSource, /PIN entries do not match/);
    assert.match(membersSource, /Not configured/);
    assert.doesNotMatch(membersSource, /Technical Information/);
    assert.doesNotMatch(membersSource, /memberTable/);
    assert.match(membersSource, /No activity has been recorded for this member/);
    assert.match(membersSource, /role="button"/);
    assert.match(membersSource, /onKeyDown/);
    assert.match(membersSource, /event\.key === "Enter" \|\| event\.key === " "/);
    assert.match(membersSource, /event\.stopPropagation/);
    assert.doesNotMatch(membersSource, /View Member/);
    assert.match(membersSource, /No members yet/);
    assert.match(membersSource, /No members found/);
    assert.match(membersSource, /Clear Filters/);
    assert.match(membersSource, /No phone/);
    assert.match(membersSource, /Setup Required/);
    assert.match(membersSource, /Ready/);
    assert.match(membersSource, /getMemberAvatarInitial/);
    assert.match(membersSource, /getMemberLastActivityLabel/);
    assert.match(membersSource, /memberCardList/);
    assert.match(membersSource, /memberListCard/);
    assert.match(membersSource, /memberListCardSelected/);
    assert.match(membersSource, /memberAvatar/);
    assert.match(membersSource, /memberUsernameMissing/);
    assert.match(membersSource, /\["all", "active", "setup", "inactive"\] as const/);
    assert.match(membersSource, /createError/);
    assert.match(membersSource, /confirmingStatusMember/);
    assert.match(membersSource, /createPortal/);
    assert.match(membersSource, /memberFloatingMenu/);
    assert.match(membersSource, /aria-haspopup="menu"/);
    assert.match(membersSource, /aria-expanded/);
    assert.match(membersSource, /Escape/);
    assert.match(membersSource, /memberMenuTriggerRefs\.current\.get\(menuMemberId\)\?\.focus/);
    assert.match(membersSource, /window\.innerHeight - rect\.bottom/);
    assert.match(membersSource, /window\.addEventListener\("scroll"/);
    assert.match(membersSource, /This member will no longer be available for new sessions/);
    assert.doesNotMatch(membersSource, /phone_number \?\? "None"/);

    const membersCss = readFileSync("src/app/page.module.css", "utf8");
    assert.doesNotMatch(membersCss, /\.memberTable/);
    assert.match(membersCss, /\.memberModal/);
    assert.match(membersCss, /\.memberDetailPopout/);
    assert.match(membersCss, /\.memberModalLayer/);
    assert.match(membersCss, /--member-modal-top: 104px/);
    assert.match(membersCss, /\.memberDetailTabs/);
    assert.match(membersCss, /\.memberDetailTabActive/);
    assert.match(membersCss, /\.memberCardList/);
    assert.match(membersCss, /\.memberListCard/);
    assert.match(membersCss, /\.memberListCardSelected/);
    assert.match(membersCss, /\.memberAvatar/);
    assert.match(membersCss, /\.memberAvatarSetup/);
    assert.match(membersCss, /\.memberCardStat/);
    assert.match(membersCss, /\.memberCardActions/);
    assert.match(membersCss, /\.memberListEnd/);
    assert.match(membersCss, /\.memberSecurityError/);
    assert.match(membersCss, /\.memberStatus-ready/);
    assert.match(membersCss, /\.memberStatus-setup/);
    assert.match(membersCss, /\.memberStatus-disabled/);
    assert.match(membersCss, /\.memberStatus-locked/);
    assert.match(membersCss, /\.memberSummaryStatus-ready/);
    assert.match(membersCss, /\.memberSummaryStatus-setup/);
    assert.match(membersCss, /\.memberPasswordToggle/);
    assert.match(membersCss, /\.memberDialogMessage/);
    assert.match(membersCss, /\.memberMoreButton/);
    assert.match(membersCss, /\.memberFloatingMenu/);
    assert.match(membersCss, /position: fixed/);
    assert.match(membersCss, /z-index: 80/);
    assert.match(membersCss, /@media \(max-width: 760px\)/);
    assert.match(membersCss, /grid-template-columns: repeat\(2, minmax\(0, 1fr\)\) auto/);
    assert.match(membersCss, /grid-column: 1 \/ 3/);

    const authShellSource = readFileSync("src/components/AuthShell.tsx", "utf8");
    assert.match(authShellSource, /isAdminOperator/);
    assert.doesNotMatch(authShellSource, /user\?\.role === "cashier" && shift === null/);
    assert.doesNotMatch(authShellSource, /before recording payments/);
    assert.match(authShellSource, /appShell__adminMenu/);
    assert.match(authShellSource, /ToastProvider/);
    assert.match(authShellSource, /Admin/);
    assert.match(authShellSource, /SalesNavIcon/);
    assert.match(authShellSource, /HistoryNavIcon/);
    assert.match(authShellSource, /Operator Activity/);
    assert.match(authShellSource, /Staff/);
    assert.match(authShellSource, /Sales/);
    assert.match(authShellSource, /History/);
    assert.match(authShellSource, /Remote Commands/);
    assert.match(authShellSource, /Settings/);
    assert.match(authShellSource, /System/);
    assert.ok(
      authShellSource.indexOf('href="/members"') < authShellSource.indexOf('href="/sales"'),
      "Members should precede Sales in the main nav"
    );
    assert.ok(
      authShellSource.indexOf('href="/sales"') < authShellSource.indexOf('href="/rates"'),
      "Sales should precede Rates in the main nav"
    );
    assert.ok(
      authShellSource.indexOf('{ href: "/history"') < authShellSource.indexOf('{ href: "/operators"'),
      "History should be the first Admin dropdown item"
    );
    assert.match(authShellSource, /className=\{pathname === "\/sales" \? "appShell__linkActive" : ""\}/);
    assert.match(authShellSource, /const isAdminNavPath = ADMIN_NAV_ITEMS\.some/);
    assert.match(authShellSource, /className=\{isAdminNavPath \? "appShell__linkActive" : ""\}/);
    assert.doesNotMatch(authShellSource, /const isAdminPath/);
    assert.match(authShellSource, /href: "\/settings"/);
    assert.match(authShellSource, /href: "\/system"/);
    assert.match(authShellSource, /href: "\/history"/);
    assert.match(authShellSource, /href="\/rates"/);
    assert.match(authShellSource, /href="\/sales"/);
    assert.doesNotMatch(authShellSource, /href="\/history"/);
    assert.match(authShellSource, /aria-expanded/);
    assert.match(authShellSource, /aria-haspopup="menu"/);
    assert.match(authShellSource, /appShell__chevron/);
    assert.match(authShellSource, /pointerdown/);
    assert.match(authShellSource, /Escape/);
    assert.match(authShellSource, /adminButtonRef\.current\?\.focus/);
    assert.match(authShellSource, /aria-current/);
    assert.match(authShellSource, /role="menuitem"/);
    assert.match(authShellSource, /setIsAdminMenuOpen\(false\)/);
    assert.doesNotMatch(authShellSource, /href="\/shifts">Shifts/);
    assert.doesNotMatch(authShellSource, /href="\/users">Users/);

    // Stage 7 leaves Stage 4's End Shift flow untouched: closing the shift
    // still clears local user/shift state and redirects to /login, which
    // is what makes the main-dashboard Operator's Sale card correctly go
    // stale/neutral on next load rather than something Stage 7 had to add.
    assert.match(authShellSource, /await closeShift\(/);
    assert.match(authShellSource, /setShift\(null\)/);
    assert.match(authShellSource, /setUser\(null\)/);
    assert.match(authShellSource, /router\.replace\("\/login"\)/);

    const operatorsSource = readFileSync("src/components/OperatorsManager.tsx", "utf8");
    assert.match(operatorsSource, /Manage who can operate Secret Grounds/);
    assert.match(operatorsSource, /Add Operator/);
    assert.match(operatorsSource, /Search operators/);
    assert.match(operatorsSource, /Administrator/);
    assert.match(operatorsSource, /staffDirectoryRow/);
    assert.match(operatorsSource, /Password Required/);
    assert.match(operatorsSource, /Quick Access/);
    assert.match(operatorsSource, /Never signed in/);
    assert.match(operatorsSource, /createAdminOperator/);
    assert.match(operatorsSource, /updateAdminOperator/);
    assert.match(operatorsSource, /disableAdminOperator/);
    assert.match(operatorsSource, /enableAdminOperator/);
    assert.match(operatorsSource, /archiveAdminOperator/);
    assert.match(operatorsSource, /resetAdminOperatorPassword/);
    assert.match(operatorsSource, /signOutAdminOperator/);
    assert.match(operatorsSource, /active_session_count > 0/);
    assert.match(operatorsSource, /Sign Out Operator/);
    assert.doesNotMatch(operatorsSource, /Reset Quick Access Session/);
    assert.doesNotMatch(operatorsSource, /Reset Password/);
    assert.match(operatorsSource, /Change Password/);
    assert.match(operatorsSource, /View Activity/);
    assert.match(operatorsSource, /staffActivityDrawer/);
    assert.match(operatorsSource, /fetchOperatorActivity\(\{ dateScope: "all", operatorId: operator\.operator_id \}\)/);
    assert.doesNotMatch(operatorsSource, /href=\{`\/operator-activity/);
    assert.match(operatorsSource, /passwordDialog/);
    assert.match(operatorsSource, /Change Password<\/h2>/);
    assert.match(operatorsSource, /Set a new password for \{operator\.display_name\}\./);
    assert.match(operatorsSource, /The current password cannot be viewed\. Saving will replace it with the new password\./);
    assert.match(operatorsSource, /At least 8 characters\./);
    assert.match(operatorsSource, /showPassword/);
    assert.match(operatorsSource, /\{showPassword \? "Hide" : "Show"\}/);
    assert.match(operatorsSource, /disabled=\{!canSave\}/);
    assert.match(operatorsSource, /Password is required\./);
    assert.match(operatorsSource, /Passwords do not match\./);
    assert.match(operatorsSource, /You will be signed out after changing your password\./);
    assert.match(operatorsSource, /Customer PC sessions and billing are not affected/);
    assert.match(operatorsSource, /aria-modal="true"/);
    assert.match(operatorsSource, /event\.key === "Escape"/);
    assert.match(operatorsSource, /Archive Operator/);
    assert.match(operatorsSource, /operator\.is_protected/);
    assert.doesNotMatch(operatorsSource, /Technical details/);

    const operatorsCss = readFileSync("src/app/users/users.module.css", "utf8");
    assert.match(operatorsCss, /\.staffDirectoryRow/);
    assert.match(operatorsCss, /\.staffPageHeader/);
    assert.match(operatorsCss, /\.staffHeaderActions/);
    assert.match(operatorsCss, /\.adminMiniBadge/);
    assert.match(operatorsCss, /\.staffRowActions/);
    assert.match(operatorsCss, /\.staffMoreMenu/);
    assert.match(operatorsCss, /\.staffDialog/);
    assert.match(operatorsCss, /\.staffActivityDrawer/);
    assert.match(operatorsCss, /\.staffDrawerBackdrop/);
    assert.match(operatorsCss, /\.staffDangerAction/);
    assert.match(operatorsCss, /\.overlay/);
    assert.match(operatorsCss, /align-items: center/);
    assert.match(operatorsCss, /\.passwordDialog/);
    assert.match(operatorsCss, /\.staffCloseButton/);
    assert.match(operatorsCss, /\.passwordField/);
    assert.match(operatorsCss, /\.passwordDialogActions/);
    assert.match(operatorsCss, /min-height: 52px/);
    assert.match(operatorsCss, /width: fit-content/);
    assert.match(operatorsCss, /grid-template-columns: minmax\(260px, 1\.6fr\) minmax\(132px, 0\.62fr\) minmax\(78px, 0\.28fr\) minmax\(190px, 0\.72fr\) 156px/);
    assert.match(operatorsCss, /@media \(max-width: 960px\)/);
    assert.match(operatorsCss, /grid-template-columns: 1fr auto/);

    const operatorActivitySource = readFileSync("src/components/OperatorActivity.tsx", "utf8");
    assert.match(operatorActivitySource, /summaryStrip/);
    assert.match(operatorActivitySource, /Operators Active/);
    assert.match(operatorActivitySource, /humanizeAction/);
    assert.match(operatorActivitySource, /activityContext/);
    assert.match(operatorActivitySource, /activityAmount/);
    assert.match(operatorActivitySource, /return item\.amount \?[\s\S]*: "-"/);
    assert.match(operatorActivitySource, /Operator selected/);
    assert.match(operatorActivitySource, /Payment collected/);
    assert.match(operatorActivitySource, /No operator activity found/);
    assert.match(operatorActivitySource, /View Details/);

    const settingsPageSource = readFileSync("src/app/settings/page.tsx", "utf8");
    assert.match(settingsPageSource, /Admin Settings/);
    assert.match(settingsPageSource, /SettingsManager/);

    const settingsSource = readFileSync("src/components/SettingsManager.tsx", "utf8");
    assert.match(settingsSource, /fetchAdminSettings/);
    assert.match(settingsSource, /updateAdminSettings/);
    assert.match(settingsSource, /resetAdminSettings/);
    assert.match(settingsSource, /Expiration grace period/);
    assert.match(settingsSource, /Reminder thresholds/);
    assert.match(settingsSource, /Power command safety status/);
    assert.match(settingsSource, /Reset Unsaved Changes/);
    assert.match(settingsSource, /Reset All Defaults/);
    assert.doesNotMatch(settingsSource, /SECRET_GROUNDS_TEST_MODE/);
    assert.doesNotMatch(settingsSource, /password_hash/);

    const settingsCss = readFileSync("src/app/settings/settings.module.css", "utf8");
    assert.match(settingsCss, /\.categoryNav/);
    assert.match(settingsCss, /\.switchField/);
    assert.match(settingsCss, /color: #f8fafc/);

    const systemPageSource = readFileSync("src/app/system/page.tsx", "utf8");
    assert.match(systemPageSource, /Admin System/);
    assert.match(systemPageSource, /SystemManager/);

    const systemSource = readFileSync("src/components/SystemManager.tsx", "utf8");
    assert.match(systemSource, /fetchSystemHealth/);
    assert.match(systemSource, /createSystemBackup/);
    assert.match(systemSource, /restoreSystemBackup/);
    assert.match(systemSource, /deleteSystemBackup/);
    assert.match(systemSource, /StatusCard/);
    assert.match(systemSource, /ActivityFeed/);
    assert.match(systemSource, /ActivityDrawerTrigger/);
    assert.match(systemSource, /limit=\{10\}/);
    assert.match(systemSource, /Restoring will replace the current database/);
    assert.match(systemSource, /Client Health/);
    assert.match(systemSource, /Recent Logs/);
    assert.match(systemSource, /Schema Version/);
    assert.match(systemSource, /Not versioned/);
    assert.match(systemSource, /No backups yet/);
    assert.match(systemSource, /Never connected/);
    assert.match(systemSource, /Not reported/);
    assert.match(systemSource, /Last known/);
    assert.match(systemSource, /humanizeEvent/);
    assert.match(systemSource, /cleanLogMessage/);

    const activityFeedSource = readFileSync("src/components/ActivityFeed.tsx", "utf8");
    assert.match(activityFeedSource, /fetchLiveActivity/);
    assert.match(activityFeedSource, /activityRow/);
    assert.match(activityFeedSource, /pushToast/);
    assert.match(activityFeedSource, /memo/);
    assert.match(activityFeedSource, /ActivityDrawerTrigger/);
    assert.match(activityFeedSource, /aria-expanded/);
    assert.match(activityFeedSource, /role="dialog"/);
    assert.match(activityFeedSource, /Escape/);
    assert.match(activityFeedSource, /triggerRef\.current\?\.focus/);
    assert.match(activityFeedSource, /nextUnreadCount/);
    assert.match(activityFeedSource, /toastedKeysRef/);
    assert.match(activityFeedSource, /shouldToastForActivity/);

    const toastSource = readFileSync("src/components/ToastProvider.tsx", "utf8");
    assert.match(toastSource, /toastStack/);
    assert.match(toastSource, /CloseButton/);
    assert.doesNotMatch(toastSource, />\s*x\s*<\/button>/);
    assert.match(toastSource, /toast\.title !== title/);
    assert.match(toastSource, /toast\.message !== message/);
    assert.match(toastSource, /durationMs/);
    assert.match(toastSource, /success/);
    assert.match(toastSource, /warning/);
    assert.match(toastSource, /error/);

    const statusCardSource = readFileSync("src/components/StatusCard.tsx", "utf8");
    assert.match(statusCardSource, /statusCardIcon/);
    assert.match(statusCardSource, /variant === "dashboard"/);
    assert.match(statusCardSource, /memo/);

    const dashboardSummarySource = readFileSync("src/components/DashboardSummary.tsx", "utf8");
    assert.match(dashboardSummarySource, /SummaryIconName/);
    assert.match(dashboardSummarySource, /icon: "available"/);
    assert.match(dashboardSummarySource, /icon: "inUse"/);
    assert.match(dashboardSummarySource, /icon: "sales"/);
    assert.match(dashboardSummarySource, /icon: "operator"/);
    assert.match(dashboardSummarySource, /variant="dashboard"/);
    assert.doesNotMatch(dashboardSummarySource, /icon: "OK"/);

    // Stage 7: main-dashboard card is Operator's Sale (Stage 5
    // server-authoritative), replacing Today's Sales on this card only.
    assert.match(dashboardSummarySource, /label: "Operator's Sale"/);
    assert.doesNotMatch(dashboardSummarySource, /label: "Today's Sales"/);
    assert.match(dashboardSummarySource, /OperatorSaleSummary/);
    // The card only ever formats/passes through fields the backend already
    // computed (operator_sale, operator_name, has_open_shift,
    // operator_user_id) -- it never sums payments or shift totals itself.
    assert.match(dashboardSummarySource, /formatMoney\(operatorSale\.operator_sale\)/);
    assert.doesNotMatch(dashboardSummarySource, /\.payments\b/);
    assert.doesNotMatch(dashboardSummarySource, /\.totals\b/);
    assert.doesNotMatch(dashboardSummarySource, /reduce\(/);
    assert.match(dashboardSummarySource, /No operator on duty/);
    assert.match(dashboardSummarySource, /No open shift/);
    assert.match(dashboardSummarySource, /Current shift/);
    assert.match(dashboardSummarySource, /operatorSale === null/);
    assert.match(dashboardSummarySource, /value: "--"/);
    assert.match(dashboardSummarySource, /operatorSale\.has_open_shift/);
    assert.match(dashboardSummarySource, /operatorSale\.operator_user_id === null/);
    assert.match(dashboardSummarySource, /operatorSale\.operator_name/);

    const pageTitleIconSource = readFileSync("src/components/PageTitleIcon.tsx", "utf8");
    assert.match(pageTitleIconSource, /PageTitleIcon/);
    assert.match(pageTitleIconSource, /dashboard/);
    assert.match(pageTitleIconSource, /members/);
    assert.match(pageTitleIconSource, /receipt/);
    assert.match(pageTitleIconSource, /history/);
    assert.match(pageTitleIconSource, /monitor-cog/);

    const dashboardClientSource = readFileSync("src/components/DashboardClient.tsx", "utf8");
    assert.match(dashboardClientSource, /PageTitleIcon name="dashboard"/);
    assert.match(dashboardClientSource, /pageTitleLockup/);
    assert.match(dashboardClientSource, /ActivityDrawerTrigger/);
    assert.doesNotMatch(dashboardClientSource, /<ActivityFeed/);
    assert.match(dashboardClientSource, /pushToast/);
    assert.ok(
      dashboardClientSource.indexOf("<DashboardSummary") < dashboardClientSource.indexOf("dashboardWorkspace")
    );

    const pageCss = readFileSync("src/app/page.module.css", "utf8");
    assert.match(pageCss, /\.iconButton/);
    assert.match(pageCss, /\.iconButtonSvg/);
    assert.match(pageCss, /transform: translateY\(-1px\) scale\(1\.02\)/);
    assert.match(pageCss, /\.changeSummary/);
    assert.match(pageCss, /\.changeSummary-muted/);
    assert.match(pageCss, /\.changeSummary-warning/);
    assert.match(pageCss, /\.changeSummary-ready/);
    assert.match(pageCss, /\.statusCard/);
    assert.match(pageCss, /\.statusCardDashboard/);
    assert.match(pageCss, /\.statusCardGlyph/);
    assert.match(pageCss, /grid-template-columns: repeat\(4, minmax\(0, 1fr\)\)/);
    assert.match(pageCss, /grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
    assert.match(pageCss, /\.activityPanel/);
    assert.match(pageCss, /\.activityTrigger/);
    assert.match(pageCss, /\.activityDrawer/);
    assert.match(pageCss, /\.activityDrawerBackdrop/);
    assert.match(pageCss, /\.buttonIcon/);
    assert.match(pageCss, /height: 19px/);
    assert.match(pageCss, /width: 19px/);
    assert.match(pageCss, /line-height: 1/);
    assert.match(pageCss, /scrollbar-gutter: stable/);
    assert.match(pageCss, /@media \(max-width: 520px\)/);
    assert.match(pageCss, /@keyframes activityIn/);
    assert.match(pageCss, /@keyframes drawerSlideIn/);

    const globalsSource = readFileSync("src/app/globals.css", "utf8");
    assert.match(globalsSource, /--radius-card/);
    assert.match(globalsSource, /\.pageTitleLockup/);
    assert.match(globalsSource, /\.pageTitleIcon/);
    assert.match(globalsSource, /\.pageTitleIcon__svg/);
    assert.match(globalsSource, /\.toastStack/);
    assert.match(globalsSource, /width: 640px/);
    assert.match(globalsSource, /\.toast--success/);
    assert.match(globalsSource, /\.toast--error/);
    assert.match(globalsSource, /\.toast--warning/);
    assert.match(globalsSource, /--global-header-height/);
    assert.match(globalsSource, /--z-confirm-modal/);
    assert.match(globalsSource, /--scrollbar-track/);
    assert.match(globalsSource, /--scrollbar-thumb-hover/);
    assert.match(globalsSource, /scrollbar-color: var\(--scrollbar-thumb\) var\(--scrollbar-track\)/);
    assert.match(globalsSource, /scrollbar-width: thin/);
    assert.match(globalsSource, /::-webkit-scrollbar/);
    assert.match(globalsSource, /::-webkit-scrollbar-thumb:hover/);
    assert.match(globalsSource, /scrollbar-gutter: stable/);
    assert.match(globalsSource, /height: 18px/);
    assert.match(globalsSource, /stroke-width: 2/);
    assert.match(globalsSource, /z-index: var\(--z-toast\)/);
    assert.match(globalsSource, /@keyframes toastIn/);
    assert.match(globalsSource, /scale\(0\.97\)/);
    assert.match(globalsSource, /\.appShell__adminMenu > button/);
    assert.match(globalsSource, /\.appShell__chevron/);

    const systemCss = readFileSync("src/app/system/system.module.css", "utf8");
    assert.match(systemCss, /\.logsPanel/);
    assert.match(systemCss, /\.emptyBackupState/);
    assert.match(systemCss, /position: sticky/);
    assert.match(systemCss, /rgba\(8, 17, 31/);

    const historyPageSource = readFileSync("src/app/history/page.tsx", "utf8");
    assert.match(historyPageSource, /Review completed, active, and settled computer sessions\./);

    const historySource = readFileSync("src/components/HistoryTable.tsx", "utf8");
    assert.match(historySource, /pageSize = 25/);
    assert.match(historySource, /summaryStrip/);
    assert.match(historySource, /Clear Filters/);
    assert.match(historySource, /View Details/);
    assert.match(historySource, /readableDuration/);
    assert.match(historySource, /historyCards/);
    assert.match(historySource, /role="dialog"/);
    assert.doesNotMatch(historySource, /Original|Added Amount|Payment Status/);

    const historyCss = readFileSync("src/app/history/history.module.css", "utf8");
    assert.match(historyCss, /\.summaryStrip/);
    assert.match(historyCss, /\.filters/);
    assert.match(historyCss, /\.historyCards/);
    assert.match(historyCss, /\.pagination/);
    assert.match(historyCss, /@media \(max-width: 760px\)/);

    const remoteCommandsSource = readFileSync("src/app/remote-commands/page.tsx", "utf8");
    assert.match(remoteCommandsSource, /Review power, Wake-on-LAN, message, and announcement commands sent to shop PCs\./);
    assert.match(remoteCommandsSource, /Wake PC/);
    assert.match(remoteCommandsSource, /Send Message/);
    assert.match(remoteCommandsSource, /Shut Down/);
    assert.match(remoteCommandsSource, /Announcement/);
    assert.match(remoteCommandsSource, /Wake packet sent/);
    assert.match(remoteCommandsSource, /Message displayed/);
    assert.match(remoteCommandsSource, /Command accepted/);
    assert.match(remoteCommandsSource, /Failure reason/);
    assert.match(remoteCommandsSource, /Not applicable/);
    assert.match(remoteCommandsSource, /Forced/);
    assert.match(remoteCommandsSource, /selectedCommand\.forced \? "Yes" : "No"/);
    assert.match(remoteCommandsSource, /Operator note/);
    assert.match(remoteCommandsSource, /View Details/);
    assert.match(remoteCommandsSource, /pageSize = 25/);
    assert.match(remoteCommandsSource, /No remote commands found/);
    assert.doesNotMatch(remoteCommandsSource, /failure_reason \?\? command\.client_response/);

    const salesSource = readFileSync("src/components/SalesManager.tsx", "utf8");
    const secretSelectSource = readFileSync("src/components/SecretSelect.tsx", "utf8");
    const secretSelectCss = readFileSync("src/components/SecretSelect.module.css", "utf8");

    assert.match(secretSelectSource, /export interface SecretSelectOption/);
    assert.match(secretSelectSource, /label: string/);
    assert.match(secretSelectSource, /leadingIcon\?: ReactNode/);
    assert.match(secretSelectSource, /placeholder/);
    assert.match(secretSelectSource, /disabled/);
    assert.match(secretSelectSource, /error/);
    assert.match(secretSelectSource, /loading/);
    assert.match(secretSelectSource, /onClear/);
    assert.match(secretSelectSource, /searchable/);
    assert.match(secretSelectSource, /createPortal/);
    assert.match(secretSelectSource, /role="listbox"/);
    assert.match(secretSelectSource, /role="option"/);
    assert.match(secretSelectSource, /aria-expanded/);
    assert.match(secretSelectSource, /aria-activedescendant/);
    assert.match(secretSelectSource, /ArrowDown/);
    assert.match(secretSelectSource, /ArrowUp/);
    assert.match(secretSelectSource, /Escape/);
    assert.match(secretSelectSource, /Home/);
    assert.match(secretSelectSource, /End/);
    assert.match(secretSelectSource, /No operators found/);
    assert.match(secretSelectSource, /triggerRef\.current\?\.focus/);
    assert.match(secretSelectSource, /SelectGlyph name="check"/);
    assert.match(secretSelectCss, /\.panel/);
    assert.match(secretSelectCss, /position: fixed/);
    assert.match(secretSelectCss, /z-index: 110/);
    assert.match(secretSelectCss, /\.rootActive/);
    assert.match(secretSelectCss, /\.optionSelected/);
    assert.match(secretSelectCss, /scrollbar-gutter: stable/);
    assert.match(secretSelectCss, /@keyframes selectIn/);

    assert.match(salesSource, /PageTitleIcon name="receipt"/);
    assert.match(salesSource, /SecretSelect/);
    assert.match(salesSource, /SelectGlyph name="users"/);
    assert.match(salesSource, /SelectGlyph name="credit-card"/);
    assert.match(salesSource, /SelectGlyph name="list-filter"/);
    assert.match(salesSource, /SelectGlyph name="badge"/);
    assert.match(salesSource, /searchable/);
    assert.match(salesSource, /Search operators/);
    assert.doesNotMatch(salesSource, /<select onChange=\{\(event\) => setOperatorFilter/);
    assert.doesNotMatch(salesSource, /<select onChange=\{\(event\) => setMethodFilter/);
    assert.doesNotMatch(salesSource, /<select onChange=\{\(event\) => setTypeFilter/);
    assert.doesNotMatch(salesSource, /<select onChange=\{\(event\) => setStatusFilter/);
    assert.match(salesSource, /Net Sales/);
    assert.match(salesSource, /Gross Sales/);
    assert.match(salesSource, /Refunds \/ Voids/);
    assert.match(salesSource, /Cash Received/);
    assert.match(salesSource, /Change/);
    assert.match(salesSource, /Non-Cash Collected/);
    assert.match(salesSource, /Operator Collections/);
    assert.doesNotMatch(salesSource, /Total Collections/);
    assert.match(salesSource, /Cash Accountability/);
    assert.match(salesSource, /Gross sales/);
    assert.match(salesSource, /Expected drawer cash/);
    assert.match(salesSource, /View Transactions/);
    assert.match(salesSource, /SelectGlyph name="list-filter"/);
    assert.match(salesSource, /operatorCardGridSingle/);
    assert.match(salesSource, /operatorCardGridDouble/);
    assert.match(salesSource, /operatorCardGridMultiple/);
    assert.match(salesSource, /collectionActionCell/);
    assert.match(salesSource, /collectionActionSpacer/);
    assert.match(salesSource, /collectionActionRow/);
    assert.match(salesSource, /colgroup/);
    assert.match(salesSource, /collectionOperatorColumn/);
    assert.match(salesSource, /collectionCountColumn/);
    assert.match(salesSource, /collectionMoneyColumn/);
    assert.match(salesSource, /collectionRefundsColumn/);
    assert.match(salesSource, /collectionExpectedCashColumn/);
    assert.match(salesSource, /numericCell/);
    assert.match(salesSource, /salesGroupCell/);
    assert.match(salesSource, /cashGroupCell/);
    assert.match(salesSource, /otherGroupCell/);
    assert.doesNotMatch(salesSource, /Hide Transactions/);
    assert.match(salesSource, /OperatorTransactionsModal/);
    assert.match(salesSource, /OPERATOR_TRANSACTION_PAGE_SIZE = 25/);
    assert.match(salesSource, /modalTypeFilter/);
    assert.match(salesSource, /modalMethodFilter/);
    assert.match(salesSource, /modalStatusFilter/);
    assert.match(salesSource, /searchQuery/);
    assert.match(salesSource, /SORT_FILTER_OPTIONS/);
    assert.match(salesSource, /Newest first/);
    assert.match(salesSource, /Oldest first/);
    assert.match(salesSource, /Highest amount/);
    assert.match(salesSource, /Lowest amount/);
    assert.match(salesSource, /No transactions match the selected filters/);
    assert.match(salesSource, /Previous operator transaction page/);
    assert.match(salesSource, /Next operator transaction page/);
    assert.match(salesSource, /event\.key === "Escape"/);
    assert.match(salesSource, /triggerRefs\.current\[closingKey\]\?\.focus/);
    assert.match(salesSource, /Technical details/);
    assert.match(salesSource, /salesTypeLabel/);
    assert.match(salesSource, /Not yet used on a PC/);
    assert.match(salesSource, /Technical details/);
    assert.match(salesSource, /TransactionDetailsModal/);
    assert.match(salesSource, /ModalSummaryItem/);
    assert.match(salesSource, /operatorModalSummaryItem/);
    assert.doesNotMatch(salesSource, /<MoneyTile label="Net sales"/);
    assert.match(salesSource, /statusLabel/);
    assert.match(salesSource, /compactReference/);
    assert.match(salesSource, /Cash refunds/);
    assert.match(salesSource, /Clear filters/);
    assert.match(salesSource, /Show detailed table/);
    assert.match(salesSource, /summarizeOperatorCollection/);
    assert.match(salesSource, /Expected drawer cash = cash received - change given - cash refunds paid out/);
    assert.match(salesSource, /operator_collections/);
    assert.match(salesSource, /recorded_by_operator/);
    assert.match(salesSource, /voided_by_operator/);
    assert.match(salesSource, /payment_method/);
    assert.match(salesSource, /sales_type/);
    assert.match(salesSource, /statusFilter/);
    assert.match(salesSource, /Outstanding Settlements/);
    assert.match(salesSource, /Export CSV/);
    assert.match(salesSource, /Sales by Operator/);
    assert.match(salesSource, /Sales by PC/);
    assert.match(salesSource, /No sales recorded for this period/);

    const salesCss = readFileSync("src/app/sales/sales.module.css", "utf8");
    assert.match(salesCss, /\.summaryGrid/);
    assert.match(salesCss, /repeat\(7, minmax\(0, 1fr\)\)/);
    assert.match(salesCss, /repeat\(4, minmax\(0, 1fr\)\)/);
    assert.match(salesCss, /\.collectionToolbar/);
    assert.match(salesCss, /\.collectionActionCell/);
    assert.match(salesCss, /\.collectionActionSpacer/);
    assert.match(salesCss, /\.collectionActionRow/);
    assert.match(salesCss, /min-height: 44px/);
    assert.match(salesCss, /grid-template-columns: repeat\(4, minmax\(160px, 1fr\)\)/);
    assert.match(salesCss, /grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
    assert.match(salesCss, /\.operatorCardGrid/);
    assert.match(salesCss, /\.operatorCardGridSingle/);
    assert.match(salesCss, /\.operatorCardGridDouble/);
    assert.match(salesCss, /\.operatorCardGridMultiple/);
    assert.match(salesCss, /grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
    assert.match(salesCss, /\.operatorCardGridSingle \.operatorGroups/);
    assert.match(salesCss, /\.operatorCollectionCard/);
    assert.match(salesCss, /grid-template-rows: auto 1fr auto/);
    assert.match(salesCss, /grid-template-columns: repeat\(3, minmax\(420px, 1fr\)\)/);
    assert.match(salesCss, /grid-template-columns: repeat\(2, minmax\(420px, 1fr\)\)/);
    assert.match(salesCss, /\.operatorGroups/);
    assert.match(salesCss, /\.cashAccountability/);
    assert.match(salesCss, /\.transactionReceipt/);
    assert.match(salesCss, /\.operatorModalBackdrop/);
    assert.match(salesCss, /\.operatorModalHeader/);
    assert.match(salesCss, /\.operatorModalSummaryItem/);
    assert.match(salesCss, /\.operatorModalSummaryPrimary/);
    assert.match(salesCss, /\.operatorModalFilters/);
    assert.match(salesCss, /\.operatorPagination/);
    assert.match(salesCss, /max-width: calc\(100vw - 48px\)/);
    assert.match(salesCss, /max-height: calc\(100vh - var\(--global-header-height\) - 48px\)/);
    assert.match(salesCss, /\.transactionModal/);
    assert.match(salesCss, /\.technicalDetails/);
    assert.match(salesCss, /\.technicalDetailsCompact/);
    assert.match(salesCss, /\.statusBadge/);
    assert.match(salesCss, /\.collectionTable/);
    assert.match(salesCss, /table-layout: fixed/);
    assert.match(salesCss, /min-width: 1620px/);
    assert.match(salesCss, /\.collectionOperatorColumn/);
    assert.match(salesCss, /\.collectionCountColumn/);
    assert.match(salesCss, /\.collectionRefundsColumn/);
    assert.match(salesCss, /\.collectionCashReceivedColumn/);
    assert.match(salesCss, /\.collectionExpectedCashColumn/);
    assert.match(salesCss, /\.collectionOutstandingColumn/);
    assert.doesNotMatch(salesCss, /\.collectionWideMoneyColumn/);
    assert.match(salesCss, /\.numericCell/);
    assert.match(salesCss, /\.salesGroupCell/);
    assert.match(salesCss, /\.cashGroupCell/);
    assert.match(salesCss, /\.otherGroupCell/);
    assert.match(salesCss, /font-variant-numeric: tabular-nums/);
    assert.match(salesCss, /text-align: center/);
    assert.match(salesCss, /scrollbar-gutter: stable/);
    assert.match(salesCss, /\.collectionTable tbody td:first-child/);
    assert.match(salesCss, /\.collectionTable tfoot th:first-child/);
    assert.match(salesCss, /vertical-align: middle/);
    assert.match(salesCss, /border-top: 2px solid rgba\(103, 232, 249, 0\.42\)/);
    assert.match(salesCss, /box-shadow: inset 0 0 0 1px rgba\(103, 232, 249, 0\.08\)/);
    assert.match(salesCss, /position: sticky/);
    assert.match(salesCss, /width: 100%/);
    assert.doesNotMatch(salesCss, /\.operatorDetailPanel h3/);
    assert.match(salesCss, /\.chartRow/);
    assert.match(salesCss, /@media \(max-width: 760px\)/);

    const membersSourceForRefund = readFileSync("src/components/MembersManager.tsx", "utf8");
    assert.match(membersSourceForRefund, /CloseButton/);
    assert.match(membersSourceForRefund, /Enter cash received/);
    assert.match(membersSourceForRefund, /Insufficient cash/);
    assert.match(membersSourceForRefund, /changeSummary/);
    assert.match(membersSourceForRefund, /confirmOverlay/);
    assert.match(membersSourceForRefund, /refundDialog/);
    assert.match(membersSourceForRefund, /Time to be removed/);
    assert.doesNotMatch(membersSourceForRefund, /Time to remove<\/span>/);
    assert.match(membersSourceForRefund, /refundSummary/);
    assert.match(membersSourceForRefund, /Balance after refund/);
    assert.match(membersSourceForRefund, /destructiveButton/);
    assert.match(membersSourceForRefund, /Processing\.\.\./);
    assert.match(membersSourceForRefund, /refundReason === "Other" && !refundNotes\.trim\(\)/);
    assert.match(pageCss, /\.confirmOverlay/);
    assert.match(pageCss, /\.refundDialog/);
    assert.match(pageCss, /\.refundSummaryRowHighlight/);

    const paymentDialogSource = readFileSync("src/components/PaymentDialog.tsx", "utf8");
    assert.match(paymentDialogSource, /Enter cash received/);
    assert.match(paymentDialogSource, /Insufficient cash/);
    assert.match(paymentDialogSource, /changeSummary/);
    assert.doesNotMatch(paymentDialogSource, /Enter enough cash/);

    const sessionDialogSource = readFileSync("src/components/SessionDialog.tsx", "utf8");
    assert.match(sessionDialogSource, /Enter cash received/);
    assert.match(sessionDialogSource, /Insufficient cash/);
    assert.match(sessionDialogSource, /changeSummary/);

    for (const sourcePath of [
      "src/components/ActivityFeed.tsx",
      "src/components/HistoryTable.tsx",
      "src/components/OperatorActivity.tsx",
      "src/components/OperatorsManager.tsx",
      "src/components/PcDetailsPanel.tsx"
    ]) {
      assert.match(readFileSync(sourcePath, "utf8"), /CloseButton/);
    }

    const pageTitleSources: Array<[string, RegExp]> = [
      ["src/app/history/page.tsx", /PageTitleIcon name="history"/],
      ["src/app/operator-activity/page.tsx", /PageTitleIcon name="activity"/],
      ["src/app/rates/page.tsx", /PageTitleIcon name="rates"/],
      ["src/app/remote-commands/page.tsx", /PageTitleIcon name="terminal"/],
      ["src/app/settings/page.tsx", /PageTitleIcon name="settings"/],
      ["src/app/system/page.tsx", /PageTitleIcon name="monitor-cog"/],
      ["src/app/users/page.tsx", /PageTitleIcon name="users"/],
      ["src/components/MembersManager.tsx", /PageTitleIcon name="members"/],
      ["src/components/OperatorsManager.tsx", /PageTitleIcon name="shield"/],
      ["src/components/ShiftsManager.tsx", /PageTitleIcon name="receipt"/]
    ];
    for (const [sourcePath, expectedIcon] of pageTitleSources) {
      const source = readFileSync(sourcePath, "utf8");
      assert.match(source, /pageTitleLockup/);
      assert.match(source, expectedIcon);
    }

    const operatorConfigSource = readFileSync("../server/operator_config.py", "utf8");
    for (const name of ["Mama", "Neil", "Tyrone", "Rogene", "Rojan", "Bel", "Karen", "Admin"]) {
      assert.match(operatorConfigSource, new RegExp(name));
    }

    // --- Stage 5: dashboard Standard/VIP pricing UX polish ---------------

    // PC cards/tiles visibly identify Standard vs VIP, and operational
    // status (customer lock, recovery, offline, etc.) is unaffected by
    // station type -- station badges are purely decorative overlays.
    const pcCardSource = readFileSync("src/components/PcCard.tsx", "utf8");
    assert.match(pcCardSource, /standardBadge/);
    assert.match(pcCardSource, />Standard</);
    assert.match(pcCardSource, />VIP</);
    const pcMonitorTileSourceForStage5 = readFileSync("src/components/PcMonitorTile.tsx", "utf8");
    assert.match(pcMonitorTileSourceForStage5, /monitorStandardBadge/);
    assert.match(pcMonitorTileSourceForStage5, /monitorVipBadge/);
    // getPcMonitorPresentation and getPcCardTone never take is_vip as input
    // at all -- proving station type structurally cannot influence which
    // operational status badge/tone is shown, VIP or Standard.
    const pcStatusSourceForStage5 = readFileSync("src/lib/pcStatus.ts", "utf8");
    assert.doesNotMatch(pcStatusSourceForStage5, /pc\.is_vip/);
    const vipCustomerLockedPc = makePc({
      is_vip: true,
      session_status: "active",
      session_mode: "member_balance",
      active_session_id: "session-vip-locked",
      remaining_seconds: 600,
      customer_locked: true
    });
    assert.deepEqual(
      getPcMonitorPresentation(vipCustomerLockedPc),
      { className: "monitorTileLocked", label: "Customer Locked" },
      "Customer Locked must take priority over VIP station classification"
    );
    assert.equal(getPcCardTone(vipCustomerLockedPc), "active");

    // Setup/Layout Computer Type control (item 2) actually mutates
    // pc_layouts.is_vip through the existing PUT endpoint, with the
    // required explanatory copy.
    assert.match(dashboardSource, /changeStationType/);
    assert.match(dashboardSource, /Computer Type/);
    assert.match(
      dashboardSource,
      /This determines which rates and member-balance rules apply to this station\./
    );
    assert.match(dashboardSource, /computerTypeToggle/);

    // Start Session: Midnight Promo card labeling is data-driven (derived
    // from the rate's own name/eligibility window), never hardcoded, and
    // shows the eligibility window + guest-only note.
    assert.match(sessionDialogSourceForOpenTimeRetirement, /ratePromoLabel/);
    assert.match(sessionDialogSourceForOpenTimeRetirement, /ratePromoWindow/);
    assert.match(sessionDialogSourceForOpenTimeRetirement, /Guest only/);
    assert.match(sessionDialogSourceForOpenTimeRetirement, /formatClockTime/);
    // Member tab clearly separates Standard Time / VIP Time balances and
    // never phrases the Standard->VIP conversion as a raw multiplier.
    assert.match(sessionDialogSourceForOpenTimeRetirement, /memberBalanceGrid/);
    assert.match(sessionDialogSourceForOpenTimeRetirement, />Standard Time</);
    assert.match(sessionDialogSourceForOpenTimeRetirement, />VIP Time</);
    assert.match(sessionDialogSourceForOpenTimeRetirement, /contributes/);
    assert.doesNotMatch(sessionDialogSourceForOpenTimeRetirement, /multiplier/i);

    // Active VIP member session: balance-source projection is fetched from
    // the server (never recomputed client-side), and both the "still on
    // VIP" and "crossed into Standard" states are rendered distinctly --
    // this is exactly what makes the VIP->Standard transition mid-session
    // show up correctly as elapsed time crosses the boundary.
    const pcDetailsSourceForStage5 = readFileSync("src/components/PcDetailsPanel.tsx", "utf8");
    assert.match(pcDetailsSourceForStage5, /fetchSessionBalanceSource/);
    assert.match(pcDetailsSourceForStage5, /Using VIP Time/);
    assert.match(pcDetailsSourceForStage5, /Using Standard Time/);
    assert.match(pcDetailsSourceForStage5, /Standard time is being used at the VIP conversion rate\./);
    assert.match(pcDetailsSourceForStage5, /standard_reserved_vip_equivalent_seconds/);
    assert.doesNotMatch(pcDetailsSourceForStage5, /STANDARD_SECONDS_PER_VIP_SECOND/);
    assert.doesNotMatch(pcDetailsSourceForStage5, /standard_reserved_seconds\s*\/\s*2/);
    assert.doesNotMatch(pcDetailsSourceForStage5, /Math\.floor\([^)]*standard[^)]*\/\s*2\)/i);
    // Customer Locked stays visually prioritized in the details panel too --
    // the VIP balance-status section is additive, not a replacement.
    assert.match(
      pcDetailsSourceForStage5,
      /The member has locked this station; their session and timer continue/
    );

    // The projection endpoint is a pure GET -- polling it can never mutate
    // session or balance state client- or server-side.
    const apiSourceForStage5 = readFileSync("src/lib/api.ts", "utf8");
    assert.match(
      apiSourceForStage5,
      /fetchSessionBalanceSource[\s\S]{0,200}cache: "no-store"/
    );
    assert.doesNotMatch(
      apiSourceForStage5.slice(
        apiSourceForStage5.indexOf("export async function fetchSessionBalanceSource"),
        apiSourceForStage5.indexOf("export async function fetchSessionBalanceSource") + 320
      ),
      /method: "(POST|PUT|PATCH|DELETE)"/
    );
    const mainPySourceForStage5 = readFileSync("../server/main.py", "utf8");
    assert.match(mainPySourceForStage5, /@app\.get\("\/sessions\/\{session_id\}\/balance-source"/);

    // Members page: VIP promo product is explicitly marked VIP-only and
    // non-refundable directly on the product card, not just in fine print.
    const membersSourceForStage5 = readFileSync("src/components/MembersManager.tsx", "utf8");
    assert.match(membersSourceForStage5, /VIP Only/);
    assert.match(membersSourceForStage5, /Non-Refundable/);

    // Rates page: station scope and Midnight-style eligibility windows are
    // visually distinguishable, deprecated metered/Open Time UI stays gone.
    const ratesManagerSourceForStage5 = readFileSync("src/components/RatesManager.tsx", "utf8");
    assert.match(ratesManagerSourceForStage5, /stationScopeBadgeClassName/);
    assert.match(ratesManagerSourceForStage5, /promoPill/);

    // Session history: VIP breakdown is read from the persisted Stage 4
    // session snapshot fields only -- never recomputed from the member's
    // current wallet -- while a Standard history row stays uncluttered and
    // historical Open Time rows still render through their original path.
    const historyTableSourceForStage5 = readFileSync("src/components/HistoryTable.tsx", "utf8");
    assert.match(historyTableSourceForStage5, /vipHistoryBadge/);
    assert.match(historyTableSourceForStage5, /station_type === "vip"/);
    assert.match(historyTableSourceForStage5, /vip_seconds_consumed/);
    assert.match(historyTableSourceForStage5, /standard_seconds_consumed_at_vip_rate/);
    assert.match(historyTableSourceForStage5, /conversion_applied/);
    assert.match(historyTableSourceForStage5, /VIP Time Used/);
    assert.match(historyTableSourceForStage5, /Standard Time Used/);
    assert.doesNotMatch(historyTableSourceForStage5, /vip_balance_seconds/);
    assert.match(historyTableSourceForStage5, /"Open Time"/);
    assert.match(historyTableSourceForStage5, /session_mode === "open_time"/);

    // Sales history: Standard top-ups and VIP promo purchases are visually
    // distinguishable, VIP purchases flagged non-refundable, and this is
    // labeling only -- underlying accounting totals are untouched.
    const salesManagerSourceForStage5 = readFileSync("src/components/SalesManager.tsx", "utf8");
    assert.match(salesManagerSourceForStage5, /salesTypeLabel/);
    assert.match(salesManagerSourceForStage5, /"VIP Promo"/);
    assert.match(salesManagerSourceForStage5, /"Standard Top-up"/);
    assert.match(salesManagerSourceForStage5, /vipPurchaseBadge/);

    // Income by Group section (Stage 6): fully data-driven off
    // report.income_groups/pending_attribution_* -- no group name is
    // hardcoded, so any seeded, custom, or legacy group the backend
    // returns renders identically through the same BreakdownPanel/
    // formatMoney path every other breakdown already uses.
    const salesManagerSourceForStage6 = readFileSync("src/components/SalesManager.tsx", "utf8");
    assert.match(salesManagerSourceForStage6, /Income by Group/);
    assert.match(salesManagerSourceForStage6, /Pending Attribution/);
    assert.match(salesManagerSourceForStage6, /Payments awaiting automatic income-group assignment\./);
    assert.match(salesManagerSourceForStage6, /report\.income_groups\.map/);
    assert.match(salesManagerSourceForStage6, /group\.income_group_name/);
    assert.match(salesManagerSourceForStage6, /group\.amount/);
    assert.match(salesManagerSourceForStage6, /group\.payment_count/);
    assert.match(salesManagerSourceForStage6, /report\.pending_attribution_amount/);
    assert.match(salesManagerSourceForStage6, /report\.pending_attribution_count/);
    assert.match(salesManagerSourceForStage6, /description\?: string/);
    assert.match(salesManagerSourceForStage6, /hint\?: string/);
    assert.match(salesManagerSourceForStage6, /formatMoney\(row\.amount\)/);
    // Existing Sales sections must remain present, untouched by the new panel.
    assert.match(salesManagerSourceForStage6, /Sales by Operator/);
    assert.match(salesManagerSourceForStage6, /Sales by PC/);
    assert.match(salesManagerSourceForStage6, /Payment Methods/);
    assert.match(salesManagerSourceForStage6, /Sales by Type/);

    // Stage 7 removed Today's Sales only from the MAIN dashboard summary
    // card -- the dedicated Sales page's own Today period/date-range
    // reporting (Net Sales, Gross Sales, etc.) is untouched.
    assert.match(salesManagerSourceForStage6, /\{ label: "Today", value: "today" \}/);
    assert.match(salesManagerSourceForStage6, /Net Sales/);
    assert.match(salesManagerSourceForStage6, /Gross Sales/);

    const typesSourceForStage6 = readFileSync("src/lib/types.ts", "utf8");
    assert.match(typesSourceForStage6, /export interface SalesIncomeGroupBreakdown/);
    assert.match(typesSourceForStage6, /income_group_id: string \| null/);
    assert.match(typesSourceForStage6, /pending_attribution_amount: string/);
    assert.match(typesSourceForStage6, /pending_attribution_count: number/);

    const loggedErrors: unknown[] = [];
    console.error = (...args: unknown[]) => {
      loggedErrors.push(args);
    };
    globalThis.fetch = async () => {
      throw new TypeError("Failed to fetch");
    };
    await assert.rejects(
      () => fetchRates(),
      /Could not reach the Secret Grounds server/
    );
    assert.equal(loggedErrors.length, 1);
  } finally {
    console.error = originalConsoleError;
    globalThis.fetch = originalFetch;
    if (originalBaseUrl === undefined) {
      delete process.env.NEXT_PUBLIC_FASTAPI_BASE_URL;
    } else {
      process.env.NEXT_PUBLIC_FASTAPI_BASE_URL = originalBaseUrl;
    }
    Object.defineProperty(process.env, "NODE_ENV", { configurable: true, enumerable: true, writable: true, value: originalNodeEnv });
  }
}

runAsyncAssertions()
  .then(() => {
    console.log("Dashboard utility tests passed.");
  })
  .catch((error: unknown) => {
    console.error(error);
    process.exitCode = 1;
  });

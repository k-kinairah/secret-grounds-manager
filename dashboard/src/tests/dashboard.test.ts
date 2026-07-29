import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  addTime,
  adminOperatorLogin,
  calculateTime,
  createUser,
  createRate,
  fetchAdminSettings,
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
  fetchUsers,
  forceEndSession,
  getApiBaseUrl,
  getSalesExportUrl,
  getSystemBackupDownloadUrl,
  login,
  logout,
  quickOperatorLogin,
  recordPayment,
  resetMemberPin,
  resetAdminSettings,
  resetUserPassword,
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
import { saveRateProfile } from "../lib/rateActions";
import { rateFormToInput, rateToForm } from "../lib/rateForm";
import { calculatePreviewSeconds } from "../lib/sessionSelection";
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
  canViewDetails: true
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
  created_at: "2026-07-23T00:00:00+00:00",
  updated_at: "2026-07-23T00:00:00+00:00"
};

assert.deepEqual(rateToForm(sampleRate), {
  name: "30 Minutes",
  minutes: "30",
  price: "50.00",
  rateType: "package",
  sortOrder: "10"
});
assert.deepEqual(
  rateFormToInput({
    name: "  Night Pack ",
    minutes: "90",
    price: "75.25",
    rateType: "package",
    sortOrder: "12"
  }),
  {
    name: "Night Pack",
    duration_seconds: 5400,
    price: "75.25",
    rate_type: "package",
    sort_order: 12
  }
);

assert.equal(calculatePreviewSeconds("20.00", {
  ...sampleRate,
  rate_id: "rate-metered",
  name: "Regular Rate",
  duration_seconds: 300,
  price: "1.00",
  rate_type: "metered",
  sort_order: 0
}), 6000);
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
    await resetMemberPin("member-1", "1234");
    await fetchRates(true);
    await calculateTime("20.00");
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
      memberId: "member-1",
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
        { method: "POST", url: "http://localhost:8000/rates/calculate-time" },
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
        { method: "GET", url: "http://localhost:8000/activity?limit=60" }
      ]
    );
    assert.equal(fetchCalls.every((call) => call.credentials === "include"), true);
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
      pin: "1234"
    });
    assert.equal((fetchCalls[9].body as { general: { shop_display_name: string } }).general.shop_display_name, "Secret Grounds");
    assert.deepEqual(fetchCalls[10].body, {
      category: "general"
    });
    assert.equal((fetchCalls[20].body as RateProfileInput).price, "12.30");
    assert.equal((fetchCalls[21].body as RateProfileInput).price, "50.00");
    assert.equal((fetchCalls[21].body as RateProfileInput).duration_seconds, 1800);
    assert.deepEqual(fetchCalls[23].body, {
      display_name: "PC 3",
      area_label: "SECRET GROUND SHOP",
      layout_order: 30,
      is_vip: false,
      is_enabled: false,
      mac_address: null
    });
    assert.deepEqual(fetchCalls[24].body, {
      pc_name: "PC-01",
      member_id: "member-1",
      duration_seconds: 3600,
      member_pin: "1234"
    });
    assert.deepEqual(fetchCalls[25].body, {
      pc_name: "PC-01",
      amount: "20.00"
    });
    assert.deepEqual(fetchCalls[26].body, {
      title: "Secret Grounds",
      message: "Please return to the cashier.",
      severity: "info",
      require_acknowledgement: false
    });
    assert.deepEqual(fetchCalls[27].body, {});
    assert.deepEqual(fetchCalls[28].body, {
      amount: "5.00"
    });
    assert.deepEqual(fetchCalls[29].body, null);
    assert.deepEqual(fetchCalls[30].body, {
      payment_method: "cash",
      amount: "20.00",
      cash_received: "50.00"
    });
    assert.deepEqual(fetchCalls[35].body, {
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
        sortOrder: "10"
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
        sortOrder: "40"
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
    assert.match(ratesCss, /\.meteredCard/);
    assert.match(ratesCss, /color: #f8fafc/);

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
    assert.match(dashboardSource, /fetchDashboardTodaySales/);
    assert.match(dashboardSource, /displayAreaLabel/);
    assert.match(dashboardSource, /SECRET GROUNDS SHOP/);
    assert.match(dashboardSource, /Select a PC/);
    assert.match(dashboardSource, /Choose a station to view session, billing, messaging, and power controls\./);
    assert.match(dashboardSource, /Register First PC/);
    assert.match(dashboardSource, /variant="dock"/);

    const membersSource = readFileSync("src/components/MembersManager.tsx", "utf8");
    assert.match(membersSource, /Search by code, name, or phone/);
    assert.match(membersSource, /setIsCreateOpen\(true\)/);
    assert.match(membersSource, /role="dialog"/);
    assert.match(membersSource, /Create Member/);
    assert.match(membersSource, /View Member/);
    assert.match(membersSource, /No members yet/);
    assert.match(membersSource, /No members found/);
    assert.match(membersSource, /No phone/);
    assert.match(membersSource, /PIN enabled/);
    assert.match(membersSource, /PIN not set/);
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
    assert.match(membersCss, /\.memberTable/);
    assert.match(membersCss, /\.memberModal/);
    assert.match(membersCss, /\.memberDetailModal/);
    assert.match(membersCss, /\.memberDialogMessage/);
    assert.match(membersCss, /\.memberMoreButton/);
    assert.match(membersCss, /\.memberFloatingMenu/);
    assert.match(membersCss, /position: fixed/);
    assert.match(membersCss, /z-index: 80/);
    assert.match(membersCss, /@media \(max-width: 760px\)/);
    assert.match(membersCss, /content: attr\(data-label\)/);

    const authShellSource = readFileSync("src/components/AuthShell.tsx", "utf8");
    assert.match(authShellSource, /isAdminOperator/);
    assert.match(authShellSource, /appShell__adminMenu/);
    assert.match(authShellSource, /ToastProvider/);
    assert.match(authShellSource, /Admin/);
    assert.match(authShellSource, /Operator Activity/);
    assert.match(authShellSource, /Staff/);
    assert.match(authShellSource, /Sales/);
    assert.match(authShellSource, /Settings/);
    assert.match(authShellSource, /System/);
    assert.match(authShellSource, /href: "\/settings"/);
    assert.match(authShellSource, /href: "\/system"/);
    assert.match(authShellSource, /href="\/rates"/);
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
    assert.match(toastSource, /success/);
    assert.match(toastSource, /warning/);
    assert.match(toastSource, /error/);

    const statusCardSource = readFileSync("src/components/StatusCard.tsx", "utf8");
    assert.match(statusCardSource, /statusCardIcon/);
    assert.match(statusCardSource, /memo/);

    const dashboardClientSource = readFileSync("src/components/DashboardClient.tsx", "utf8");
    assert.match(dashboardClientSource, /ActivityDrawerTrigger/);
    assert.doesNotMatch(dashboardClientSource, /<ActivityFeed/);
    assert.match(dashboardClientSource, /pushToast/);
    assert.ok(
      dashboardClientSource.indexOf("<DashboardSummary") < dashboardClientSource.indexOf("dashboardWorkspace")
    );

    const pageCss = readFileSync("src/app/page.module.css", "utf8");
    assert.match(pageCss, /\.statusCard/);
    assert.match(pageCss, /\.activityPanel/);
    assert.match(pageCss, /\.activityTrigger/);
    assert.match(pageCss, /\.activityDrawer/);
    assert.match(pageCss, /\.activityDrawerBackdrop/);
    assert.match(pageCss, /@media \(max-width: 520px\)/);
    assert.match(pageCss, /@keyframes activityIn/);
    assert.match(pageCss, /@keyframes drawerSlideIn/);

    const globalsSource = readFileSync("src/app/globals.css", "utf8");
    assert.match(globalsSource, /--radius-card/);
    assert.match(globalsSource, /\.toastStack/);
    assert.match(globalsSource, /@keyframes toastIn/);
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
    assert.match(salesSource, /Total Sales/);
    assert.match(salesSource, /Outstanding Settlements/);
    assert.match(salesSource, /Export CSV/);
    assert.match(salesSource, /Sales by Operator/);
    assert.match(salesSource, /Sales by PC/);
    assert.match(salesSource, /No sales recorded for this period/);

    const salesCss = readFileSync("src/app/sales/sales.module.css", "utf8");
    assert.match(salesCss, /\.summaryGrid/);
    assert.match(salesCss, /\.chartRow/);
    assert.match(salesCss, /@media \(max-width: 760px\)/);

    const operatorConfigSource = readFileSync("../server/operator_config.py", "utf8");
    for (const name of ["Mama", "Neil", "Tyrone", "Rogene", "Rojan", "Bel", "Karen", "Admin"]) {
      assert.match(operatorConfigSource, new RegExp(name));
    }

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

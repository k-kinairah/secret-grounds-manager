import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  addTime,
  calculateTime,
  createUser,
  createRate,
  fetchCurrentUser,
  fetchRates,
  fetchUsers,
  forceEndSession,
  getApiBaseUrl,
  login,
  logout,
  recordPayment,
  resetUserPassword,
  startSession,
  toCashierMessage,
  updateRate,
  updateRateStatus,
  updateUser,
  updateUserStatus
} from "../lib/api";
import { calculateCashChange, isPaymentInputValid } from "../lib/paymentSelection";
import { getPcActionAvailability, getPcCardTone, summarizePcs } from "../lib/pcStatus";
import { saveRateProfile } from "../lib/rateActions";
import { rateFormToInput, rateToForm } from "../lib/rateForm";
import { calculatePreviewSeconds } from "../lib/sessionSelection";
import {
  calculateOpenTimeEstimate,
  formatDuration,
  formatMoney,
  formatRateDuration,
  formatTimer,
  minutesToSeconds,
  secondsFromStartedAt
} from "../lib/time";
import type { PcSummary, RateProfile, RateProfileInput } from "../lib/types";

function makePc(overrides: Partial<PcSummary> = {}): PcSummary {
  return {
    pc_name: "PC-01",
    ip_address: "127.0.0.1",
    connection_status: "online",
    last_seen: "2026-07-23T00:00:00+00:00",
    session_status: "idle",
    active_session_id: null,
    started_at: null,
    scheduled_end_at: null,
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
assert.equal(formatMoney("12.30"), "₱12.30");
assert.equal(formatMoney("0.00"), "₱0.00");

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

const offlinePc = makePc({ connection_status: "offline" });
assert.equal(getPcCardTone(offlinePc), "offline");
assert.deepEqual(getPcActionAvailability(offlinePc), {
  canStart: false,
  canAddTime: false,
  canEnd: false,
  canCollectPayment: false,
  canViewDetails: true
});

const activePc = makePc({
  session_status: "active",
  active_session_id: "session-1",
  remaining_seconds: 60
});
assert.equal(getPcCardTone(activePc), "active");
assert.equal(getPcActionAvailability(activePc).canAddTime, true);
assert.equal(getPcActionAvailability(activePc).canEnd, true);

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

const availablePc = makePc();
assert.equal(getPcCardTone(availablePc), "available");
assert.equal(getPcActionAvailability(availablePc).canStart, true);

assert.deepEqual(summarizePcs([availablePc, activePc, offlinePc]), {
  total: 3,
  inUse: 1,
  available: 1,
  offline: 1
});

assert.equal(
  toCashierMessage("PC-01 is offline"),
  "This PC is offline. Session actions are disabled until it reconnects."
);
assert.equal(
  toCashierMessage("Start session command failed: timeout"),
  "The client did not complete the command. Check the PC connection and try again."
);

const renderedRate = {
  name: "1 Hour",
  duration: formatRateDuration(3600),
  price: formatMoney("100.10")
};
assert.deepEqual(renderedRate, {
  name: "1 Hour",
  duration: "1 hr",
  price: "₱100.10"
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
const originalFetch = globalThis.fetch;
const originalConsoleError = console.error;
process.env.NEXT_PUBLIC_FASTAPI_BASE_URL = "http://127.0.0.1:8000/";
assert.equal(getApiBaseUrl(), "http://127.0.0.1:8000");

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
    await fetchCurrentUser();
    await fetchUsers();
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
    await startSession("PC-01", { amount: "20.00" });
    await addTime("session-1", { amount: "5.00" });
    await forceEndSession("session-1");
    await recordPayment("session-1", {
      payment_method: "cash",
      amount: "20.00",
      cash_received: "50.00"
    });

    assert.deepEqual(
      fetchCalls.map((call) => ({ method: call.method, url: call.url })),
      [
        { method: "POST", url: "http://127.0.0.1:8000/auth/login" },
        { method: "GET", url: "http://127.0.0.1:8000/auth/me" },
        { method: "GET", url: "http://127.0.0.1:8000/users" },
        { method: "POST", url: "http://127.0.0.1:8000/users" },
        { method: "PUT", url: "http://127.0.0.1:8000/users/user-1" },
        { method: "PATCH", url: "http://127.0.0.1:8000/users/user-1/status" },
        { method: "POST", url: "http://127.0.0.1:8000/users/user-1/reset-password" },
        { method: "POST", url: "http://127.0.0.1:8000/auth/logout" },
        { method: "GET", url: "http://127.0.0.1:8000/rates?include_inactive=true" },
        { method: "POST", url: "http://127.0.0.1:8000/rates/calculate-time" },
        { method: "POST", url: "http://127.0.0.1:8000/rates" },
        { method: "PUT", url: "http://127.0.0.1:8000/rates/rate-30" },
        { method: "PATCH", url: "http://127.0.0.1:8000/rates/rate-30/status" },
        { method: "POST", url: "http://127.0.0.1:8000/sessions/start" },
        { method: "POST", url: "http://127.0.0.1:8000/sessions/session-1/add-time" },
        { method: "POST", url: "http://127.0.0.1:8000/sessions/session-1/force-end" },
        { method: "POST", url: "http://127.0.0.1:8000/sessions/session-1/payments" }
      ]
    );
    assert.equal(fetchCalls.every((call) => call.credentials === "include"), true);
    assert.deepEqual(fetchCalls[0].body, {
      username: "owner",
      password: "password123"
    });
    assert.deepEqual(fetchCalls[6].body, {
      password: "new-password"
    });
    assert.equal((fetchCalls[11].body as RateProfileInput).price, "50.00");
    assert.equal((fetchCalls[11].body as RateProfileInput).duration_seconds, 1800);
    assert.deepEqual(fetchCalls[13].body, {
      pc_name: "PC-01",
      amount: "20.00"
    });
    assert.deepEqual(fetchCalls[14].body, {
      amount: "5.00"
    });
    assert.deepEqual(fetchCalls[15].body, null);
    assert.deepEqual(fetchCalls[16].body, {
      payment_method: "cash",
      amount: "20.00",
      cash_received: "50.00"
    });

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

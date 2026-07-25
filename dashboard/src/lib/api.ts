import type {
  BillingSummary,
  CurrentUser,
  ForceEndSessionResponse,
  PcSummary,
  CalculatedTimeResponse,
  PaymentInput,
  PrepaidSessionSelection,
  UserAdminRecord,
  UserCreateInput,
  UserUpdateInput,
  RateProfile,
  RateProfileInput,
  SessionCommandResponse,
  SessionRecord
} from "./types";

const DEFAULT_BASE_URL = "http://127.0.0.1:8000";

export function getApiBaseUrl(): string {
  return (process.env.NEXT_PUBLIC_FASTAPI_BASE_URL ?? DEFAULT_BASE_URL).replace(/\/$/, "");
}

export function getPollingIntervalMs(): number {
  const rawValue = Number(process.env.NEXT_PUBLIC_POLL_INTERVAL_MS ?? "4000");
  return Number.isFinite(rawValue) && rawValue >= 3000 ? rawValue : 4000;
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `Server returned ${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") {
        message = payload.detail;
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

export async function fetchUsers(): Promise<UserAdminRecord[]> {
  return requestJson<UserAdminRecord[]>(`${getApiBaseUrl()}/users`, {
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

  if (lowerMessage.includes("offline")) {
    return "This PC is offline. Session actions are disabled until it reconnects.";
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

export async function fetchMeteredRate(): Promise<RateProfile> {
  return requestJson<RateProfile>(`${getApiBaseUrl()}/rates/metered`, {
    cache: "no-store"
  });
}

export async function calculateTime(amount: string): Promise<CalculatedTimeResponse> {
  return requestJson<CalculatedTimeResponse>(`${getApiBaseUrl()}/rates/calculate-time`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ amount })
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

export async function startOpenTimeSession(pcName: string): Promise<SessionCommandResponse> {
  return requestJson<SessionCommandResponse>(`${getApiBaseUrl()}/sessions/start-open-time`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pc_name: pcName })
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

export async function endSession(sessionId: string): Promise<SessionCommandResponse> {
  return requestJson<SessionCommandResponse>(
    `${getApiBaseUrl()}/sessions/${sessionId}/end`,
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

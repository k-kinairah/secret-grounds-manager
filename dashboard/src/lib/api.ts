import type {
  PcSummary,
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
    throw new Error(toCashierMessage(message));
  }

  return response.json() as Promise<T>;
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
  return parseResponse<PcSummary[]>(await fetch(`${getApiBaseUrl()}/pcs`, { cache: "no-store" }));
}

export async function fetchHistory(): Promise<SessionRecord[]> {
  return parseResponse<SessionRecord[]>(
    await fetch(`${getApiBaseUrl()}/sessions/history`, { cache: "no-store" })
  );
}

export async function fetchRates(includeInactive = false): Promise<RateProfile[]> {
  const query = includeInactive ? "?include_inactive=true" : "";
  return parseResponse<RateProfile[]>(
    await fetch(`${getApiBaseUrl()}/rates${query}`, { cache: "no-store" })
  );
}

export async function startSession(
  pcName: string,
  payload: { durationSeconds?: number; rateId?: string }
): Promise<SessionCommandResponse> {
  return parseResponse<SessionCommandResponse>(
    await fetch(`${getApiBaseUrl()}/sessions/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pc_name: pcName,
        duration_seconds: payload.durationSeconds,
        rate_id: payload.rateId
      })
    })
  );
}

export async function addTime(
  sessionId: string,
  payload: { seconds?: number; rateId?: string }
): Promise<SessionCommandResponse> {
  return parseResponse<SessionCommandResponse>(
    await fetch(`${getApiBaseUrl()}/sessions/${sessionId}/add-time`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ seconds: payload.seconds, rate_id: payload.rateId })
    })
  );
}

export async function endSession(sessionId: string): Promise<SessionCommandResponse> {
  return parseResponse<SessionCommandResponse>(
    await fetch(`${getApiBaseUrl()}/sessions/${sessionId}/end`, {
      method: "POST"
    })
  );
}

export async function createRate(input: RateProfileInput): Promise<RateProfile> {
  return parseResponse<RateProfile>(
    await fetch(`${getApiBaseUrl()}/rates`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    })
  );
}

export async function updateRate(
  rateId: string,
  input: RateProfileInput
): Promise<RateProfile> {
  return parseResponse<RateProfile>(
    await fetch(`${getApiBaseUrl()}/rates/${rateId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    })
  );
}

export async function updateRateStatus(
  rateId: string,
  isActive: boolean
): Promise<RateProfile> {
  return parseResponse<RateProfile>(
    await fetch(`${getApiBaseUrl()}/rates/${rateId}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_active: isActive })
    })
  );
}

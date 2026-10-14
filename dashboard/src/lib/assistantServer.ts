// Voice assistant (phase 2) -- server-only proxy logic for /api/assistant/*.
//
// The browser never talks to the agent service. This route runs inside the
// dashboard's own Node server, checks the caller's dashboard login against the
// backend (GET /auth/me with their sg_session cookie), scopes the conversation
// to that user, and only then forwards to the agent on 127.0.0.1. So the agent
// stays loopback-only, and only signed-in staff can reach it.

import { NextResponse, type NextRequest } from "next/server";
import { getApiBaseUrl } from "./api";
import { CONVERSATION_ID_PATTERN, MAX_MESSAGE_LENGTH, isAssistantEnabled } from "./assistant";

const AUTH_COOKIE_NAME = "sg_session";
const DEFAULT_AGENT_URL = "http://127.0.0.1:8781";
const AGENT_TIMEOUT_MS = 90_000;
const AUTH_TIMEOUT_MS = 10_000;

type ProxyBody = { conversation_id?: unknown; message?: unknown };
type BackendUser = { user_id: string };

function json(status: number, body: Record<string, unknown>) {
  return NextResponse.json(body, { headers: { "Cache-Control": "no-store" }, status });
}

function errorReply(status: number, reply: string, error: string) {
  return json(status, { error, reply, tools_used: [] });
}

function agentBaseUrl(): string {
  return (process.env.ASSISTANT_AGENT_URL || DEFAULT_AGENT_URL).replace(/\/$/, "");
}

// Server-side "localhost" can resolve to ::1 while the backend listens on
// 127.0.0.1 only, so prefer the IPv4 loopback for the same host and port.
function backendBaseUrl(): string {
  const configured = process.env.ASSISTANT_BACKEND_URL || getApiBaseUrl();
  try {
    const url = new URL(configured);
    if (url.hostname === "localhost") {
      url.hostname = "127.0.0.1";
    }
    return url.toString().replace(/\/$/, "");
  } catch {
    return configured.replace(/\/$/, "");
  }
}

function isSameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  if (!origin) {
    return true; // same-origin fetches may omit it; the session check still applies
  }
  try {
    return new URL(origin).host === request.headers.get("host");
  } catch {
    return false;
  }
}

async function currentUser(request: NextRequest): Promise<BackendUser | null> {
  const session = request.cookies.get(AUTH_COOKIE_NAME)?.value;
  if (!session) {
    return null;
  }
  try {
    const response = await fetch(`${backendBaseUrl()}/auth/me`, {
      cache: "no-store",
      headers: { Cookie: `${AUTH_COOKIE_NAME}=${session}` },
      signal: AbortSignal.timeout(AUTH_TIMEOUT_MS)
    });
    if (!response.ok) {
      return null;
    }
    const user = (await response.json()) as Partial<BackendUser>;
    return typeof user.user_id === "string" ? { user_id: user.user_id } : null;
  } catch {
    return null;
  }
}

async function guard(request: NextRequest): Promise<
  { ok: true; user: BackendUser; body: ProxyBody } | { ok: false; response: NextResponse }
> {
  if (!isAssistantEnabled()) {
    return { ok: false, response: json(404, { detail: "Not found" }) };
  }
  if (!isSameOrigin(request)) {
    return { ok: false, response: errorReply(403, "That request was blocked.", "Cross-origin request rejected.") };
  }
  const user = await currentUser(request);
  if (user === null) {
    return {
      ok: false,
      response: errorReply(401, "Please sign in to the dashboard first.", "Not signed in to the dashboard.")
    };
  }
  let body: ProxyBody;
  try {
    body = (await request.json()) as ProxyBody;
  } catch {
    return { ok: false, response: errorReply(400, "That request was invalid.", "Body must be JSON.") };
  }
  if (typeof body.conversation_id !== "string" || !CONVERSATION_ID_PATTERN.test(body.conversation_id)) {
    return { ok: false, response: errorReply(400, "That request was invalid.", "Invalid conversation_id.") };
  }
  return { ok: true, body, user };
}

function scopedSessionId(user: BackendUser, conversationId: string): string {
  return `${user.user_id}:${conversationId}`;
}

async function callAgent(path: string, payload: Record<string, unknown>): Promise<Response> {
  return fetch(`${agentBaseUrl()}${path}`, {
    body: JSON.stringify(payload),
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    method: "POST",
    signal: AbortSignal.timeout(AGENT_TIMEOUT_MS)
  });
}

export async function proxyChat(request: NextRequest): Promise<NextResponse> {
  const checked = await guard(request);
  if (!checked.ok) {
    return checked.response;
  }
  const message = typeof checked.body.message === "string" ? checked.body.message.trim() : "";
  if (!message || message.length > MAX_MESSAGE_LENGTH) {
    return errorReply(400, "I didn't get a question.", "Message is empty or too long.");
  }

  let agentResponse: Response;
  try {
    agentResponse = await callAgent("/chat", {
      message,
      session_id: scopedSessionId(checked.user, checked.body.conversation_id as string)
    });
  } catch (error) {
    const timedOut = error instanceof Error && error.name === "TimeoutError";
    return errorReply(
      502,
      timedOut ? "Sorry, that took too long. Please try again." : "The assistant service isn't running right now.",
      timedOut ? "The agent service timed out." : `Could not reach the agent service at ${agentBaseUrl()}.`
    );
  }

  try {
    const payload = (await agentResponse.json()) as Record<string, unknown>;
    return json(agentResponse.ok ? 200 : 502, {
      error: typeof payload.error === "string" ? payload.error : agentResponse.ok ? undefined : `Agent HTTP ${agentResponse.status}`,
      reply: typeof payload.reply === "string" ? payload.reply : "Something went wrong.",
      tools_used: Array.isArray(payload.tools_used) ? payload.tools_used : []
    });
  } catch {
    return errorReply(502, "Something went wrong.", `Agent returned an unreadable response (HTTP ${agentResponse.status}).`);
  }
}

export async function proxyReset(request: NextRequest): Promise<NextResponse> {
  const checked = await guard(request);
  if (!checked.ok) {
    return checked.response;
  }
  try {
    await callAgent("/reset", {
      session_id: scopedSessionId(checked.user, checked.body.conversation_id as string)
    });
  } catch {
    // The agent may be down; there is nothing to clear then.
  }
  return json(200, { cleared: true });
}

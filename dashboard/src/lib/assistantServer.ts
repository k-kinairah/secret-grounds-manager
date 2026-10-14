// Voice assistant (phase 2) -- server-only proxy logic for /api/assistant/*.
//
// The browser never talks to the agent service. This route runs inside the
// dashboard's own Node server, checks the caller's dashboard login against the
// backend (GET /auth/me with their sg_session cookie), scopes the conversation
// to that user, and only then forwards to the agent on 127.0.0.1. So the agent
// stays loopback-only, and only signed-in staff can reach it.

import { NextResponse, type NextRequest } from "next/server";
import { getApiBaseUrl } from "./api";
import { ACTION_ID_PATTERN, CONVERSATION_ID_PATTERN, MAX_MESSAGE_LENGTH, isAssistantEnabled } from "./assistant";

const AUTH_COOKIE_NAME = "sg_session";
const DEFAULT_AGENT_URL = "http://127.0.0.1:8781";
const AGENT_TIMEOUT_MS = 90_000;
const AUTH_TIMEOUT_MS = 10_000;

type ProxyBody = { conversation_id?: unknown; message?: unknown; action_id?: unknown };
type BackendUser = { user_id: string; display_name?: string; role?: string; is_protected?: boolean };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

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
    return typeof user.user_id === "string"
      ? {
          display_name: typeof user.display_name === "string" ? user.display_name : undefined,
          is_protected: user.is_protected === true,
          role: typeof user.role === "string" ? user.role : undefined,
          user_id: user.user_id
        }
      : null;
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
    const { user } = checked;
    agentResponse = await callAgent("/chat", {
      message,
      // Who is talking, as verified with the backend above -- only used to
      // phrase replies; the backend still enforces every permission.
      ...(user.display_name
        ? {
            operator: {
              display_name: user.display_name,
              is_admin_account: user.is_protected === true,
              role: user.role ?? "cashier"
            }
          }
        : {}),
      session_id: scopedSessionId(user, checked.body.conversation_id as string)
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
      pending_action: isRecord(payload.pending_action) ? payload.pending_action : undefined,
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

// --- Phase 3: confirmed actions ---------------------------------------------
//
// The agent only ever PREPARES an action. When the operator confirms on
// screen, this route takes the prepared request from the agent (single use),
// checks it against the hard-coded allow-list below, rebuilds it from the
// validated fields only, and sends it to the backend with the operator's own
// sg_session cookie -- exactly what their own click in the dashboard would do,
// so the backend's normal role checks, on-duty attribution and audit apply.

const PC_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$/;
const SEVERITIES = new Set(["info", "warning", "urgent"]);
const MAX_BROADCAST_TARGETS = 60;
const RATE_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;
const AMOUNT_PATTERN = /^\d{1,4}(\.\d{1,2})?$/;
const MAX_ADD_TIME_AMOUNT = 2000;

export type AgentAction = {
  action_id: string;
  kind: string;
  request: { method: string; path: string; body: Record<string, unknown> };
};
type BackendCall = { path: string; body: Record<string, unknown> };

function onlyKeys(body: Record<string, unknown>, allowed: string[]): boolean {
  return Object.keys(body).every((key) => allowed.includes(key));
}

function validMessageFields(body: Record<string, unknown>): Record<string, unknown> | null {
  const message = typeof body.message === "string" ? body.message.trim() : "";
  if (!message || message.length > 500) {
    return null;
  }
  const severity = body.severity ?? "info";
  if (typeof severity !== "string" || !SEVERITIES.has(severity)) {
    return null;
  }
  const fields: Record<string, unknown> = { message, severity };
  if (body.duration_seconds !== undefined) {
    const seconds = body.duration_seconds;
    if (typeof seconds !== "number" || !Number.isInteger(seconds) || seconds < 5 || seconds > 3600) {
      return null;
    }
    fields.duration_seconds = seconds;
  }
  return fields;
}

// The allow-list. Anything not matched exactly here is refused.
export function allowedBackendCall(action: AgentAction): BackendCall | null {
  const { kind, request } = action;
  if (!isRecord(request) || request.method !== "POST" || typeof request.path !== "string" || !isRecord(request.body)) {
    return null;
  }

  if (kind === "message_pc") {
    const match = /^\/pcs\/([^/]+)\/message$/.exec(request.path);
    let pcName = "";
    try {
      pcName = match ? decodeURIComponent(match[1]) : "";
    } catch {
      return null;
    }
    if (!PC_NAME_PATTERN.test(pcName) || !onlyKeys(request.body, ["message", "severity", "duration_seconds"])) {
      return null;
    }
    const fields = validMessageFields(request.body);
    return fields ? { body: fields, path: `/pcs/${encodeURIComponent(pcName)}/message` } : null;
  }

  if (kind === "broadcast") {
    if (request.path !== "/commands/broadcast") {
      return null;
    }
    if (!onlyKeys(request.body, ["message", "severity", "duration_seconds", "pc_names"])) {
      return null;
    }
    const names = request.body.pc_names;
    // Always explicit targets: an empty list would mean "every PC online at
    // run time", which is not what the operator confirmed.
    if (
      !Array.isArray(names) ||
      names.length === 0 ||
      names.length > MAX_BROADCAST_TARGETS ||
      !names.every((name) => typeof name === "string" && PC_NAME_PATTERN.test(name))
    ) {
      return null;
    }
    const fields = validMessageFields(request.body);
    return fields ? { body: { ...fields, pc_names: [...new Set(names as string[])] }, path: "/commands/broadcast" } : null;
  }

  if (kind === "add_time") {
    // Billed later: exactly ONE of a package (rate_id) or a peso amount.
    // "seconds" is refused -- on this endpoint it adds the time for free.
    const match = /^\/sessions\/([A-Za-z0-9-]{1,64})\/add-time$/.exec(request.path);
    const keys = Object.keys(request.body);
    if (!match || keys.length !== 1) {
      return null;
    }
    const path = `/sessions/${match[1]}/add-time`;
    if (keys[0] === "rate_id") {
      const rateId = request.body.rate_id;
      return typeof rateId === "string" && RATE_ID_PATTERN.test(rateId) ? { body: { rate_id: rateId }, path } : null;
    }
    if (keys[0] === "amount") {
      const amount = request.body.amount;
      if (typeof amount !== "string" || !AMOUNT_PATTERN.test(amount)) {
        return null;
      }
      const value = Number(amount);
      return value > 0 && value <= MAX_ADD_TIME_AMOUNT ? { body: { amount }, path } : null;
    }
    return null;
  }

  return null;
}

// Multi-call actions. Shutting down idle PCs is one backend call per PC,
// always {"force": false}: the backend then refuses any PC that has an
// active session (409), so nothing in use can be powered off from here.
const SHUTDOWN_PATH_TEMPLATE = "/pcs/{pc}/power/shutdown";

type BackendPlan = { batch: boolean; calls: (BackendCall & { pc?: string })[] };

export function allowedBackendPlan(action: AgentAction): BackendPlan | null {
  if (action.kind !== "shutdown_pcs") {
    const call = allowedBackendCall(action);
    return call ? { batch: false, calls: [call] } : null;
  }
  const request = action.request as AgentAction["request"] & { each_pc?: unknown };
  if (
    !isRecord(request) ||
    request.method !== "POST" ||
    request.path !== SHUTDOWN_PATH_TEMPLATE ||
    !isRecord(request.body) ||
    Object.keys(request.body).length !== 1 ||
    request.body.force !== false
  ) {
    return null;
  }
  const names = request.each_pc;
  if (
    !Array.isArray(names) ||
    names.length === 0 ||
    names.length > MAX_BROADCAST_TARGETS ||
    !names.every((name) => typeof name === "string" && PC_NAME_PATTERN.test(name))
  ) {
    return null;
  }
  return {
    batch: true,
    calls: [...new Set(names as string[])].map((pc) => ({
      body: { force: false },
      path: `/pcs/${encodeURIComponent(pc)}/power/shutdown`,
      pc
    }))
  };
}

async function sendToBackend(call: BackendCall, session: string): Promise<{ status: number; body: unknown }> {
  try {
    const response = await fetch(`${backendBaseUrl()}${call.path}`, {
      body: JSON.stringify(call.body),
      cache: "no-store",
      headers: { "Content-Type": "application/json", Cookie: `${AUTH_COOKIE_NAME}=${session}` },
      method: "POST",
      signal: AbortSignal.timeout(AGENT_TIMEOUT_MS)
    });
    try {
      return { body: await response.json(), status: response.status };
    } catch {
      return { body: { detail: `The shop system returned HTTP ${response.status}.` }, status: response.status };
    }
  } catch {
    return { body: { detail: "Could not reach the shop system." }, status: 502 };
  }
}

async function agentJson(path: string, payload: Record<string, unknown>): Promise<{ status: number; body: unknown }> {
  const response = await callAgent(path, payload);
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  return { body, status: response.status };
}

function invalidActionId(actionId: unknown): NextResponse | null {
  return typeof actionId === "string" && ACTION_ID_PATTERN.test(actionId)
    ? null
    : errorReply(400, "That request was invalid.", "Invalid action_id.");
}

export async function proxyConfirm(request: NextRequest): Promise<NextResponse> {
  const checked = await guard(request);
  if (!checked.ok) {
    return checked.response;
  }
  const actionId = checked.body.action_id;
  const invalid = invalidActionId(actionId);
  if (invalid) {
    return invalid;
  }
  const sessionId = scopedSessionId(checked.user, checked.body.conversation_id as string);

  let taken: { status: number; body: unknown };
  try {
    taken = await agentJson("/actions/take", { action_id: actionId, session_id: sessionId });
  } catch {
    return errorReply(
      502,
      "The assistant service isn't running right now.",
      `Could not reach the agent service at ${agentBaseUrl()}.`
    );
  }
  if (taken.status !== 200 || !isRecord(taken.body)) {
    const detail =
      isRecord(taken.body) && typeof taken.body.detail === "string"
        ? taken.body.detail
        : "That action is no longer available.";
    return errorReply(409, detail, detail);
  }

  const plan = allowedBackendPlan(taken.body as AgentAction);
  if (plan === null) {
    // Report the refusal so the agent forgets the action and can say why.
    await agentJson("/actions/result", {
      action_id: actionId,
      body: { detail: "That action isn't allowed from the assistant." },
      session_id: sessionId,
      status_code: 403
    }).catch(() => undefined);
    return errorReply(403, "That action isn't allowed from the assistant.", "Refused by the dashboard allow-list.");
  }

  const session = request.cookies.get(AUTH_COOKIE_NAME)?.value ?? "";
  let backendStatus: number;
  let backendBody: unknown;
  if (plan.batch) {
    // One call per PC, in order; every PC gets its own real result.
    const results: { pc: string | undefined; status_code: number; body: unknown }[] = [];
    for (const call of plan.calls) {
      const sent = await sendToBackend(call, session);
      results.push({ body: sent.body, pc: call.pc, status_code: sent.status });
    }
    backendStatus = 200;
    backendBody = { results };
  } else {
    const sent = await sendToBackend(plan.calls[0], session);
    backendStatus = sent.status;
    backendBody = sent.body;
  }

  try {
    const result = await agentJson("/actions/result", {
      action_id: actionId,
      body: backendBody,
      session_id: sessionId,
      status_code: backendStatus
    });
    if (result.status === 200 && isRecord(result.body)) {
      const outcome = result.body;
      return json(200, {
        outcome: {
          kind: outcome.kind,
          ok: outcome.ok === true,
          summary: outcome.summary,
          targets: Array.isArray(outcome.targets) ? outcome.targets : []
        },
        reply: typeof outcome.reply === "string" ? outcome.reply : "Done."
      });
    }
  } catch {
    // The action ran (or failed) but the agent couldn't record it; answer anyway.
  }
  const ok = backendStatus >= 200 && backendStatus < 300;
  return json(200, { outcome: { ok, targets: [] }, reply: ok ? "Done." : "That didn't go through." });
}

export async function proxyCancel(request: NextRequest): Promise<NextResponse> {
  const checked = await guard(request);
  if (!checked.ok) {
    return checked.response;
  }
  const actionId = checked.body.action_id;
  const invalid = invalidActionId(actionId);
  if (invalid) {
    return invalid;
  }
  try {
    const result = await agentJson("/actions/cancel", {
      action_id: actionId,
      session_id: scopedSessionId(checked.user, checked.body.conversation_id as string)
    });
    const reply = isRecord(result.body) && typeof result.body.reply === "string" ? result.body.reply : "Cancelled.";
    return json(200, { reply });
  } catch {
    return json(200, { reply: "Cancelled. Nothing was sent." });
  }
}

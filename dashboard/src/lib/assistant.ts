// Voice assistant (phase 2) -- client helpers. The page and its nav link only
// exist when NEXT_PUBLIC_ASSISTANT_ENABLED=true at build/dev time; off by
// default so the normal dashboard is unchanged.

export type AssistantReply = {
  reply: string;
  tools_used: string[];
  error?: string;
};

export function isAssistantEnabled(): boolean {
  // Must stay a literal property access so Next inlines it at build time.
  return process.env.NEXT_PUBLIC_ASSISTANT_ENABLED === "true";
}

const TOOL_LABELS: Record<string, string> = {
  get_today_overview: "today's sales",
  get_sales_report: "sales report",
  list_active_sessions: "active sessions",
  list_pcs: "PCs",
  find_member: "members",
  get_current_shift: "current shift"
};

export function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name.replace(/_/g, " ");
}

// Conversation ids are generated in the browser and scoped to the signed-in
// user by the server route, so one operator can never read another's chat.
export function newConversationId(): string {
  const random =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID() // secure contexts only (https / localhost)
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  return random.replace(/[^A-Za-z0-9-]/g, "").slice(0, 64);
}

export const CONVERSATION_ID_PATTERN = /^[A-Za-z0-9-]{1,64}$/;
export const MAX_MESSAGE_LENGTH = 2000;

async function postJson(path: string, body: unknown): Promise<Response> {
  return fetch(path, {
    body: JSON.stringify(body),
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    method: "POST"
  });
}

export async function sendAssistantMessage(conversationId: string, message: string): Promise<AssistantReply> {
  let response: Response;
  try {
    response = await postJson("/api/assistant/chat", { conversation_id: conversationId, message });
  } catch {
    return {
      reply: "I couldn't reach the dashboard server.",
      tools_used: [],
      error: "Network error talking to the dashboard."
    };
  }
  try {
    const payload = (await response.json()) as Partial<AssistantReply>;
    return {
      reply: typeof payload.reply === "string" ? payload.reply : "Something went wrong.",
      tools_used: Array.isArray(payload.tools_used) ? payload.tools_used.filter((t) => typeof t === "string") : [],
      error: typeof payload.error === "string" ? payload.error : response.ok ? undefined : `HTTP ${response.status}`
    };
  } catch {
    return { reply: "Something went wrong.", tools_used: [], error: `Unexpected response (HTTP ${response.status}).` };
  }
}

export async function resetAssistantConversation(conversationId: string): Promise<void> {
  try {
    await postJson("/api/assistant/reset", { conversation_id: conversationId });
  } catch {
    // Best effort: the server forgets idle sessions on its own.
  }
}

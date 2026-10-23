// Voice assistant (phase 2) -- client helpers. The page and its nav link only
// exist when NEXT_PUBLIC_ASSISTANT_ENABLED=true at build/dev time; off by
// default so the normal dashboard is unchanged.

export type PendingAction = {
  id: string;
  kind: string;
  summary: string;
  targets: string[];
  details: {
    message?: string;
    severity?: string;
    skipped_offline?: string[];
    // add_time
    added?: string;
    price?: string;
    new_end_time?: string;
    // shutdown_pcs
    admin_only?: boolean;
    skipped?: { pc: string; reason: string }[];
  };
  expires_in_seconds: number;
};

// "received": the PC got a power command -- not proof it powered off.
export type ActionTargetResult = {
  pc: string;
  // "not_shown": the PC got it but couldn't show it (e.g. its pop-up couldn't open).
  status: "done" | "received" | "skipped" | "offline" | "failed" | "not_shown";
  detail: string | null;
};

export type ActionOutcome = {
  ok: boolean;
  kind?: string;
  summary?: string;
  targets: ActionTargetResult[];
};

export type AssistantReply = {
  reply: string;
  tools_used: string[];
  error?: string;
  pending_action?: PendingAction;
};

export type ActionReply = {
  reply: string;
  outcome?: ActionOutcome;
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
  get_current_shift: "current shift",
  propose_message_pc: "a message",
  propose_broadcast: "an announcement",
  propose_add_time: "time to add",
  propose_shutdown_idle_pcs: "PCs to shut down",
  save_handover_note: "a handover note",
  list_handover_notes: "handover notes",
  resolve_handover_note: "a handover note"
};

const TOOL_VERBS: Record<string, string> = {
  resolve_handover_note: "Marked done",
  save_handover_note: "Saved"
};

// Chip under a reply: "Checked: today's sales" for lookups, "Prepared: a
// message" for propose_* tools (nothing was sent by preparing it).
export function toolChipText(name: string): string {
  const verb = TOOL_VERBS[name] ?? (name.startsWith("propose_") ? "Prepared" : "Checked");
  return `${verb}: ${toolLabel(name)}`;
}

// While the tool is still running: "Checking: today's sales", "Preparing: a message".
export function liveToolText(name: string): string {
  const verb = name.startsWith("propose_")
    ? "Preparing"
    : name === "save_handover_note"
      ? "Saving"
      : name === "resolve_handover_note"
        ? "Updating"
        : "Checking";
  return `${verb}: ${toolLabel(name)}`;
}

// --- Shift handover notes (phase 3 step d) -----------------------------------

export type HandoverNote = { note_id: string; text: string; author: string; age: string };
export const HANDOVER_TOOLS = new Set(["save_handover_note", "resolve_handover_note"]);

export async function fetchHandoverNotes(): Promise<{ enabled: boolean; notes: HandoverNote[]; error?: string }> {
  try {
    const response = await fetch("/api/assistant/handover", { cache: "no-store", credentials: "same-origin" });
    const body = (await response.json()) as { enabled?: unknown; notes?: unknown; error?: unknown };
    const notes = Array.isArray(body.notes)
      ? body.notes.filter(
          (n): n is HandoverNote =>
            typeof n === "object" && n !== null && typeof (n as HandoverNote).note_id === "string" && typeof (n as HandoverNote).text === "string"
        )
      : [];
    return { enabled: body.enabled === true, error: typeof body.error === "string" ? body.error : undefined, notes };
  } catch {
    return { enabled: false, error: "Couldn't load handover notes.", notes: [] };
  }
}

export async function resolveHandoverNote(conversationId: string, noteId: string): Promise<string | null> {
  try {
    const response = await postJson("/api/assistant/handover/resolve", { conversation_id: conversationId, note_id: noteId });
    if (response.ok) {
      return null;
    }
    const body = (await response.json()) as { error?: unknown };
    return typeof body.error === "string" ? body.error : "Couldn't update the note.";
  } catch {
    return "Couldn't update the note.";
  }
}

export const ACTION_ID_PATTERN = /^[a-f0-9]{32}$/;

// Strict spoken confirmation: the WHOLE utterance must be one of these, so a
// sentence that merely contains "yes" never confirms anything. Matching is
// done here in the browser; the model is never asked whether to proceed.
const CONFIRM_PHRASES = new Set([
  "yes",
  "yes please",
  "yeah",
  "yep",
  "confirm",
  "confirmed",
  "i confirm",
  "go ahead",
  "do it",
  "send",
  "send it",
  "ok send it",
  "okay send it"
]);
const CANCEL_PHRASES = new Set(["no", "nope", "cancel", "cancel it", "stop", "never mind", "nevermind", "dont", "no thanks"]);

export function matchSpokenDecision(text: string): "confirm" | "cancel" | null {
  const normalized = text
    .toLowerCase()
    .replace(/[^a-z\s]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (CONFIRM_PHRASES.has(normalized)) {
    return "confirm";
  }
  if (CANCEL_PHRASES.has(normalized)) {
    return "cancel";
  }
  return null;
}

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
    return toAssistantReply((await response.json()) as Partial<AssistantReply>, response.ok, response.status);
  } catch {
    return { reply: "Something went wrong.", tools_used: [], error: `Unexpected response (HTTP ${response.status}).` };
  }
}

function toAssistantReply(payload: Partial<AssistantReply>, ok: boolean, status: number): AssistantReply {
  return {
    reply: typeof payload.reply === "string" ? payload.reply : "Something went wrong.",
    tools_used: Array.isArray(payload.tools_used) ? payload.tools_used.filter((t) => typeof t === "string") : [],
    error: typeof payload.error === "string" ? payload.error : ok ? undefined : `HTTP ${status}`,
    pending_action:
      payload.pending_action && typeof payload.pending_action.id === "string" ? payload.pending_action : undefined
  };
}

// Latency fix: the streaming route reports each tool as it starts, so the page
// can show "Thinking… · Checking: today's sales" before the answer is ready.
// Falls back to the plain /chat route if streaming isn't available.
export async function sendAssistantMessageStream(
  conversationId: string,
  message: string,
  onTool: (name: string) => void
): Promise<AssistantReply> {
  let response: Response;
  try {
    response = await postJson("/api/assistant/chat/stream", { conversation_id: conversationId, message });
  } catch {
    return sendAssistantMessage(conversationId, message);
  }
  const type = response.headers.get("content-type") ?? "";
  if (!response.ok || response.body === null || !type.includes("ndjson")) {
    if (type.includes("json")) {
      try {
        return toAssistantReply((await response.json()) as Partial<AssistantReply>, response.ok, response.status);
      } catch {
        // fall through to the plain route
      }
    }
    return sendAssistantMessage(conversationId, message);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      newline = buffer.indexOf("\n");
      if (!line) {
        continue;
      }
      let event: { type?: unknown; name?: unknown } & Partial<AssistantReply>;
      try {
        event = JSON.parse(line) as typeof event;
      } catch {
        continue;
      }
      if (event.type === "tool" && typeof event.name === "string" && event.name) {
        onTool(event.name);
      } else if (event.type === "final") {
        return toAssistantReply(event, true, 200);
      }
    }
    if (done) {
      break;
    }
  }
  return { reply: "Something went wrong.", tools_used: [], error: "The answer stream ended early." };
}

async function postAction(path: string, conversationId: string, actionId: string): Promise<ActionReply> {
  let response: Response;
  try {
    response = await postJson(path, { action_id: actionId, conversation_id: conversationId });
  } catch {
    return { error: "Network error talking to the dashboard.", reply: "I couldn't reach the dashboard server." };
  }
  try {
    const payload = (await response.json()) as Partial<ActionReply>;
    return {
      error: typeof payload.error === "string" ? payload.error : response.ok ? undefined : `HTTP ${response.status}`,
      outcome: payload.outcome,
      reply: typeof payload.reply === "string" ? payload.reply : "Something went wrong."
    };
  } catch {
    return { error: `Unexpected response (HTTP ${response.status}).`, reply: "Something went wrong." };
  }
}

export function confirmAssistantAction(conversationId: string, actionId: string): Promise<ActionReply> {
  return postAction("/api/assistant/confirm", conversationId, actionId);
}

export function cancelAssistantAction(conversationId: string, actionId: string): Promise<ActionReply> {
  return postAction("/api/assistant/cancel", conversationId, actionId);
}

export async function resetAssistantConversation(conversationId: string): Promise<void> {
  try {
    await postJson("/api/assistant/reset", { conversation_id: conversationId });
  } catch {
    // Best effort: the server forgets idle sessions on its own.
  }
}

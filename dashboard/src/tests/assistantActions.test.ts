// Phase 3: confirmed actions. Run: npx tsx src/tests/assistantActions.test.ts
// fetch is stubbed; nothing touches the network, the backend or the agent.
import assert from "node:assert/strict";
import { NextRequest } from "next/server";
import { matchSpokenDecision, toolChipText } from "../lib/assistant";
import { type AgentAction, allowedBackendCall, proxyCancel, proxyConfirm } from "../lib/assistantServer";

type Call = { url: string; init?: RequestInit };

function stubFetch(handler: (url: string, init?: RequestInit) => Response | Promise<Response>): Call[] {
  const calls: Call[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    calls.push({ init, url });
    return handler(url, init);
  }) as typeof fetch;
  return calls;
}

function request(path: string, body: unknown, headers: Record<string, string> = {}): NextRequest {
  return new NextRequest(`http://127.0.0.1:3000${path}`, {
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", host: "127.0.0.1:3000", ...headers },
    method: "POST"
  });
}

const ACTION_ID = "0123456789abcdef0123456789abcdef";
const signedIn = { cookie: "sg_session=abc123; other=1" };
const messageAction: AgentAction = {
  action_id: ACTION_ID,
  kind: "message_pc",
  request: {
    body: { duration_seconds: 20, message: "Your friend is here", severity: "info" },
    method: "POST",
    path: "/pcs/PC-03/message"
  }
};
const broadcastAction: AgentAction = {
  action_id: ACTION_ID,
  kind: "broadcast",
  request: {
    body: { duration_seconds: 20, message: "Closing soon", pc_names: ["PC-01", "PC-02", "PC-01"], severity: "warning" },
    method: "POST",
    path: "/commands/broadcast"
  }
};

function withRequest(action: AgentAction, patch: Partial<AgentAction["request"]>): AgentAction {
  return { ...action, request: { ...action.request, ...patch } };
}

async function run() {
  process.env.ASSISTANT_BACKEND_URL = "http://127.0.0.1:8000";
  process.env.ASSISTANT_AGENT_URL = "http://127.0.0.1:8781";
  process.env.NEXT_PUBLIC_ASSISTANT_ENABLED = "true";

  // --- chips -----------------------------------------------------------------------
  assert.equal(toolChipText("propose_message_pc"), "Prepared: a message");
  assert.equal(toolChipText("propose_broadcast"), "Prepared: an announcement");
  assert.equal(toolChipText("get_today_overview"), "Checked: today's sales");

  // --- strict spoken decision ---------------------------------------------------
  for (const yes of ["yes", "Yes.", "confirm", "Go ahead!", "send it", "OK, send it"]) {
    assert.equal(matchSpokenDecision(yes), "confirm", yes);
  }
  for (const no of ["no", "Cancel.", "never mind", "don't", "stop"]) {
    assert.equal(matchSpokenDecision(no), "cancel", no);
  }
  for (const other of ["yes but change it to PC 5", "send it to PC 5", "is it sent", "", "yes yes", "confirm it later"]) {
    assert.equal(matchSpokenDecision(other), null, other);
  }

  // --- allow-list ------------------------------------------------------------------
  assert.deepEqual(allowedBackendCall(messageAction), {
    body: { duration_seconds: 20, message: "Your friend is here", severity: "info" },
    path: "/pcs/PC-03/message"
  });
  assert.deepEqual(allowedBackendCall(withRequest(messageAction, { path: "/pcs/VIP%201/message" }))?.path, "/pcs/VIP%201/message");
  assert.deepEqual(allowedBackendCall(broadcastAction), {
    body: { duration_seconds: 20, message: "Closing soon", pc_names: ["PC-01", "PC-02"], severity: "warning" },
    path: "/commands/broadcast"
  });
  const refused: [string, AgentAction][] = [
    ["GET", withRequest(messageAction, { method: "GET" })],
    ["other endpoint", withRequest(messageAction, { path: "/sessions/abc/end" })],
    ["power", withRequest(messageAction, { path: "/pcs/PC-03/power/shutdown" })],
    ["traversal", withRequest(messageAction, { path: "/pcs/..%2F..%2Fsessions/message" })],
    ["bad escape", withRequest(messageAction, { path: "/pcs/%E0%A4%A/message" })],
    ["extra key", withRequest(messageAction, { body: { ...messageAction.request.body, force: true } })],
    ["long message", withRequest(messageAction, { body: { message: "x".repeat(501) } })],
    ["blank message", withRequest(messageAction, { body: { message: "   " } })],
    ["bad severity", withRequest(messageAction, { body: { message: "hi", severity: "shout" } })],
    ["bad duration", withRequest(messageAction, { body: { duration_seconds: 99999, message: "hi" } })],
    ["broadcast to everyone", withRequest(broadcastAction, { body: { message: "hi", pc_names: [] } })],
    ["broadcast no targets", withRequest(broadcastAction, { body: { message: "hi" } })],
    ["broadcast bad name", withRequest(broadcastAction, { body: { message: "hi", pc_names: ["PC-01/../x"] } })],
    ["kind/path mismatch", { ...broadcastAction, kind: "message_pc" }],
    ["unknown kind", { ...messageAction, kind: "end_session" }]
  ];
  for (const [label, action] of refused) {
    assert.equal(allowedBackendCall(action), null, label);
  }

  // --- add time (billed later) ------------------------------------------------------
  const addTime: AgentAction = {
    action_id: ACTION_ID,
    kind: "add_time",
    request: { body: { rate_id: "r-1h" }, method: "POST", path: "/sessions/0b5c1e2a-1111-4c3d-9e8f-123456789abc/add-time" }
  };
  assert.deepEqual(allowedBackendCall(addTime), {
    body: { rate_id: "r-1h" },
    path: "/sessions/0b5c1e2a-1111-4c3d-9e8f-123456789abc/add-time"
  });
  assert.deepEqual(allowedBackendCall(withRequest(addTime, { body: { amount: "50.00" } }))?.body, { amount: "50.00" });
  assert.equal(toolChipText("propose_add_time"), "Prepared: time to add");
  const refusedAddTime: [string, AgentAction][] = [
    ["free seconds", withRequest(addTime, { body: { seconds: 3600 } })],
    ["rate and amount", withRequest(addTime, { body: { amount: "20.00", rate_id: "r-1h" } })],
    ["rate and seconds", withRequest(addTime, { body: { rate_id: "r-1h", seconds: 60 } })],
    ["empty body", withRequest(addTime, { body: {} })],
    ["amount too big", withRequest(addTime, { body: { amount: "2000.01" } })],
    ["amount zero", withRequest(addTime, { body: { amount: "0.00" } })],
    ["amount as number", withRequest(addTime, { body: { amount: 50 } })],
    ["amount not money", withRequest(addTime, { body: { amount: "1e3" } })],
    ["bad rate id", withRequest(addTime, { body: { rate_id: "../x" } })],
    ["prepaid endpoint", withRequest(addTime, { path: "/sessions/abc/add-time-prepaid" })],
    ["member endpoint", withRequest(addTime, { path: "/sessions/abc/add-time-member" })],
    ["bad session id", withRequest(addTime, { path: "/sessions/a%2Fb/add-time" })],
    ["GET", withRequest(addTime, { method: "GET" })],
    ["kind mismatch", { ...addTime, kind: "message_pc" }]
  ];
  for (const [label, action] of refusedAddTime) {
    assert.equal(allowedBackendCall(action), null, label);
  }

  // --- confirm: happy path ------------------------------------------------------------
  let calls = stubFetch((url, init) => {
    if (url.endsWith("/auth/me")) {
      return Response.json({ role: "cashier", user_id: "user-1" });
    }
    if (url.endsWith("/actions/take")) {
      return Response.json(messageAction);
    }
    if (url === "http://127.0.0.1:8000/pcs/PC-03/message") {
      return Response.json({ acknowledgements: [], command: { pc_name: "PC-03", status: "completed" } });
    }
    if (url.endsWith("/actions/result")) {
      const sent = JSON.parse(String(init?.body));
      assert.equal(sent.status_code, 200);
      return Response.json({
        kind: "message_pc",
        ok: true,
        reply: "Done. The message is showing on PC-03.",
        summary: "s",
        targets: [{ detail: null, pc: "PC-03", status: "done" }]
      });
    }
    throw new Error(`unexpected ${url}`);
  });
  let response = await proxyConfirm(
    request("/api/assistant/confirm", { action_id: ACTION_ID, conversation_id: "c1" }, { ...signedIn, origin: "http://127.0.0.1:3000" })
  );
  assert.equal(response.status, 200);
  const confirmed = (await response.json()) as { reply: string; outcome: { ok: boolean; targets: unknown[] } };
  assert.equal(confirmed.reply, "Done. The message is showing on PC-03.");
  assert.equal(confirmed.outcome.ok, true);
  assert.deepEqual(
    calls.map((c) => c.url),
    [
      "http://127.0.0.1:8000/auth/me",
      "http://127.0.0.1:8781/actions/take",
      "http://127.0.0.1:8000/pcs/PC-03/message",
      "http://127.0.0.1:8781/actions/result"
    ]
  );
  // Taken from THIS user's session only.
  assert.deepEqual(JSON.parse(String(calls[1].init?.body)), { action_id: ACTION_ID, session_id: "user-1:c1" });
  // Sent as the operator, with only their session cookie, and the rebuilt body.
  const backendCall = calls[2];
  assert.equal(backendCall.init?.method, "POST");
  assert.equal((backendCall.init?.headers as Record<string, string>).Cookie, "sg_session=abc123");
  assert.equal((backendCall.init?.headers as Record<string, string>).Authorization, undefined);
  assert.deepEqual(JSON.parse(String(backendCall.init?.body)), messageAction.request.body);

  // --- confirm: backend refuses (e.g. cashier lacks permission) -> reported --------------
  calls = stubFetch((url, init) => {
    if (url.endsWith("/auth/me")) {
      return Response.json({ user_id: "user-1" });
    }
    if (url.endsWith("/actions/take")) {
      return Response.json(messageAction);
    }
    if (url.includes(":8000/pcs/")) {
      return Response.json({ detail: "This PC is offline" }, { status: 409 });
    }
    const sent = JSON.parse(String(init?.body));
    assert.equal(sent.status_code, 409);
    assert.deepEqual(sent.body, { detail: "This PC is offline" });
    return Response.json({ ok: false, reply: "That didn't go through: this PC is offline.", targets: [] });
  });
  response = await proxyConfirm(request("/api/assistant/confirm", { action_id: ACTION_ID, conversation_id: "c1" }, signedIn));
  assert.equal(((await response.json()) as { reply: string }).reply, "That didn't go through: this PC is offline.");

  // --- confirm: allow-list refusal -> backend never called ---------------------------------
  calls = stubFetch((url) => {
    if (url.endsWith("/auth/me")) {
      return Response.json({ user_id: "user-1" });
    }
    if (url.endsWith("/actions/take")) {
      return Response.json(withRequest(messageAction, { path: "/sessions/abc/end" }));
    }
    if (url.endsWith("/actions/result")) {
      return Response.json({ ok: false, reply: "x", targets: [] });
    }
    throw new Error(`backend must not be called: ${url}`);
  });
  response = await proxyConfirm(request("/api/assistant/confirm", { action_id: ACTION_ID, conversation_id: "c1" }, signedIn));
  assert.equal(response.status, 403);
  assert.ok(!calls.some((c) => c.url.startsWith("http://127.0.0.1:8000/sessions")));
  assert.equal(JSON.parse(String(calls[2].init?.body)).status_code, 403);

  // --- confirm: not signed in / bad id / already used ---------------------------------------
  calls = stubFetch(() => Response.json({}));
  response = await proxyConfirm(request("/api/assistant/confirm", { action_id: ACTION_ID, conversation_id: "c1" }));
  assert.equal(response.status, 401);
  assert.equal(calls.length, 0);

  stubFetch((url) => (url.endsWith("/auth/me") ? Response.json({ user_id: "user-1" }) : Response.json({})));
  response = await proxyConfirm(request("/api/assistant/confirm", { action_id: "not-an-id", conversation_id: "c1" }, signedIn));
  assert.equal(response.status, 400);

  stubFetch((url) =>
    url.endsWith("/auth/me")
      ? Response.json({ user_id: "user-1" })
      : Response.json({ detail: "That action expired. Please ask again." }, { status: 409 })
  );
  response = await proxyConfirm(request("/api/assistant/confirm", { action_id: ACTION_ID, conversation_id: "c1" }, signedIn));
  assert.equal(response.status, 409);
  assert.equal(((await response.json()) as { reply: string }).reply, "That action expired. Please ask again.");

  // --- cancel is scoped to the user ----------------------------------------------------------
  calls = stubFetch((url) =>
    url.endsWith("/auth/me") ? Response.json({ user_id: "user-9" }) : Response.json({ reply: "Okay, cancelled. Nothing was sent." })
  );
  response = await proxyCancel(request("/api/assistant/cancel", { action_id: ACTION_ID, conversation_id: "c1" }, signedIn));
  assert.equal(((await response.json()) as { reply: string }).reply, "Okay, cancelled. Nothing was sent.");
  assert.equal(calls[1].url, "http://127.0.0.1:8781/actions/cancel");
  assert.deepEqual(JSON.parse(String(calls[1].init?.body)), { action_id: ACTION_ID, session_id: "user-9:c1" });

  // --- flag off -> 404 -------------------------------------------------------------------------
  delete process.env.NEXT_PUBLIC_ASSISTANT_ENABLED;
  calls = stubFetch(() => Response.json({}));
  response = await proxyConfirm(request("/api/assistant/confirm", { action_id: ACTION_ID, conversation_id: "c1" }, signedIn));
  assert.equal(response.status, 404);
  assert.equal(calls.length, 0);

  console.log("Assistant action tests passed.");
}

void run().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});

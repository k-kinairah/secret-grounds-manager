// Phase 3 step (c): shut down idle PCs + operator context. Run: npx tsx src/tests/assistantShutdown.test.ts
// fetch is stubbed; nothing touches the network, the backend or the agent.
import assert from "node:assert/strict";
import { NextRequest } from "next/server";
import { toolChipText } from "../lib/assistant";
import { type AgentAction, allowedBackendPlan, proxyChat, proxyConfirm } from "../lib/assistantServer";

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

function request(path: string, body: unknown): NextRequest {
  return new NextRequest(`http://127.0.0.1:3000${path}`, {
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", cookie: "sg_session=admin-cookie", host: "127.0.0.1:3000" },
    method: "POST"
  });
}

const ACTION_ID = "fedcba9876543210fedcba9876543210";
const shutdown: AgentAction & { request: AgentAction["request"] & { each_pc: string[] } } = {
  action_id: ACTION_ID,
  kind: "shutdown_pcs",
  request: { body: { force: false }, each_pc: ["PC-01", "PC 3", "PC-01"], method: "POST", path: "/pcs/{pc}/power/shutdown" }
};

function variant(patch: Record<string, unknown>): AgentAction {
  return { ...shutdown, request: { ...shutdown.request, ...patch } } as AgentAction;
}

async function run() {
  process.env.ASSISTANT_BACKEND_URL = "http://127.0.0.1:8000";
  process.env.ASSISTANT_AGENT_URL = "http://127.0.0.1:8781";
  process.env.NEXT_PUBLIC_ASSISTANT_ENABLED = "true";

  assert.equal(toolChipText("propose_shutdown_idle_pcs"), "Prepared: PCs to shut down");

  // --- allow-list ----------------------------------------------------------------------
  assert.deepEqual(allowedBackendPlan(shutdown), {
    batch: true,
    calls: [
      { body: { force: false }, path: "/pcs/PC-01/power/shutdown", pc: "PC-01" },
      { body: { force: false }, path: "/pcs/PC%203/power/shutdown", pc: "PC 3" }
    ]
  });
  const refused: [string, AgentAction][] = [
    ["force true", variant({ body: { force: true } })],
    ["force with reason", variant({ body: { confirm: true, force: true, reason: "x" } })],
    ["extra body key", variant({ body: { force: false, reason: "x" } })],
    ["empty body", variant({ body: {} })],
    ["restart", variant({ path: "/pcs/{pc}/power/restart" })],
    ["sleep", variant({ path: "/pcs/{pc}/power/sleep" })],
    ["wake", variant({ path: "/pcs/{pc}/wake" })],
    ["no PCs", variant({ each_pc: [] })],
    ["missing PCs", variant({ each_pc: undefined })],
    ["bad PC name", variant({ each_pc: ["PC-01/../x"] })],
    ["too many", variant({ each_pc: Array.from({ length: 61 }, (_, i) => `PC-${i}`) })],
    ["GET", variant({ method: "GET" })]
  ];
  for (const [label, action] of refused) {
    assert.equal(allowedBackendPlan(action), null, label);
  }
  // Single-call kinds still go through the same plan.
  const single = allowedBackendPlan({
    action_id: ACTION_ID,
    kind: "message_pc",
    request: { body: { message: "hi" }, method: "POST", path: "/pcs/PC-01/message" }
  });
  assert.deepEqual(single, { batch: false, calls: [{ body: { message: "hi", severity: "info" }, path: "/pcs/PC-01/message" }] });

  // --- confirm runs one call per PC with the operator's cookie, reports every result --------
  let reported: { status_code: number; body: { results: { pc: string; status_code: number }[] } } | null = null;
  let calls = stubFetch((url, init) => {
    if (url.endsWith("/auth/me")) {
      return Response.json({ display_name: "Admin", is_protected: true, role: "owner", user_id: "admin-1" });
    }
    if (url.endsWith("/actions/take")) {
      return Response.json(shutdown);
    }
    if (url === "http://127.0.0.1:8000/pcs/PC-01/power/shutdown") {
      return Response.json({
        acknowledgements: [{ message: "accepted (test mode: not powered off)", status: "completed" }],
        command: { pc_name: "PC-01", status: "completed" }
      });
    }
    if (url === "http://127.0.0.1:8000/pcs/PC%203/power/shutdown") {
      return Response.json({ detail: "Active sessions require force=true" }, { status: 409 });
    }
    if (url.endsWith("/actions/result")) {
      reported = JSON.parse(String(init?.body));
      return Response.json({
        kind: "shutdown_pcs",
        ok: true,
        reply: "PC-01 received the shutdown command (test mode: not powered off). PC 3 was skipped because it is in use now.",
        targets: [
          { detail: "test mode: not powered off", pc: "PC-01", status: "received" },
          { detail: "in use now", pc: "PC 3", status: "skipped" }
        ]
      });
    }
    throw new Error(`unexpected ${url}`);
  });
  const response = await proxyConfirm(request("/api/assistant/confirm", { action_id: ACTION_ID, conversation_id: "c1" }));
  assert.equal(response.status, 200);
  const body = (await response.json()) as { reply: string; outcome: { targets: { status: string }[] } };
  assert.match(body.reply, /test mode: not powered off/);
  assert.deepEqual(body.outcome.targets.map((t) => t.status), ["received", "skipped"]);
  const backendCalls = calls.filter((c) => c.url.startsWith("http://127.0.0.1:8000/pcs/"));
  assert.equal(backendCalls.length, 2);
  for (const call of backendCalls) {
    assert.equal(call.init?.method, "POST");
    assert.deepEqual(JSON.parse(String(call.init?.body)), { force: false });
    assert.equal((call.init?.headers as Record<string, string>).Cookie, "sg_session=admin-cookie");
  }
  assert.ok(reported);
  const sent = reported as { status_code: number; body: { results: { pc: string; status_code: number }[] } };
  assert.equal(sent.status_code, 200);
  assert.deepEqual(sent.body.results.map((r) => [r.pc, r.status_code]), [["PC-01", 200], ["PC 3", 409]]);

  // --- refused plan never reaches the backend -----------------------------------------------------
  calls = stubFetch((url) => {
    if (url.endsWith("/auth/me")) {
      return Response.json({ user_id: "admin-1" });
    }
    if (url.endsWith("/actions/take")) {
      return Response.json(variant({ body: { force: true } }));
    }
    if (url.endsWith("/actions/result")) {
      return Response.json({ ok: false, reply: "x", targets: [] });
    }
    throw new Error(`backend must not be called: ${url}`);
  });
  const refusedResponse = await proxyConfirm(request("/api/assistant/confirm", { action_id: ACTION_ID, conversation_id: "c1" }));
  assert.equal(refusedResponse.status, 403);
  assert.ok(!calls.some((c) => c.url.includes("/power/")));

  // --- chat passes who is talking (as verified by the backend) -----------------------------------
  calls = stubFetch((url) =>
    url.endsWith("/auth/me")
      ? Response.json({ display_name: "Neil", is_protected: false, role: "cashier", user_id: "u-neil" })
      : Response.json({ reply: "ok", tools_used: [] })
  );
  await proxyChat(request("/api/assistant/chat", { conversation_id: "c1", message: "shut down the idle pcs" }));
  // Step (e) also fetches the operator's shift first, so find the agent call by URL.
  const chatCall = calls.find((c) => c.url.endsWith("/chat"));
  assert.ok(chatCall);
  assert.deepEqual(JSON.parse(String(chatCall.init?.body)), {
    message: "shut down the idle pcs",
    operator: { display_name: "Neil", is_admin_account: false, role: "cashier" },
    session_id: "u-neil:c1"
  });

  console.log("Assistant shutdown tests passed.");
}

void run().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});

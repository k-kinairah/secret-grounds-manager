// Drawer check: the chat proxy passes who is on duty and their running totals to the agent,
// read with the operator's own login. Run: npx tsx src/tests/assistantDuty.test.ts
// fetch is stubbed; nothing touches the network, the backend or the agent.
import assert from "node:assert/strict";
import { NextRequest } from "next/server";
import { proxyChat, proxyChatStream } from "../lib/assistantServer";

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

function post(path: string, body: unknown): NextRequest {
  return new NextRequest(`http://127.0.0.1:3000${path}`, {
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", cookie: "sg_session=tyrone-cookie", host: "127.0.0.1:3000" },
    method: "POST"
  });
}

const TYRONE = { display_name: "Tyrone", is_protected: false, role: "owner", user_id: "operator_tyrone" };
const CURRENT = {
  operator: { display_name: "Tyrone", role: "owner", user_id: "operator_tyrone", username: "operator_tyrone" },
  selected_at: "2026-10-17T00:05:00Z",
  selected_by_user_id: "operator_tyrone"
};
const SALE = {
  cash_payments: "1050.00",
  gcash_payments: "220.00",
  has_open_shift: false,
  operator_name: "Tyrone",
  operator_sale: "1270.00",
  operator_user_id: "operator_tyrone",
  payment_count: 31
};

type Answer = () => Response;

// Returns the body the agent received, given what the two backend reads answer.
async function agentBody(
  current: Answer,
  sale: Answer,
  stream = false
): Promise<{ calls: Call[]; body: Record<string, unknown> }> {
  const calls = stubFetch((url) => {
    if (url.endsWith("/auth/me")) return Response.json(TYRONE);
    if (url.endsWith("/operators/current")) return current();
    if (url.endsWith("/dashboard/operator-sale")) return sale();
    return stream ? new Response('{"type":"final","reply":"ok","tools_used":[]}\n') : Response.json({ reply: "ok", tools_used: [] });
  });
  if (stream) {
    await (await proxyChatStream(post("/api/assistant/chat/stream", { conversation_id: "c1", message: "check my drawer" }))).text();
  } else {
    await proxyChat(post("/api/assistant/chat", { conversation_id: "c1", message: "check my drawer" }));
  }
  const agentCall = calls.find((c) => c.url.startsWith("http://127.0.0.1:8781/chat"));
  assert.ok(agentCall);
  return { body: JSON.parse(String(agentCall.init?.body)) as Record<string, unknown>, calls };
}

async function run() {
  process.env.ASSISTANT_BACKEND_URL = "http://127.0.0.1:8000";
  process.env.ASSISTANT_AGENT_URL = "http://127.0.0.1:8781";
  process.env.NEXT_PUBLIC_ASSISTANT_ENABLED = "true";

  // --- on duty: both reads use the operator's own login, read-only ------------------------------
  const { body, calls } = await agentBody(() => Response.json(CURRENT), () => Response.json(SALE));
  for (const path of ["/operators/current", "/dashboard/operator-sale"]) {
    const call = calls.find((c) => c.url === `http://127.0.0.1:8000${path}`);
    assert.ok(call, path);
    assert.equal(call.init?.method ?? "GET", "GET");
    assert.deepEqual(call.init?.headers, { Cookie: "sg_session=tyrone-cookie" });
  }
  assert.deepEqual(body.duty, {
    asked_by_on_duty_operator: true,
    cash_collected: "1050.00",
    gcash_collected: "220.00",
    on_duty: true,
    operator_name: "Tyrone",
    payment_count: 31,
    since: "2026-10-17T00:05:00Z"
  });
  // Nothing is ever written: only GETs to the backend.
  assert.ok(!calls.some((c) => c.url.startsWith("http://127.0.0.1:8000") && (c.init?.method ?? "GET") !== "GET"));

  // --- the stream route sends the same summary ------------------------------------------------------
  assert.deepEqual((await agentBody(() => Response.json(CURRENT), () => Response.json(SALE), true)).body.duty, body.duty);

  // --- someone else is on duty -> flagged for the agent -----------------------------------------------
  const rogeneOnDuty = { ...CURRENT, operator: { ...CURRENT.operator, display_name: "Rogene", user_id: "operator_rogene" } };
  const other = (await agentBody(() => Response.json(rogeneOnDuty), () => Response.json(SALE))).body.duty as Record<string, unknown>;
  assert.equal(other.asked_by_on_duty_operator, false);
  assert.equal(other.operator_name, "Rogene");

  // --- nobody on duty -> {on_duty: false} ----------------------------------------------------------------
  const nobody = { operator: null, selected_at: null, selected_by_user_id: null };
  assert.deepEqual((await agentBody(() => Response.json(nobody), () => Response.json({ has_open_shift: false, payment_count: 0 }))).body.duty, { on_duty: false });

  // --- errors or odd shapes -> summary omitted (the agent then has no drawer tool) -----------------------
  assert.ok(!("duty" in (await agentBody(() => Response.json({ detail: "nope" }, { status: 403 }), () => Response.json(SALE))).body));
  assert.ok(!("duty" in (await agentBody(() => Response.json(CURRENT), () => Response.json({ reply: "x" }))).body));
  assert.ok(!("duty" in (await agentBody(() => { throw new Error("backend down"); }, () => Response.json(SALE))).body));

  console.log("Assistant duty tests passed.");
}

void run().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});

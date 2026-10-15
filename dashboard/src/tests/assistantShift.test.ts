// Step (e): the chat proxy passes the operator's OWN shift to the agent for the read-only
// close check. Run: npx tsx src/tests/assistantShift.test.ts
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
    headers: { "Content-Type": "application/json", cookie: "sg_session=neil-cookie", host: "127.0.0.1:3000" },
    method: "POST"
  });
}

const NEIL = { display_name: "Neil", is_protected: false, role: "cashier", user_id: "u-neil" };
const DETAIL = {
  shift: { cashier_name: "Neil", opened_at: "2026-10-16T08:00:00Z", opening_cash: "1000.00", status: "open" },
  totals: {
    expected_cash: "4250.00",
    outstanding_unsettled_balance: "0.00",
    payment_count: 12,
    review_required_payments: 1,
    settlement_pending_sessions: 2
  }
};

// Returns the body the agent received, given what /shifts/current answers.
async function agentBody(shiftResponse: () => Response, stream = false): Promise<{ calls: Call[]; body: Record<string, unknown> }> {
  const calls = stubFetch((url) => {
    if (url.endsWith("/auth/me")) {
      return Response.json(NEIL);
    }
    if (url.endsWith("/shifts/current")) {
      return shiftResponse();
    }
    return stream ? new Response('{"type":"final","reply":"ok","tools_used":[]}\n') : Response.json({ reply: "ok", tools_used: [] });
  });
  if (stream) {
    await (await proxyChatStream(post("/api/assistant/chat/stream", { conversation_id: "c1", message: "close my shift" }))).text();
  } else {
    await proxyChat(post("/api/assistant/chat", { conversation_id: "c1", message: "close my shift" }));
  }
  const agentCall = calls.find((c) => c.url.startsWith("http://127.0.0.1:8781/chat"));
  assert.ok(agentCall);
  return { body: JSON.parse(String(agentCall.init?.body)) as Record<string, unknown>, calls };
}

async function run() {
  process.env.ASSISTANT_BACKEND_URL = "http://127.0.0.1:8000";
  process.env.ASSISTANT_AGENT_URL = "http://127.0.0.1:8781";
  process.env.NEXT_PUBLIC_ASSISTANT_ENABLED = "true";

  // --- open shift: fetched with the operator's own login, summarised for the agent ---------------
  const { body, calls } = await agentBody(() => Response.json(DETAIL));
  const shiftCall = calls.find((c) => c.url === "http://127.0.0.1:8000/shifts/current");
  assert.ok(shiftCall);
  assert.equal(shiftCall.init?.method ?? "GET", "GET");
  assert.deepEqual(shiftCall.init?.headers, { Cookie: "sg_session=neil-cookie" });
  assert.deepEqual(body.shift, {
    cashier_name: "Neil",
    expected_cash: "4250.00",
    open: true,
    opened_at: "2026-10-16T08:00:00Z",
    opening_cash: "1000.00",
    outstanding_unsettled_balance: "0.00",
    payment_count: 12,
    review_required_payments: 1,
    settlement_pending_sessions: 2
  });
  // Nothing that could close the shift is ever called.
  assert.ok(!calls.some((c) => c.url.includes("/close") || (c.init?.method ?? "GET") !== "GET" && c.url.includes("/shifts")));

  // --- the stream route sends the same snapshot ---------------------------------------------------
  assert.deepEqual((await agentBody(() => Response.json(DETAIL), true)).body.shift, body.shift);

  // --- no open shift → {open: false} -----------------------------------------------------------------
  assert.deepEqual((await agentBody(() => Response.json(null))).body.shift, { open: false });

  // --- errors or odd shapes → shift omitted (the agent then has no close-check tool) ---------------
  assert.ok(!("shift" in (await agentBody(() => Response.json({ detail: "nope" }, { status: 403 }))).body));
  assert.ok(!("shift" in (await agentBody(() => Response.json({ shift: "x" }))).body));
  assert.ok(!("shift" in (await agentBody(() => { throw new Error("backend down"); })).body));

  console.log("Assistant shift tests passed.");
}

void run().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});

// Phase 3 step (d): shift handover notes routes. Run: npx tsx src/tests/assistantHandover.test.ts
// fetch is stubbed; nothing touches the network, the backend or the agent.
import assert from "node:assert/strict";
import { NextRequest } from "next/server";
import { HANDOVER_TOOLS, toolChipText } from "../lib/assistant";
import { proxyHandoverList, proxyHandoverResolve } from "../lib/assistantServer";

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

function get(path: string, cookie = true): NextRequest {
  return new NextRequest(`http://127.0.0.1:3000${path}`, {
    headers: { host: "127.0.0.1:3000", ...(cookie ? { cookie: "sg_session=abc" } : {}) },
    method: "GET"
  });
}

function post(path: string, body: unknown, cookie = true): NextRequest {
  return new NextRequest(`http://127.0.0.1:3000${path}`, {
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", host: "127.0.0.1:3000", ...(cookie ? { cookie: "sg_session=abc" } : {}) },
    method: "POST"
  });
}

const me = () => Response.json({ display_name: "Neil", is_protected: false, role: "cashier", user_id: "u-neil" });

async function run() {
  process.env.ASSISTANT_BACKEND_URL = "http://127.0.0.1:8000";
  process.env.ASSISTANT_AGENT_URL = "http://127.0.0.1:8781";
  process.env.NEXT_PUBLIC_ASSISTANT_ENABLED = "true";

  // --- chips -------------------------------------------------------------------------------
  assert.equal(toolChipText("save_handover_note"), "Saved: a handover note");
  assert.equal(toolChipText("resolve_handover_note"), "Marked done: a handover note");
  assert.equal(toolChipText("list_handover_notes"), "Checked: handover notes");
  assert.ok(HANDOVER_TOOLS.has("save_handover_note") && !HANDOVER_TOOLS.has("list_handover_notes"));

  // --- list: needs a dashboard login ---------------------------------------------------------
  let calls = stubFetch(() => Response.json({}));
  let response = await proxyHandoverList(get("/api/assistant/handover", false));
  assert.equal(response.status, 401);
  assert.equal(calls.length, 0);

  calls = stubFetch((url) =>
    url.endsWith("/auth/me")
      ? me()
      : Response.json({ enabled: true, notes: [{ age: "5 minutes ago", author: "Ana", note_id: "a1b2c3d4", text: "PC 7 mouse broken" }] })
  );
  response = await proxyHandoverList(get("/api/assistant/handover"));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    enabled: true,
    notes: [{ age: "5 minutes ago", author: "Ana", note_id: "a1b2c3d4", text: "PC 7 mouse broken" }]
  });
  assert.equal(calls[1].url, "http://127.0.0.1:8781/handover");

  // Agent down -> readable error, no crash.
  stubFetch((url) => {
    if (url.endsWith("/auth/me")) {
      return me();
    }
    throw new TypeError("fetch failed");
  });
  response = await proxyHandoverList(get("/api/assistant/handover"));
  assert.equal(response.status, 502);
  assert.match(((await response.json()) as { error: string }).error, /agent service/);

  // --- resolve: resolver is the verified operator, never the browser's word -------------------
  calls = stubFetch((url) =>
    url.endsWith("/auth/me") ? me() : Response.json({ resolved: { note_id: "a1b2c3d4", resolved_by: "Neil" } })
  );
  response = await proxyHandoverResolve(
    post("/api/assistant/handover/resolve", { conversation_id: "c1", note_id: "A1B2C3D4", resolved_by: "Owner" })
  );
  assert.equal(response.status, 200);
  assert.equal(calls[1].url, "http://127.0.0.1:8781/handover/resolve");
  assert.deepEqual(JSON.parse(String(calls[1].init?.body)), { note_id: "a1b2c3d4", resolved_by: "Neil" });

  // Already done -> 409 with the agent's reason.
  stubFetch((url) =>
    url.endsWith("/auth/me") ? me() : Response.json({ detail: "That note was already marked done by Ana." }, { status: 409 })
  );
  response = await proxyHandoverResolve(post("/api/assistant/handover/resolve", { conversation_id: "c1", note_id: "a1b2c3d4" }));
  assert.equal(response.status, 409);
  assert.match(((await response.json()) as { error: string }).error, /already marked done by Ana/);

  // Bad id / not signed in -> never reaches the agent.
  calls = stubFetch((url) => (url.endsWith("/auth/me") ? me() : Response.json({})));
  response = await proxyHandoverResolve(post("/api/assistant/handover/resolve", { conversation_id: "c1", note_id: "../x" }));
  assert.equal(response.status, 400);
  assert.equal(calls.filter((c) => c.url.includes(":8781")).length, 0);
  calls = stubFetch(() => Response.json({}));
  response = await proxyHandoverResolve(post("/api/assistant/handover/resolve", { conversation_id: "c1", note_id: "a1b2c3d4" }, false));
  assert.equal(response.status, 401);
  assert.equal(calls.length, 0);

  // --- flag off -> 404 -------------------------------------------------------------------------
  delete process.env.NEXT_PUBLIC_ASSISTANT_ENABLED;
  calls = stubFetch(() => Response.json({}));
  assert.equal((await proxyHandoverList(get("/api/assistant/handover"))).status, 404);
  assert.equal(calls.length, 0);

  console.log("Assistant handover tests passed.");
}

void run().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});

// Latency fix: streaming chat route + client parser. Run: npx tsx src/tests/assistantStream.test.ts
// fetch is stubbed; nothing touches the network, the backend or the agent.
import assert from "node:assert/strict";
import { NextRequest } from "next/server";
import { liveToolText, sendAssistantMessageStream } from "../lib/assistant";
import { proxyChatStream } from "../lib/assistantServer";

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

// A stream that arrives in awkward chunks (split mid-line), like a real network.
function ndjson(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    }
  });
  return new Response(body, { headers: { "Content-Type": "application/x-ndjson" } });
}

function post(path: string, body: unknown, cookie = true): NextRequest {
  return new NextRequest(`http://127.0.0.1:3000${path}`, {
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", host: "127.0.0.1:3000", ...(cookie ? { cookie: "sg_session=abc" } : {}) },
    method: "POST"
  });
}

async function run() {
  process.env.ASSISTANT_BACKEND_URL = "http://127.0.0.1:8000";
  process.env.ASSISTANT_AGENT_URL = "http://127.0.0.1:8781";
  process.env.NEXT_PUBLIC_ASSISTANT_ENABLED = "true";

  assert.equal(liveToolText("get_today_overview"), "Checking: today's sales");
  assert.equal(liveToolText("propose_message_pc"), "Preparing: a message");
  assert.equal(liveToolText("save_handover_note"), "Saving: a handover note");

  // --- proxy: login checked, stream passed through ------------------------------------------
  let calls = stubFetch(() => Response.json({}));
  let response = await proxyChatStream(post("/api/assistant/chat/stream", { conversation_id: "c1", message: "hi" }, false));
  assert.equal(response.status, 401);
  assert.equal(calls.length, 0);

  calls = stubFetch((url) =>
    url.endsWith("/auth/me")
      ? Response.json({ display_name: "Neil", is_protected: false, role: "cashier", user_id: "u-neil" })
      : ndjson(['{"type":"start"}\n{"type":"tool","name":"get_today_overview"}\n', '{"type":"final","reply":"ok","tools_used":["get_today_overview"]}\n'])
  );
  response = await proxyChatStream(post("/api/assistant/chat/stream", { conversation_id: "c1", message: "sales?" }));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "application/x-ndjson");
  assert.match(await response.text(), /"type":"tool"/);
  assert.equal(calls[1].url, "http://127.0.0.1:8781/chat/stream");
  assert.deepEqual(JSON.parse(String(calls[1].init?.body)), {
    message: "sales?",
    operator: { display_name: "Neil", is_admin_account: false, role: "cashier" },
    session_id: "u-neil:c1"
  });

  // --- client: tool events arrive before the final answer, even split mid-line ------------------
  const seen: string[] = [];
  stubFetch(() =>
    ndjson([
      '{"type":"start"}\n{"type":"to',
      'ol","name":"get_today_overview"}\n{"type":"tool","name":"list_pcs"}\n',
      '{"type":"final","reply":"We made ₱1,250.","tools_used":["get_today_overview","list_pcs"],',
      '"pending_action":{"id":"0123456789abcdef0123456789abcdef","kind":"message_pc","summary":"s","targets":[],"details":{},"expires_in_seconds":100}}\n'
    ])
  );
  const reply = await sendAssistantMessageStream("c1", "sales?", (tool) => seen.push(tool));
  assert.deepEqual(seen, ["get_today_overview", "list_pcs"]);
  assert.equal(reply.reply, "We made ₱1,250.");
  assert.deepEqual(reply.tools_used, ["get_today_overview", "list_pcs"]);
  assert.equal(reply.pending_action?.kind, "message_pc");

  // --- client falls back to /chat if streaming isn't available ------------------------------------
  calls = stubFetch((url) =>
    url.endsWith("/chat/stream")
      ? new Response("not found", { status: 404, headers: { "Content-Type": "text/plain" } })
      : Response.json({ reply: "Fallback answer.", tools_used: [] })
  );
  const fallback = await sendAssistantMessageStream("c1", "hi", () => undefined);
  assert.equal(fallback.reply, "Fallback answer.");
  assert.deepEqual(calls.map((c) => c.url), ["/api/assistant/chat/stream", "/api/assistant/chat"]);

  // A JSON error from the stream route (e.g. not signed in) is shown, not retried.
  stubFetch(() => Response.json({ error: "Not signed in to the dashboard.", reply: "Please sign in.", tools_used: [] }, { status: 401 }));
  const unauthorized = await sendAssistantMessageStream("c1", "hi", () => undefined);
  assert.equal(unauthorized.reply, "Please sign in.");
  assert.equal(unauthorized.error, "Not signed in to the dashboard.");

  console.log("Assistant stream tests passed.");
}

void run().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});

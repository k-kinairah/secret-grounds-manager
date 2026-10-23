// Voice assistant proxy + helpers. Run: npx tsx src/tests/assistant.test.ts
// fetch is stubbed; nothing touches the network, the backend or the agent.
import assert from "node:assert/strict";
import { NextRequest } from "next/server";
import { CONVERSATION_ID_PATTERN, isAssistantEnabled, newConversationId, toolLabel } from "../lib/assistant";
import { proxyChat, proxyReset } from "../lib/assistantServer";

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

const signedIn = { cookie: "sg_session=abc123; other=1" };
const backendMe = (url: string) =>
  url.endsWith("/auth/me") ? Response.json({ role: "cashier", user_id: "user-1", username: "ana" }) : null;

async function run() {
  process.env.ASSISTANT_BACKEND_URL = "http://localhost:8000";
  process.env.ASSISTANT_AGENT_URL = "http://127.0.0.1:8781";

  // --- helpers ---------------------------------------------------------------
  assert.equal(toolLabel("get_today_overview"), "today's sales");
  assert.equal(toolLabel("some_new_tool"), "some new tool");
  for (let i = 0; i < 20; i += 1) {
    assert.match(newConversationId(), CONVERSATION_ID_PATTERN);
  }

  // --- flag off: page/route do not exist --------------------------------------
  delete process.env.NEXT_PUBLIC_ASSISTANT_ENABLED;
  assert.equal(isAssistantEnabled(), false);
  let calls = stubFetch(() => Response.json({}));
  let response = await proxyChat(request("/api/assistant/chat", { conversation_id: "c1", message: "hi" }, signedIn));
  assert.equal(response.status, 404);
  assert.equal(calls.length, 0);

  process.env.NEXT_PUBLIC_ASSISTANT_ENABLED = "true";
  assert.equal(isAssistantEnabled(), true);

  // --- not signed in -> 401, agent never called --------------------------------
  calls = stubFetch(() => Response.json({}));
  response = await proxyChat(request("/api/assistant/chat", { conversation_id: "c1", message: "hi" }));
  assert.equal(response.status, 401);
  assert.equal(calls.length, 0);

  // Backend rejects the cookie -> 401.
  calls = stubFetch(() => new Response("{}", { status: 401 }));
  response = await proxyChat(request("/api/assistant/chat", { conversation_id: "c1", message: "hi" }, signedIn));
  assert.equal(response.status, 401);
  assert.equal(calls.length, 1);
  assert.ok(calls[0].url.endsWith("/auth/me"));

  // --- cross-origin -> 403 before any backend call ------------------------------
  calls = stubFetch(() => Response.json({}));
  response = await proxyChat(
    request("/api/assistant/chat", { conversation_id: "c1", message: "hi" }, { ...signedIn, origin: "http://evil.example" })
  );
  assert.equal(response.status, 403);
  assert.equal(calls.length, 0);

  // --- invalid input -> 400 ------------------------------------------------------
  stubFetch((url) => backendMe(url) ?? Response.json({}));
  response = await proxyChat(request("/api/assistant/chat", { conversation_id: "../x", message: "hi" }, signedIn));
  assert.equal(response.status, 400);
  response = await proxyChat(request("/api/assistant/chat", { conversation_id: "c1", message: "   " }, signedIn));
  assert.equal(response.status, 400);

  // --- happy path: login checked, session scoped to user, forwarded to agent ------
  calls = stubFetch(
    (url) =>
      backendMe(url) ?? Response.json({ reply: "We made ₱1,250 today.", tools_used: ["get_today_overview"] })
  );
  response = await proxyChat(
    request(
      "/api/assistant/chat",
      { conversation_id: "c1", message: " How are we doing? " },
      { ...signedIn, origin: "http://127.0.0.1:3000" }
    )
  );
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { reply: "We made ₱1,250 today.", tools_used: ["get_today_overview"] });
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, "http://127.0.0.1:8000/auth/me"); // localhost -> IPv4 loopback
  assert.equal((calls[0].init?.headers as Record<string, string>).Cookie, "sg_session=abc123"); // only the session cookie
  assert.equal(calls[1].url, "http://127.0.0.1:8781/chat");
  assert.deepEqual(JSON.parse(String(calls[1].init?.body)), { message: "How are we doing?", session_id: "user-1:c1" });

  // Agent reports a tool/model error -> passed through for the UI.
  stubFetch(
    (url) =>
      backendMe(url) ??
      Response.json({ error: "Model call failed: AccessDenied", reply: "Sorry, I can't reach the AI model.", tools_used: [] })
  );
  response = await proxyChat(request("/api/assistant/chat", { conversation_id: "c1", message: "hi" }, signedIn));
  const body = (await response.json()) as { error?: string };
  assert.equal(response.status, 200);
  assert.match(body.error ?? "", /AccessDenied/);

  // --- agent down -> 502 with a speakable reply ----------------------------------
  stubFetch((url) => {
    const me = backendMe(url);
    if (me) {
      return me;
    }
    throw new TypeError("fetch failed");
  });
  response = await proxyChat(request("/api/assistant/chat", { conversation_id: "c1", message: "hi" }, signedIn));
  assert.equal(response.status, 502);
  const down = (await response.json()) as { reply: string; error: string; tools_used: string[] };
  assert.equal(down.reply, "The assistant service isn't running right now.");
  assert.match(down.error, /127\.0\.0\.1:8781/);
  assert.deepEqual(down.tools_used, []);

  // --- reset is scoped to the user too --------------------------------------------
  calls = stubFetch((url) => backendMe(url) ?? Response.json({ cleared: true }));
  response = await proxyReset(request("/api/assistant/reset", { conversation_id: "c1" }, signedIn));
  assert.equal(response.status, 200);
  assert.equal(calls[1].url, "http://127.0.0.1:8781/reset");
  assert.deepEqual(JSON.parse(String(calls[1].init?.body)), { session_id: "user-1:c1" });

  console.log("Assistant proxy tests passed.");
}

void run().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});

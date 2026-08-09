// Regression test for the Member Lock "Lock PC" confirmation flow in the
// customer overlay's WebView UI (client/web_ui/app.js).
//
// This is the exact bug found during the real installed-client acceptance
// test: the confirm button cleared `overlayLockConfirmVisible` immediately
// on click, before the backend "/client/session-lock" request resolved.
// The session timer refreshes the overlay every second regardless of this
// flow, and that routine push arrived first, silently discarding the
// confirm panel (and its still-in-flight request's eventual result) before
// the member ever saw an outcome -- the PC never entered the locked screen
// and no error was shown.
//
// This test loads the real app.js in a minimal sandboxed DOM and drives it
// exactly like a real WebView: click "Lock PC" -> confirm shown -> click
// the confirm "Lock PC" button -> a routine no-op state push arrives (the
// per-second timer tick) -> the confirm/pending panel must still be
// showing, not the plain overlay -- THIS is the assertion that would have
// failed before the fix. Then a failure notice arrives and must be shown
// to the member instead of being silently dropped.

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const appJsPath = path.join(__dirname, "..", "client", "web_ui", "app.js");
const source = fs.readFileSync(appJsPath, "utf8");

function makeElement(id) {
  return {
    id,
    disabled: false,
    textContent: "",
    value: "",
    type: "password",
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
    style: {},
    _listeners: {},
    addEventListener(type, fn) {
      this._listeners[type] = fn;
    },
    click() {
      const handler = this._listeners.click;
      if (handler) handler({ currentTarget: this });
    },
  };
}

function buildSandbox() {
  const emitted = [];
  let elementRegistry = new Map();
  let htmlSource = "";

  const appRoot = {
    set innerHTML(value) {
      htmlSource = value;
      elementRegistry = new Map();
    },
    get innerHTML() {
      return htmlSource;
    },
  };

  const documentStub = {
    body: { dataset: {}, getBoundingClientRect: () => ({ width: 0, height: 0 }) },
    getElementById(id) {
      if (id === "app") return appRoot;
      if (!htmlSource.includes(`id="${id}"`)) return null;
      if (!elementRegistry.has(id)) elementRegistry.set(id, makeElement(id));
      return elementRegistry.get(id);
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    addEventListener() {},
    createElement() {
      return makeElement("");
    },
  };

  const windowStub = {
    pywebview: {
      api: {
        emitAction(action, payload) {
          emitted.push({ action, payload });
        },
      },
    },
    requestAnimationFrame(fn) {
      fn();
    },
    setInterval: (...args) => setInterval(...args).unref(),
    clearInterval: (...args) => clearInterval(...args),
    addEventListener() {},
  };

  const sandbox = {
    window: windowStub,
    document: documentStub,
    app: appRoot,
    performance,
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
  };
  sandbox.window.SecretGroundsUI = undefined; // populated by app.js itself
  vm.createContext(sandbox);
  return { sandbox, emitted, getHtml: () => htmlSource };
}

function loadAppJs(sandbox) {
  vm.runInContext(source, sandbox);
}

function test_lock_pc_confirm_survives_routine_refresh_until_backend_responds() {
  const { sandbox, emitted, getHtml } = buildSandbox();
  loadAppJs(sandbox);

  const activeMemberSessionState = {
    view: "overlay",
    pc_name: "PC-01",
    session_type: "Member Time",
    customer_name: "Kinairah",
    remaining_seconds: 7200,
    elapsed_seconds: 0,
    used_seconds: 0,
    connection_state: "online",
    can_logout: true,
    can_lock_pc: true,
    notice: "",
    presentation_token: 1,
  };

  sandbox.window.SecretGroundsUI.setState(activeMemberSessionState);
  assert.ok(getHtml().includes('id="lock-pc-open"'), "active member overlay must show a Lock PC button");

  sandbox.document.getElementById("lock-pc-open").click();
  assert.ok(getHtml().includes("Lock this computer?"), "clicking Lock PC must show the confirmation panel");

  sandbox.document.getElementById("lock-pc-confirm").click();
  const lockActions = emitted.filter(entry => entry.action === "lock_pc");
  assert.strictEqual(lockActions.length, 1, "confirming must emit exactly one lock_pc action");
  assert.ok(getHtml().includes("Lock this computer?"), "confirm panel must stay visible while the request is pending");

  // The routine per-second timer tick: the backend hasn't responded to the
  // lock request yet, so this push carries no new information at all --
  // just the same session state ticking forward, exactly like every other
  // second while the overlay is open. Before the fix, this alone discarded
  // the confirm panel and silently returned to the plain active overlay.
  sandbox.window.SecretGroundsUI.setState({ ...activeMemberSessionState, remaining_seconds: 7199 });
  assert.ok(
    getHtml().includes("Lock this computer?"),
    "REGRESSION: a routine state push must not discard the pending confirm panel before the backend responds"
  );
  assert.ok(!getHtml().includes('id="lock-pc-open"'), "must not have silently fallen back to the plain active overlay");

  // The backend now actually responds -- this time with a failure, which
  // must reach the member instead of vanishing silently.
  sandbox.window.SecretGroundsUI.setState({
    ...activeMemberSessionState,
    notice: "Could not lock this PC. Please ask the cashier.",
  });
  assert.ok(!getHtml().includes("Lock this computer?"), "a definitive failure result must leave the confirm panel");
  assert.ok(
    getHtml().includes("Could not lock this PC. Please ask the cashier."),
    "the failure notice must actually be rendered to the member, not dropped"
  );
}

function test_lock_pc_confirm_does_not_resurface_after_unlock() {
  // Reproduces the exact bug found during physical verification: locking
  // succeeds (this window is hidden while locked), the member unlocks, and
  // the overlay is shown again -- the stale "Locking..." confirm panel must
  // NOT resurface on that fresh presentation.
  const { sandbox, emitted, getHtml } = buildSandbox();
  loadAppJs(sandbox);

  const baseState = {
    view: "overlay",
    pc_name: "PC-01",
    session_type: "Member Time",
    customer_name: "Kinairah",
    remaining_seconds: 7200,
    elapsed_seconds: 0,
    used_seconds: 0,
    connection_state: "online",
    can_logout: true,
    can_lock_pc: true,
    notice: "",
    presentation_token: 1,
  };

  sandbox.window.SecretGroundsUI.setState(baseState);
  sandbox.document.getElementById("lock-pc-open").click();
  sandbox.document.getElementById("lock-pc-confirm").click();
  assert.strictEqual(emitted.filter(entry => entry.action === "lock_pc").length, 1);
  assert.ok(getHtml().includes("Lock this computer?"), "confirm panel must be showing before the lock succeeds");

  // The lock succeeds: from this window's perspective nothing more arrives
  // (Python hides this window entirely -- no notice, no further push) until
  // the member unlocks and the overlay is shown again as a fresh
  // presentation_token, with the confirm/pending flow long since moot.
  sandbox.window.SecretGroundsUI.setState({ ...baseState, presentation_token: 2 });

  assert.ok(
    !getHtml().includes("Lock this computer?"),
    "REGRESSION: the stale pending confirm panel must not resurface after a lock/unlock cycle"
  );
  assert.ok(getHtml().includes('id="lock-pc-open"'), "must show the normal active overlay again after unlock");
}

function test_lock_pc_open_time_session_has_no_lock_button() {
  const { sandbox, getHtml } = buildSandbox();
  loadAppJs(sandbox);

  sandbox.window.SecretGroundsUI.setState({
    view: "overlay",
    pc_name: "PC-01",
    session_type: "Open Time",
    customer_name: "",
    remaining_seconds: null,
    elapsed_seconds: 120,
    used_seconds: 0,
    connection_state: "online",
    can_logout: true,
    can_lock_pc: false,
    notice: "",
  });
  assert.ok(!getHtml().includes('id="lock-pc-open"'), "Open Time sessions must never show a Lock PC button");
}

test_lock_pc_confirm_survives_routine_refresh_until_backend_responds();
test_lock_pc_confirm_does_not_resurface_after_unlock();
test_lock_pc_open_time_session_has_no_lock_button();
console.log("Customer lock UI smoke tests passed.");

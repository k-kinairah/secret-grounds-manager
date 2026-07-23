import assert from "node:assert/strict";
import { toCashierMessage } from "../lib/api";
import { getPcActionAvailability, getPcCardTone, summarizePcs } from "../lib/pcStatus";
import { formatDuration, minutesToSeconds } from "../lib/time";
import type { PcSummary } from "../lib/types";

function makePc(overrides: Partial<PcSummary> = {}): PcSummary {
  return {
    pc_name: "PC-01",
    ip_address: "127.0.0.1",
    connection_status: "online",
    last_seen: "2026-07-23T00:00:00+00:00",
    session_status: "idle",
    active_session_id: null,
    scheduled_end_at: null,
    remaining_seconds: null,
    ...overrides
  };
}

assert.equal(minutesToSeconds(30), 1800);
assert.equal(minutesToSeconds(0.5), 30);
assert.throws(() => minutesToSeconds(0));
assert.equal(formatDuration(90), "1m 30s");
assert.equal(formatDuration(null), "Unknown");

const offlinePc = makePc({ connection_status: "offline" });
assert.equal(getPcCardTone(offlinePc), "offline");
assert.deepEqual(getPcActionAvailability(offlinePc), {
  canStart: false,
  canAddTime: false,
  canEnd: false,
  canViewDetails: true
});

const activePc = makePc({
  session_status: "active",
  active_session_id: "session-1",
  remaining_seconds: 60
});
assert.equal(getPcCardTone(activePc), "active");
assert.equal(getPcActionAvailability(activePc).canAddTime, true);
assert.equal(getPcActionAvailability(activePc).canEnd, true);

const availablePc = makePc();
assert.equal(getPcCardTone(availablePc), "available");
assert.equal(getPcActionAvailability(availablePc).canStart, true);

assert.deepEqual(summarizePcs([availablePc, activePc, offlinePc]), {
  total: 3,
  inUse: 1,
  available: 1,
  offline: 1
});

assert.equal(
  toCashierMessage("PC-01 is offline"),
  "This PC is offline. Session actions are disabled until it reconnects."
);
assert.equal(
  toCashierMessage("Start session command failed: timeout"),
  "The client did not complete the command. Check the PC connection and try again."
);

console.log("Dashboard utility tests passed.");

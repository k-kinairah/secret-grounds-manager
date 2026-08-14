import type { ScreenMonitorStatus } from "./types";

/**
 * Pure, framework-free PC screen-monitoring logic. Kept out of the React
 * hook (useMonitorFrames) so every decision here is unit-testable with
 * plain function calls -- see src/tests/dashboard.test.ts.
 */

export function mapMonitorStatusesByPcName(
  statuses: ScreenMonitorStatus[]
): Record<string, ScreenMonitorStatus> {
  const map: Record<string, ScreenMonitorStatus> = {};
  for (const status of statuses) {
    map[status.pc_name] = status;
  }
  return map;
}

// A frame is only ever worth downloading when the station actually has one
// AND it is fresh enough to show as current (live/stale) AND the receiving
// timestamp has changed since the last successful download for this PC --
// this is what stops a redundant re-fetch every ~3s poll when nothing new
// has been captured.
export function shouldFetchFrame(
  status: ScreenMonitorStatus | undefined,
  lastFetchedReceivedAt: string | null | undefined
): boolean {
  if (!status || !status.has_frame || !status.frame_received_at) {
    return false;
  }
  if (status.frame_status !== "live" && status.frame_status !== "stale") {
    return false;
  }
  return status.frame_received_at !== lastFetchedReceivedAt;
}

export type MonitorTileVisualState = "live" | "stale" | "offline" | "no_image" | "error";

// Single source of truth for how a tile/detail panel renders monitoring
// state. frame_status already encodes live/stale/offline/no_image
// authoritatively from the server (offline takes precedence there); the
// only thing this layer adds is "error", which is purely a client-side
// concept -- the frame bytes failed to download even though the server
// says a live/stale frame exists.
export function getMonitorTileVisualState(input: {
  status: ScreenMonitorStatus | undefined;
  hasFetchError: boolean;
}): MonitorTileVisualState {
  if (!input.status) {
    return "no_image";
  }
  if (input.hasFetchError && (input.status.frame_status === "live" || input.status.frame_status === "stale")) {
    return "error";
  }
  return input.status.frame_status;
}

export interface FrameUrlEntry {
  url: string;
  receivedAt: string;
}

export type FrameUrlCache = Record<string, FrameUrlEntry>;

// Replaces (or adds) one PC's cached Blob URL, revoking whatever URL it
// previously held first. Never accumulates: at most one URL per pc_name
// exists in the returned cache at any time.
export function applyFrameUrlUpdate(
  cache: FrameUrlCache,
  pcName: string,
  receivedAt: string,
  newUrl: string,
  revoke: (url: string) => void
): FrameUrlCache {
  const previous = cache[pcName];
  if (previous) {
    revoke(previous.url);
  }
  return { ...cache, [pcName]: { url: newUrl, receivedAt } };
}

// Revokes and drops a single PC's cached URL (offline, no_image, or the
// PC disappearing from the monitor list). No-op if nothing was cached.
export function clearFrameUrl(cache: FrameUrlCache, pcName: string, revoke: (url: string) => void): FrameUrlCache {
  if (!(pcName in cache)) {
    return cache;
  }
  revoke(cache[pcName].url);
  const next = { ...cache };
  delete next[pcName];
  return next;
}

// Full teardown -- component unmount / navigating away from the page.
export function revokeAllFrameUrls(cache: FrameUrlCache, revoke: (url: string) => void): void {
  for (const entry of Object.values(cache)) {
    revoke(entry.url);
  }
}

/**
 * Guards a polling cycle against two failure modes at once:
 *  - overlap: a new run() while the previous one is still in flight is a
 *    silent no-op (mirrors DashboardClient's existing isPollingRef guard).
 *  - post-stop: once stop() is called (component unmount), run() becomes
 *    a permanent no-op and isStopped becomes true so any already-in-flight
 *    request's .then() continuation can check it before touching state.
 */
export class PollGuard {
  private isRunning = false;
  private stopped = false;

  async run(fn: () => Promise<void>): Promise<void> {
    if (this.stopped || this.isRunning) {
      return;
    }
    this.isRunning = true;
    try {
      await fn();
    } finally {
      this.isRunning = false;
    }
  }

  stop(): void {
    this.stopped = true;
  }

  get isStopped(): boolean {
    return this.stopped;
  }
}

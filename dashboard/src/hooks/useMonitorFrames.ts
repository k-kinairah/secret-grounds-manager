import { useEffect, useMemo, useRef, useState } from "react";
import { fetchMonitorFrame, fetchMonitorStatuses, getMonitorPollingIntervalMs } from "@/lib/api";
import { mapMonitorStatusesByPcName, PollGuard, shouldFetchFrame } from "@/lib/pcMonitor";
import type { ScreenMonitorStatus } from "@/lib/types";

export interface MonitorFrameState {
  statusByPcName: Record<string, ScreenMonitorStatus>;
  frameUrlByPcName: Record<string, string>;
  frameErrorByPcName: Record<string, boolean>;
}

/**
 * Polls GET /monitor/pcs on its own ~3s cycle for every station's
 * lightweight status (no images) -- this always runs, regardless of
 * selection. A screen frame is only ever downloaded for `selectedPcName`:
 * the PC grid never shows a screenshot, only the right-side details panel
 * does once a PC is selected (see PcDetailsPanel's Live Screen section).
 *
 * Switching the selection (or clearing it) aborts any in-flight frame
 * request and revokes its Blob URL before anything else happens -- see
 * the second effect below, which owns that lifecycle entirely separately
 * from the always-on status poll.
 */
export function useMonitorFrames(selectedPcName: string | null): MonitorFrameState {
  const [statusByPcName, setStatusByPcName] = useState<Record<string, ScreenMonitorStatus>>({});
  const [frameUrlByPcName, setFrameUrlByPcName] = useState<Record<string, string>>({});
  const [frameErrorByPcName, setFrameErrorByPcName] = useState<Record<string, boolean>>({});

  // Refs so both effects (and the closures they call) always see the
  // latest values without needing to be re-created on every change.
  const statusByPcNameRef = useRef<Record<string, ScreenMonitorStatus>>({});
  const selectedPcNameRef = useRef<string | null>(selectedPcName);
  const currentFrameRef = useRef<{ pcName: string; url: string } | null>(null);
  const lastFetchedReceivedAtRef = useRef<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  function revokeCurrentFrame() {
    if (currentFrameRef.current) {
      URL.revokeObjectURL(currentFrameRef.current.url);
      currentFrameRef.current = null;
      setFrameUrlByPcName({});
    }
    lastFetchedReceivedAtRef.current = null;
  }

  async function downloadSelectedFrame(pcName: string, receivedAt: string) {
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      const result = await fetchMonitorFrame(pcName, controller.signal);
      if (controller.signal.aborted || selectedPcNameRef.current !== pcName) {
        // Superseded by a newer selection (or the selection was cleared)
        // while this request was in flight -- discard it silently.
        return;
      }

      if (result.status === 404) {
        // Frame disappeared between the status poll and this fetch (e.g.
        // the station just went offline) -- treat like no_image.
        revokeCurrentFrame();
        setFrameErrorByPcName({});
        return;
      }
      if (result.blob === null) {
        setFrameErrorByPcName({ [pcName]: true });
        return;
      }

      const objectUrl = URL.createObjectURL(result.blob);
      if (currentFrameRef.current) {
        URL.revokeObjectURL(currentFrameRef.current.url);
      }
      currentFrameRef.current = { pcName, url: objectUrl };
      lastFetchedReceivedAtRef.current = receivedAt;
      setFrameUrlByPcName({ [pcName]: objectUrl });
      setFrameErrorByPcName({});
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      setFrameErrorByPcName({ [pcName]: true });
      console.error("Secret Grounds monitor frame fetch failed", { pcName, error });
    }
  }

  function syncSelectedFrame() {
    const pcName = selectedPcNameRef.current;
    if (!pcName) {
      return;
    }
    const status = statusByPcNameRef.current[pcName];
    if (shouldFetchFrame(status, lastFetchedReceivedAtRef.current)) {
      void downloadSelectedFrame(pcName, status!.frame_received_at as string);
      return;
    }
    // The selected PC no longer qualifies as live/stale-with-a-frame
    // (went offline, lost its frame) -- stop showing a stale screenshot.
    const stillShowable =
      Boolean(status?.has_frame) && (status?.frame_status === "live" || status?.frame_status === "stale");
    if (!stillShowable && currentFrameRef.current) {
      revokeCurrentFrame();
    }
  }

  // Status polling effect: always on, independent of selection. Never
  // downloads frames itself except by asking syncSelectedFrame to check
  // the (possibly unchanged) current selection after each poll.
  useEffect(() => {
    // Created fresh per effect run (not a useRef) deliberately: React
    // 18/19 StrictMode dev-mode double-invokes this effect (mount ->
    // cleanup -> mount) to catch cleanup bugs. A guard stored in a ref
    // would survive that cleanup, so the first mount's guard.stop() would
    // permanently freeze the SAME guard instance the second mount goes on
    // to reuse -- every poll after that silently no-ops forever. Scoping
    // the guard to this effect's own closure means each mount gets its
    // own guard, and only that mount's own cleanup can ever stop it.
    const guard = new PollGuard();

    async function poll() {
      let statuses: ScreenMonitorStatus[];
      try {
        statuses = await fetchMonitorStatuses();
      } catch (error) {
        // Server-unreachable/auth errors here are surfaced by the main
        // dashboard poll already (same endpoint family, same server) --
        // this loop stays quiet rather than adding a second toast source,
        // per "avoid excessive error notifications."
        console.error("Secret Grounds monitor status poll failed", error);
        return;
      }
      if (guard.isStopped) {
        return;
      }

      const nextStatusByPcName = mapMonitorStatusesByPcName(statuses);
      statusByPcNameRef.current = nextStatusByPcName;
      setStatusByPcName(nextStatusByPcName);
      syncSelectedFrame();
    }

    const intervalMs = getMonitorPollingIntervalMs();
    const initialTimeoutId = window.setTimeout(() => void guard.run(poll), 0);
    const intervalId = window.setInterval(() => void guard.run(poll), intervalMs);

    return () => {
      guard.stop();
      window.clearTimeout(initialTimeoutId);
      window.clearInterval(intervalId);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally mount-once; see StrictMode note above.
  }, []);

  // Selection-change effect: owns the selected frame's entire lifecycle,
  // separately from the status poll above. Cleanup runs on every
  // selection change AND on unmount, so switching PCs or closing the
  // details panel always aborts the in-flight request and revokes the
  // Blob URL before anything else can happen.
  useEffect(() => {
    selectedPcNameRef.current = selectedPcName;
    if (selectedPcName) {
      syncSelectedFrame();
    }
    return () => {
      abortControllerRef.current?.abort();
      revokeCurrentFrame();
      setFrameErrorByPcName({});
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- syncSelectedFrame/revokeCurrentFrame close over refs only.
  }, [selectedPcName]);

  return useMemo(
    () => ({ statusByPcName, frameUrlByPcName, frameErrorByPcName }),
    [statusByPcName, frameUrlByPcName, frameErrorByPcName]
  );
}

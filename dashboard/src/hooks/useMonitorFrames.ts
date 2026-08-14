import { useEffect, useMemo, useRef, useState } from "react";
import { fetchMonitorFrame, fetchMonitorStatuses, getMonitorPollingIntervalMs } from "@/lib/api";
import {
  applyFrameUrlUpdate,
  clearFrameUrl,
  mapMonitorStatusesByPcName,
  PollGuard,
  revokeAllFrameUrls,
  shouldFetchFrame,
  type FrameUrlCache
} from "@/lib/pcMonitor";
import type { ScreenMonitorStatus } from "@/lib/types";

export interface MonitorFrameState {
  statusByPcName: Record<string, ScreenMonitorStatus>;
  frameUrlByPcName: Record<string, string>;
  frameErrorByPcName: Record<string, boolean>;
}

/**
 * Polls GET /monitor/pcs on its own ~3s cycle (independent of the main
 * dashboard refresh) and lazily downloads each station's latest frame as
 * an authenticated Blob, exposing one shared Blob-URL cache that both the
 * PC grid tiles and the details panel read from -- so selecting a tile to
 * open its enlarged view never triggers a duplicate download of the frame
 * already showing on the tile.
 *
 * All cleanup (aborting in-flight frame requests, revoking every Blob URL,
 * clearing the interval) happens in one effect-cleanup pass on unmount.
 */
export function useMonitorFrames(): MonitorFrameState {
  const [statusByPcName, setStatusByPcName] = useState<Record<string, ScreenMonitorStatus>>({});
  const [frameUrlByPcName, setFrameUrlByPcName] = useState<FrameUrlCache>({});
  const [frameErrorByPcName, setFrameErrorByPcName] = useState<Record<string, boolean>>({});

  const guardRef = useRef<PollGuard | null>(null);
  if (guardRef.current === null) {
    guardRef.current = new PollGuard();
  }
  // Latest values available to the async poll closure without re-creating
  // the interval effect every render (mirrors DashboardClient's refresh()
  // pattern, which likewise reads via a stable callback).
  const frameUrlCacheRef = useRef<FrameUrlCache>({});
  const lastFetchedReceivedAtRef = useRef<Record<string, string>>({});
  const abortControllersRef = useRef<Record<string, AbortController>>({});

  useEffect(() => {
    const guard = guardRef.current as PollGuard;

    function revoke(url: string) {
      URL.revokeObjectURL(url);
    }

    async function downloadFrame(pcName: string, receivedAt: string) {
      abortControllersRef.current[pcName]?.abort();
      const controller = new AbortController();
      abortControllersRef.current[pcName] = controller;

      try {
        const result = await fetchMonitorFrame(pcName, controller.signal);
        if (guard.isStopped) {
          return;
        }
        delete abortControllersRef.current[pcName];

        if (result.status === 404) {
          // Frame disappeared between the status poll and this fetch
          // (e.g. the station just went offline) -- treat exactly like
          // no_image, not a fatal error.
          frameUrlCacheRef.current = clearFrameUrl(frameUrlCacheRef.current, pcName, revoke);
          delete lastFetchedReceivedAtRef.current[pcName];
          setFrameUrlByPcName(frameUrlCacheRef.current);
          setFrameErrorByPcName((previous) => (previous[pcName] ? { ...previous, [pcName]: false } : previous));
          return;
        }
        if (result.blob === null) {
          setFrameErrorByPcName((previous) => ({ ...previous, [pcName]: true }));
          return;
        }

        const objectUrl = URL.createObjectURL(result.blob);
        lastFetchedReceivedAtRef.current[pcName] = receivedAt;
        frameUrlCacheRef.current = applyFrameUrlUpdate(frameUrlCacheRef.current, pcName, receivedAt, objectUrl, revoke);
        setFrameUrlByPcName(frameUrlCacheRef.current);
        setFrameErrorByPcName((previous) => (previous[pcName] ? { ...previous, [pcName]: false } : previous));
      } catch (error) {
        if (controller.signal.aborted || guard.isStopped) {
          return;
        }
        delete abortControllersRef.current[pcName];
        setFrameErrorByPcName((previous) => ({ ...previous, [pcName]: true }));
        console.error("Secret Grounds monitor frame fetch failed", { pcName, error });
      }
    }

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
      setStatusByPcName(nextStatusByPcName);

      // A PC that no longer qualifies as live/stale-with-a-frame (went
      // offline, lost its frame, or dropped out of the list entirely)
      // must not keep showing its last screenshot.
      for (const pcName of Object.keys(frameUrlCacheRef.current)) {
        const status = nextStatusByPcName[pcName];
        const stillShowable = Boolean(status?.has_frame) && (status?.frame_status === "live" || status?.frame_status === "stale");
        if (!stillShowable) {
          frameUrlCacheRef.current = clearFrameUrl(frameUrlCacheRef.current, pcName, revoke);
          delete lastFetchedReceivedAtRef.current[pcName];
        }
      }
      setFrameUrlByPcName(frameUrlCacheRef.current);

      const downloads = Object.values(nextStatusByPcName)
        .filter((status) => shouldFetchFrame(status, lastFetchedReceivedAtRef.current[status.pc_name]))
        .map((status) => downloadFrame(status.pc_name, status.frame_received_at as string));
      await Promise.all(downloads);
    }

    const intervalMs = getMonitorPollingIntervalMs();
    const initialTimeoutId = window.setTimeout(() => void guard.run(poll), 0);
    const intervalId = window.setInterval(() => void guard.run(poll), intervalMs);

    return () => {
      guard.stop();
      window.clearTimeout(initialTimeoutId);
      window.clearInterval(intervalId);
      for (const controller of Object.values(abortControllersRef.current)) {
        controller.abort();
      }
      abortControllersRef.current = {};
      revokeAllFrameUrls(frameUrlCacheRef.current, revoke);
      frameUrlCacheRef.current = {};
      lastFetchedReceivedAtRef.current = {};
    };
  }, []);

  const frameUrlOnlyByPcName = useMemo(() => {
    const urls: Record<string, string> = {};
    for (const [pcName, entry] of Object.entries(frameUrlByPcName)) {
      urls[pcName] = entry.url;
    }
    return urls;
  }, [frameUrlByPcName]);

  return useMemo(
    () => ({ statusByPcName, frameUrlByPcName: frameUrlOnlyByPcName, frameErrorByPcName }),
    [statusByPcName, frameUrlOnlyByPcName, frameErrorByPcName]
  );
}

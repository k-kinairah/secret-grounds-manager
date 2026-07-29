"use client";

import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode
} from "react";
import { fetchLiveActivity } from "@/lib/api";
import type { OperatorActivityRecord } from "@/lib/types";
import {
  activityKey,
  buildActivityDescription,
  nextUnreadCount,
  shouldToastForActivity
} from "@/lib/activity";
import { useToast } from "./ToastProvider";
import { CloseButton } from "./CloseButton";
import styles from "@/app/page.module.css";

const activityIconByCategory: Record<string, string> = {
  operator: "OP",
  payment: "$",
  recovery: "RX",
  remote: "PC",
  session: "SS",
  settings: "ST",
  system: "SY"
};

const filterOptions = [
  { label: "All", categories: null },
  { label: "Sessions", categories: new Set(["session", "recovery"]) },
  { label: "Payments", categories: new Set(["payment"]) },
  { label: "PCs", categories: new Set(["remote"]) },
  { label: "System", categories: new Set(["system", "settings", "operator"]) }
] as const;

function formatRelativeTime(timestamp: string): string {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(timestamp).getTime()) / 1000));
  if (seconds < 60) {
    return "Just now";
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}

function useLiveActivity(limit: number, isOpen: boolean) {
  const [activity, setActivity] = useState<OperatorActivityRecord[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [unreadCount, setUnreadCount] = useState(0);
  const seenKeysRef = useRef<Set<string>>(new Set());
  const toastedKeysRef = useRef<Set<string>>(new Set());
  const hasLoadedRef = useRef(false);
  const { pushToast } = useToast();

  const loadActivity = useCallback(async () => {
    try {
      const nextActivity = await fetchLiveActivity(limit);
      const previousKeys = seenKeysRef.current;
      const nextKeys = new Set(nextActivity.map(activityKey));
      if (hasLoadedRef.current) {
        const newItems = nextActivity.filter((item) => !previousKeys.has(activityKey(item)));
        setUnreadCount((current) =>
          nextUnreadCount({
            hasLoaded: hasLoadedRef.current,
            isOpen,
            previousKeys,
            nextActivity,
            unreadCount: current
          })
        );
        for (const item of newItems) {
          const key = activityKey(item);
          if (shouldToastForActivity(item) && !toastedKeysRef.current.has(key)) {
            toastedKeysRef.current.add(key);
            pushToast(buildActivityDescription(item), item.category === "payment" ? "success" : "warning");
            break;
          }
        }
      }
      seenKeysRef.current = nextKeys;
      hasLoadedRef.current = true;
      setActivity(nextActivity);
    } catch {
      if (!hasLoadedRef.current) {
        setActivity([]);
      }
    } finally {
      setIsLoading(false);
    }
  }, [isOpen, limit, pushToast]);

  useEffect(() => {
    const firstLoad = window.setTimeout(() => void loadActivity(), 0);
    const interval = window.setInterval(() => void loadActivity(), 5000);
    return () => {
      window.clearTimeout(firstLoad);
      window.clearInterval(interval);
    };
  }, [loadActivity]);

  const clearUnread = useCallback(() => setUnreadCount(0), []);

  return { activity, clearUnread, isLoading, unreadCount };
}

const ActivityRow = memo(function ActivityRow({ activity }: { activity: OperatorActivityRecord }) {
  const icon = activityIconByCategory[activity.category] ?? "SG";
  const context = [activity.operator_name, activity.pc_name, activity.customer].filter(Boolean).join(" / ");
  return (
    <li className={styles.activityRow}>
      <span className={styles.activityIcon} aria-hidden="true">{icon}</span>
      <div className={styles.activityBody}>
        <strong>{buildActivityDescription(activity)}</strong>
        <span>
          {formatRelativeTime(activity.timestamp)}
          {context ? ` - ${context}` : ""}
        </span>
      </div>
      <span className={styles.activityChip}>{activity.result}</span>
    </li>
  );
});

const ActivityList = memo(function ActivityList({
  activity,
  compact = false,
  isLoading
}: {
  activity: OperatorActivityRecord[];
  compact?: boolean;
  isLoading: boolean;
}) {
  const visibleActivity = activity.slice(0, compact ? 8 : 60);
  return (
    <>
      <ol className={styles.activityList}>
        {visibleActivity.map((item) => (
          <ActivityRow activity={item} key={activityKey(item)} />
        ))}
      </ol>
      {!isLoading && visibleActivity.length === 0 ? (
        <p className={styles.activityEmpty}>No activity recorded yet today.</p>
      ) : null}
    </>
  );
});

export function ActivityFeed({
  compact = false,
  limit = 40,
  title = "Live Activity"
}: {
  compact?: boolean;
  limit?: number;
  title?: string;
}) {
  const { activity, isLoading } = useLiveActivity(limit, true);
  return (
    <section className={`${styles.activityPanel} ${compact ? styles.activityPanelCompact : ""}`} aria-label={title}>
      <div className={styles.activityHeader}>
        <div>
          <h2>{title}</h2>
          <p>{isLoading ? "Syncing events..." : `${Math.min(activity.length, compact ? 8 : 40)} latest events`}</p>
        </div>
        <span className={styles.liveBadge}>Live</span>
      </div>
      <ActivityList activity={activity} compact={compact} isLoading={isLoading} />
    </section>
  );
}

export function ActivityDrawerTrigger({ icon = <span aria-hidden="true">AC</span>, label = "Activity" }: { icon?: ReactNode; label?: string }) {
  const [isOpen, setIsOpen] = useState(false);
  const [activeFilter, setActiveFilter] = useState<(typeof filterOptions)[number]["label"]>("All");
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const drawerRef = useRef<HTMLElement | null>(null);
  const { activity, clearUnread, isLoading, unreadCount } = useLiveActivity(80, isOpen);
  const displayedUnread = unreadCount > 99 ? "99+" : String(unreadCount);
  const filteredActivity = useMemo(() => {
    const option = filterOptions.find((item) => item.label === activeFilter);
    if (!option?.categories) {
      return activity;
    }
    return activity.filter((item) => option.categories?.has(item.category));
  }, [activity, activeFilter]);

  const closeDrawer = useCallback(() => {
    setIsOpen(false);
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  }, []);

  const openDrawer = useCallback(() => {
    clearUnread();
    setIsOpen(true);
  }, [clearUnread]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    const focusableSelector = "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])";
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.setTimeout(() => drawerRef.current?.querySelector<HTMLElement>(focusableSelector)?.focus(), 0);
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [isOpen]);

  function handleDrawerKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeDrawer();
      return;
    }
    if (event.key !== "Tab" || drawerRef.current === null) {
      return;
    }
    const focusable = Array.from(
      drawerRef.current.querySelectorAll<HTMLElement>(
        "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])"
      )
    ).filter((element) => !element.hasAttribute("disabled"));
    if (focusable.length === 0) {
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <>
      <button
        aria-controls="activity-drawer"
        aria-expanded={isOpen}
        className={styles.activityTrigger}
        onClick={openDrawer}
        ref={triggerRef}
        type="button"
      >
        {icon}
        <span>{label}</span>
        {unreadCount > 0 ? (
          <strong aria-label={`${displayedUnread} unread activity events`}>{displayedUnread}</strong>
        ) : null}
      </button>
      {isOpen ? (
        <div className={styles.activityDrawerLayer} role="presentation">
          <button
            aria-label="Close activity drawer"
            className={styles.activityDrawerBackdrop}
            onClick={closeDrawer}
            type="button"
          />
          <section
            aria-labelledby="activity-drawer-title"
            aria-modal="true"
            className={styles.activityDrawer}
            id="activity-drawer"
            onKeyDown={handleDrawerKeyDown}
            ref={drawerRef}
            role="dialog"
          >
            <header className={styles.activityDrawerHeader}>
              <div>
                <h2 id="activity-drawer-title">Live Activity</h2>
                <p>{isLoading ? "Syncing events..." : `${activity.length} recent events`}</p>
              </div>
              <span className={styles.liveBadge}>Live</span>
              <CloseButton label="Close Live Activity" onClick={closeDrawer} />
            </header>
            <div className={styles.activityFilterRow} aria-label="Activity filters">
              {filterOptions.map((filter) => (
                <button
                  aria-pressed={filter.label === activeFilter}
                  className={filter.label === activeFilter ? styles.activityFilterActive : ""}
                  key={filter.label}
                  onClick={() => setActiveFilter(filter.label)}
                  type="button"
                >
                  {filter.label}
                </button>
              ))}
            </div>
            <ActivityList activity={filteredActivity} isLoading={isLoading} />
          </section>
        </div>
      ) : null}
    </>
  );
}

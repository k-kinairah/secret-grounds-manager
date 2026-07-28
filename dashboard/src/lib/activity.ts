import { formatMoney } from "./time";
import type { OperatorActivityRecord } from "./types";

const toastCategories = new Set(["payment", "recovery", "system"]);
const toastActions = ["Settlement", "shutdown failed", "Backup failed", "Restore"];

export function activityKey(activity: OperatorActivityRecord): string {
  return [
    activity.timestamp,
    activity.action,
    activity.operator_id ?? "system",
    activity.pc_name ?? "",
    activity.customer ?? "",
    activity.amount ?? ""
  ].join("|");
}

export function buildActivityDescription(activity: OperatorActivityRecord): string {
  const parts = [activity.action];
  if (activity.pc_name) {
    parts.push(activity.pc_name);
  }
  if (activity.customer) {
    parts.push(activity.customer);
  }
  if (activity.amount !== null) {
    parts.push(formatMoney(activity.amount));
  }
  return parts.join(" - ");
}

export function shouldToastForActivity(activity: OperatorActivityRecord): boolean {
  return toastCategories.has(activity.category) || toastActions.some((token) => activity.action.includes(token));
}

export function nextUnreadCount({
  hasLoaded,
  isOpen,
  previousKeys,
  nextActivity,
  unreadCount
}: {
  hasLoaded: boolean;
  isOpen: boolean;
  previousKeys: Set<string>;
  nextActivity: OperatorActivityRecord[];
  unreadCount: number;
}): number {
  if (!hasLoaded || isOpen) {
    return 0;
  }
  const newCount = nextActivity.filter((item) => !previousKeys.has(activityKey(item))).length;
  return unreadCount + newCount;
}

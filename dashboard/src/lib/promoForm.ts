import type {
  Promo,
  PromoAmountMode,
  PromoAppliesTo,
  PromoAudience,
  PromoInput,
  PromoStationScope,
  PromoType
} from "./types";

export interface PromoFormState {
  promoType: PromoType;
  name: string;
  audience: PromoAudience;
  amountMode: PromoAmountMode;
  price: string;
  grantedMinutes: string;
  stationScope: PromoStationScope | "";
  appliesTo: PromoAppliesTo | "";
  startsAt: string;
  endsAt: string;
  priority: string;
  perMemberLimit: string;
  totalLimit: string;
  // Time-Based Rate only. daysOfWeek uses 0=Monday .. 6=Sunday and is the
  // day the recurring window OPENS -- a window that wraps past midnight
  // stays active until its end time even though the calendar day changed.
  daysOfWeek: number[];
  windowStart: string; // "HH:MM"
  windowEnd: string; // "HH:MM"
}

export interface PromoAudienceRules {
  stationScopeRequired: boolean;
  appliesToRequired: boolean;
  allowedAmountModes: PromoAmountMode[];
  perMemberLimitAllowed: boolean;
}

// Frontend UX guidance only, mirroring server/schemas.py's
// PromoFields.validate_cross_fields -- the backend remains the sole
// authority; this only decides which fields the form shows/enables and
// which amount_mode is pre-selected. A submission that somehow bypasses
// this (or a rule that later changes server-side) is still validated for
// real by the server, and its rejection message is surfaced verbatim.
export function promoAudienceRules(audience: PromoAudience): PromoAudienceRules {
  switch (audience) {
    case "standard":
    case "vip":
      return {
        stationScopeRequired: true,
        appliesToRequired: true,
        allowedAmountModes: ["exact"],
        perMemberLimitAllowed: false
      };
    case "member_topup":
      return {
        stationScopeRequired: false,
        appliesToRequired: false,
        allowedAmountModes: ["exact", "minimum"],
        perMemberLimitAllowed: true
      };
    case "new_member":
      return {
        stationScopeRequired: false,
        appliesToRequired: false,
        allowedAmountModes: ["exact"],
        perMemberLimitAllowed: false
      };
    default:
      return {
        stationScopeRequired: false,
        appliesToRequired: false,
        allowedAmountModes: ["exact"],
        perMemberLimitAllowed: false
      };
  }
}

export function isTimeBasedRate(form: PromoFormState): boolean {
  return form.promoType === "time_based_rate";
}

const AUDIENCE_STATION_SCOPE: Record<"standard" | "vip", PromoStationScope> = {
  standard: "standard",
  vip: "vip"
};

// Applying the rules above to a form draft as the audience changes --
// clears/resets fields that are no longer applicable rather than leaving
// stale values the user can't see (station_scope/applies_to/priceless
// per-member limit for an audience that forbids them).
export function applyAudienceRules(form: PromoFormState): PromoFormState {
  const rules = promoAudienceRules(form.audience);
  const nextAmountMode = rules.allowedAmountModes.includes(form.amountMode)
    ? form.amountMode
    : rules.allowedAmountModes[0];
  return {
    ...form,
    amountMode: nextAmountMode,
    stationScope: rules.stationScopeRequired
      ? (form.stationScope || AUDIENCE_STATION_SCOPE[form.audience as "standard" | "vip"])
      : "",
    appliesTo: rules.appliesToRequired ? (form.appliesTo || "start_session") : "",
    perMemberLimit: rules.perMemberLimitAllowed ? form.perMemberLimit : ""
  };
}

// Time-Based Rate promos are Standard-only, exact-amount, and carry no
// station scope / applies-to / redemption limits -- only a recurring
// schedule + a promotional linear rate. Switching the Promo Type selector
// runs this so no stale purchase-only field is submitted.
export function applyPromoTypeRules(form: PromoFormState): PromoFormState {
  if (form.promoType === "time_based_rate") {
    return {
      ...form,
      audience: "standard",
      amountMode: "exact",
      stationScope: "",
      appliesTo: "",
      perMemberLimit: "",
      totalLimit: "",
      daysOfWeek: form.daysOfWeek.length > 0 ? form.daysOfWeek : [0, 1, 2, 3, 4, 5, 6],
      windowStart: form.windowStart || "00:00",
      windowEnd: form.windowEnd || "06:00"
    };
  }
  return applyAudienceRules(form);
}

export function emptyPromoForm(
  promoType: PromoType = "purchase",
  audience: PromoAudience = "standard"
): PromoFormState {
  return applyPromoTypeRules({
    promoType,
    name: "",
    audience,
    amountMode: "exact",
    price: promoType === "time_based_rate" ? "20.00" : "0.00",
    grantedMinutes: promoType === "time_based_rate" ? "120" : "60",
    stationScope: "",
    appliesTo: "",
    startsAt: "",
    endsAt: "",
    priority: "0",
    perMemberLimit: "",
    totalLimit: "",
    daysOfWeek: [],
    windowStart: "",
    windowEnd: ""
  });
}

function isoToLocalInputValue(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function localInputValueToIso(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    throw new Error("Enter a valid date and time.");
  }
  return date.toISOString();
}

export function promoToForm(promo: Promo): PromoFormState {
  return {
    promoType: promo.promo_type,
    name: promo.name,
    audience: promo.audience,
    amountMode: promo.amount_mode,
    price: promo.price,
    grantedMinutes: String(Math.round(promo.granted_seconds / 60)),
    stationScope: promo.station_scope ?? "",
    appliesTo: promo.applies_to ?? "",
    startsAt: isoToLocalInputValue(promo.starts_at),
    endsAt: isoToLocalInputValue(promo.ends_at),
    priority: String(promo.priority),
    perMemberLimit: promo.per_member_redemption_limit !== null ? String(promo.per_member_redemption_limit) : "",
    totalLimit: promo.total_redemption_limit !== null ? String(promo.total_redemption_limit) : "",
    daysOfWeek: promo.days_of_week ?? [],
    windowStart: promo.window_start_local_time ?? "",
    windowEnd: promo.window_end_local_time ?? ""
  };
}

const HHMM_PATTERN = /^([01]\d|2[0-3]):([0-5]\d)$/;

function timeBasedRateFormToInput(form: PromoFormState): PromoInput {
  if (!form.name.trim()) {
    throw new Error("Enter a promo name.");
  }

  const price = form.price.trim();
  if (!price || Number.isNaN(Number(price)) || Number(price) <= 0) {
    throw new Error("Enter a valid positive rate price.");
  }

  const grantedMinutes = Number(form.grantedMinutes);
  if (!Number.isFinite(grantedMinutes) || grantedMinutes <= 0) {
    throw new Error("Enter how many minutes that price should buy.");
  }

  const days = Array.from(new Set(form.daysOfWeek)).sort((a, b) => a - b);
  if (days.length === 0) {
    throw new Error("Pick at least one day of the week.");
  }
  if (days.some((day) => day < 0 || day > 6)) {
    throw new Error("Days of week must be Monday through Sunday.");
  }

  if (!HHMM_PATTERN.test(form.windowStart)) {
    throw new Error("Enter a valid start time (HH:MM).");
  }
  if (!HHMM_PATTERN.test(form.windowEnd)) {
    throw new Error("Enter a valid end time (HH:MM).");
  }
  if (form.windowStart === form.windowEnd) {
    throw new Error("Start time and end time must be different.");
  }

  if (!form.startsAt) {
    throw new Error("Enter a start date/time.");
  }
  if (!form.endsAt) {
    throw new Error("Enter an end date/time.");
  }
  const startsAt = localInputValueToIso(form.startsAt);
  const endsAt = localInputValueToIso(form.endsAt);
  if (endsAt <= startsAt) {
    throw new Error("End date/time must be later than the start date/time.");
  }

  return {
    name: form.name.trim(),
    audience: "standard",
    promo_type: "time_based_rate",
    amount_mode: "exact",
    price,
    granted_seconds: Math.round(grantedMinutes * 60),
    station_scope: null,
    applies_to: null,
    starts_at: startsAt,
    ends_at: endsAt,
    // Time-Based Rate promos have no owner-facing priority. Overlapping
    // schedules are rejected by the server, not resolved by priority, so
    // this is always 0 (the shared promos.priority column still requires
    // a value). See server/promo_time_based.py.
    priority: 0,
    per_member_redemption_limit: null,
    total_redemption_limit: null,
    days_of_week: days,
    window_start_local_time: form.windowStart,
    window_end_local_time: form.windowEnd
  };
}

export function promoFormToInput(form: PromoFormState): PromoInput {
  if (form.promoType === "time_based_rate") {
    return timeBasedRateFormToInput(form);
  }

  const rules = promoAudienceRules(form.audience);

  if (!form.name.trim()) {
    throw new Error("Enter a promo name.");
  }

  const price = form.price.trim();
  if (!price || Number.isNaN(Number(price)) || Number(price) <= 0) {
    throw new Error("Enter a valid positive price.");
  }

  const grantedMinutes = Number(form.grantedMinutes);
  if (!Number.isFinite(grantedMinutes) || grantedMinutes <= 0) {
    throw new Error("Enter a valid positive granted time.");
  }

  if (rules.stationScopeRequired && !form.stationScope) {
    throw new Error(`${form.audience} promos require a station scope.`);
  }
  if (rules.appliesToRequired && !form.appliesTo) {
    throw new Error(`${form.audience} promos require Applies To.`);
  }

  if (!form.startsAt) {
    throw new Error("Enter a start date/time.");
  }
  if (!form.endsAt) {
    throw new Error("Enter an end date/time.");
  }
  const startsAt = localInputValueToIso(form.startsAt);
  const endsAt = localInputValueToIso(form.endsAt);
  if (endsAt <= startsAt) {
    throw new Error("End date/time must be later than the start date/time.");
  }

  const priority = Number(form.priority);
  if (!Number.isInteger(priority)) {
    throw new Error("Enter a whole number for priority.");
  }

  const perMemberLimit = rules.perMemberLimitAllowed && form.perMemberLimit.trim()
    ? Number(form.perMemberLimit)
    : null;
  if (perMemberLimit !== null && (!Number.isInteger(perMemberLimit) || perMemberLimit <= 0)) {
    throw new Error("Per-member limit must be a positive whole number.");
  }

  const totalLimit = form.totalLimit.trim() ? Number(form.totalLimit) : null;
  if (totalLimit !== null && (!Number.isInteger(totalLimit) || totalLimit <= 0)) {
    throw new Error("Total redemption limit must be a positive whole number.");
  }

  return {
    name: form.name.trim(),
    audience: form.audience,
    promo_type: "purchase",
    amount_mode: form.amountMode,
    price,
    granted_seconds: Math.round(grantedMinutes * 60),
    station_scope: rules.stationScopeRequired ? (form.stationScope as PromoStationScope) : null,
    applies_to: rules.appliesToRequired ? (form.appliesTo as PromoAppliesTo) : null,
    starts_at: startsAt,
    ends_at: endsAt,
    priority,
    per_member_redemption_limit: perMemberLimit,
    total_redemption_limit: totalLimit,
    days_of_week: null,
    window_start_local_time: null,
    window_end_local_time: null
  };
}

// Which state-transition actions are meaningful to offer for a promo's
// CURRENT derived status -- not a second state machine, just a UI-level
// question of which buttons to show. server/promo_repository.py's
// transition_state remains the sole authority on whether a given
// transition is actually allowed once submitted.
export type PromoTransitionAction = "enable" | "disable" | "end" | "reactivate";

export function availableTransitionActions(promo: Promo): PromoTransitionAction[] {
  switch (promo.status) {
    case "Draft":
      return ["enable"];
    case "Scheduled":
    case "Active":
      return ["disable", "end"];
    case "Disabled":
      return ["enable", "end"];
    case "Ended":
      return ["reactivate"];
    default:
      return [];
  }
}

export function transitionActionToOwnerState(action: PromoTransitionAction): "enabled" | "disabled" | "ended" {
  switch (action) {
    case "enable":
    case "reactivate":
      return "enabled";
    case "disable":
      return "disabled";
    case "end":
      return "ended";
    default:
      return "disabled";
  }
}

export const DAY_LABELS: Array<{ value: number; short: string; long: string }> = [
  { value: 0, short: "Mon", long: "Monday" },
  { value: 1, short: "Tue", long: "Tuesday" },
  { value: 2, short: "Wed", long: "Wednesday" },
  { value: 3, short: "Thu", long: "Thursday" },
  { value: 4, short: "Fri", long: "Friday" },
  { value: 5, short: "Sat", long: "Saturday" },
  { value: 6, short: "Sun", long: "Sunday" }
];

export function formatDaysOfWeek(days: number[] | null): string {
  if (!days || days.length === 0) {
    return "—";
  }
  const sorted = Array.from(new Set(days)).sort((a, b) => a - b);
  if (sorted.length === 7) {
    return "Every day";
  }
  return sorted
    .map((day) => DAY_LABELS.find((entry) => entry.value === day)?.short ?? String(day))
    .join(", ");
}

import type {
  Promo,
  PromoAmountMode,
  PromoAppliesTo,
  PromoAudience,
  PromoInput,
  PromoStationScope
} from "./types";

export interface PromoFormState {
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

export function emptyPromoForm(audience: PromoAudience = "standard"): PromoFormState {
  return applyAudienceRules({
    name: "",
    audience,
    amountMode: "exact",
    price: "0.00",
    grantedMinutes: "60",
    stationScope: "",
    appliesTo: "",
    startsAt: "",
    endsAt: "",
    priority: "0",
    perMemberLimit: "",
    totalLimit: ""
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
    totalLimit: promo.total_redemption_limit !== null ? String(promo.total_redemption_limit) : ""
  };
}

export function promoFormToInput(form: PromoFormState): PromoInput {
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
    amount_mode: form.amountMode,
    price,
    granted_seconds: Math.round(grantedMinutes * 60),
    station_scope: rules.stationScopeRequired ? (form.stationScope as PromoStationScope) : null,
    applies_to: rules.appliesToRequired ? (form.appliesTo as PromoAppliesTo) : null,
    starts_at: startsAt,
    ends_at: endsAt,
    priority,
    per_member_redemption_limit: perMemberLimit,
    total_redemption_limit: totalLimit
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

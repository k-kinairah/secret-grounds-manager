import { minutesToSeconds } from "./time";
import type { RateProfile, RateProfileInput } from "./types";

export interface RateFormState {
  name: string;
  minutes: string;
  price: string;
  rateType: "metered" | "package";
  sortOrder: string;
}

export const emptyRateForm: RateFormState = {
  name: "",
  minutes: "30",
  price: "0.00",
  rateType: "package",
  sortOrder: "0"
};

export function rateToForm(rate: RateProfile): RateFormState {
  return {
    name: rate.name,
    minutes: String(rate.duration_seconds / 60),
    price: rate.price,
    rateType: rate.rate_type,
    sortOrder: String(rate.sort_order)
  };
}

export function rateFormToInput(form: RateFormState): RateProfileInput {
  const durationMinutes = Number(form.minutes);
  const sortOrder = Number(form.sortOrder);

  if (!form.name.trim()) {
    throw new Error("Enter a rate name.");
  }
  if (!Number.isFinite(durationMinutes) || durationMinutes <= 0) {
    throw new Error("Enter a valid positive duration.");
  }
  if (!Number.isInteger(sortOrder)) {
    throw new Error("Enter a whole number for sort order.");
  }

  return {
    name: form.name.trim(),
    duration_seconds: minutesToSeconds(durationMinutes),
    price: form.price.trim(),
    rate_type: form.rateType,
    sort_order: sortOrder
  };
}

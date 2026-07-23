import type { RateProfile } from "./types";

export function calculatePreviewSeconds(
  amount: string,
  meteredRate: RateProfile | null
): number | null {
  const parsedAmount = Number(amount);
  const ratePrice = Number(meteredRate?.price ?? 0);

  if (!meteredRate || !Number.isFinite(parsedAmount) || parsedAmount <= 0 || ratePrice <= 0) {
    return null;
  }

  return Math.floor((parsedAmount * meteredRate.duration_seconds) / ratePrice);
}

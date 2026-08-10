import type { PcSummary, RateProfile } from "./types";

// Client-side convenience filter only -- the server independently enforces
// station scope and eligibility windows on every start/add-time request
// (see server/rate_eligibility.py). This just keeps the cashier from being
// offered a choice that would be rejected. Uses the browser's local clock
// as a proxy for shop local time, which is fine for this on-premise LAN
// dashboard; the server's own check uses the configured shop timezone.
export function isRateEligibleNow(rate: RateProfile, now: Date = new Date()): boolean {
  if (!rate.eligible_start_local_time || !rate.eligible_end_local_time) {
    return true;
  }

  const [startHour, startMinute] = rate.eligible_start_local_time.split(":").map(Number);
  const [endHour, endMinute] = rate.eligible_end_local_time.split(":").map(Number);
  const startMinutes = startHour * 60 + startMinute;
  const endMinutes = endHour * 60 + endMinute;
  const nowMinutes = now.getHours() * 60 + now.getMinutes();

  if (startMinutes <= endMinutes) {
    return nowMinutes >= startMinutes && nowMinutes < endMinutes;
  }
  return nowMinutes >= startMinutes || nowMinutes < endMinutes;
}

export function getPackageRatesForPc(
  rates: RateProfile[],
  pc: Pick<PcSummary, "is_vip">,
  now: Date = new Date()
): RateProfile[] {
  const expectedScope = pc.is_vip ? "vip" : "standard";
  return rates.filter((rate) => {
    if (rate.rate_type !== "package" || !rate.is_active) {
      return false;
    }
    if (rate.station_scope !== expectedScope && rate.station_scope !== "any") {
      return false;
    }
    return isRateEligibleNow(rate, now);
  });
}

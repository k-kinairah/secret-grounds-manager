export function minutesToSeconds(minutes: number): number {
  if (!Number.isFinite(minutes) || minutes <= 0) {
    throw new Error("Minutes must be a positive number.");
  }

  return Math.round(minutes * 60);
}

export function formatDuration(totalSeconds: number | null): string {
  if (totalSeconds === null || totalSeconds < 0) {
    return "Unknown";
  }

  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }

  if (minutes > 0) {
    return `${minutes}m ${seconds}s`;
  }

  return `${seconds}s`;
}

export function formatTimer(totalSeconds: number | null): string {
  if (totalSeconds === null || totalSeconds < 0) {
    return "--:--";
  }

  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const paddedMinutes = hours > 0 ? String(minutes).padStart(2, "0") : String(minutes);
  const paddedSeconds = String(seconds).padStart(2, "0");

  if (hours > 0) {
    return `${hours}:${paddedMinutes}:${paddedSeconds}`;
  }

  return `${paddedMinutes}:${paddedSeconds}`;
}

export function formatRateDuration(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);

  if (hours > 0 && minutes > 0) {
    return `${hours} hr ${minutes} min`;
  }

  if (hours > 0) {
    return hours === 1 ? "1 hr" : `${hours} hrs`;
  }

  return `${minutes} min`;
}

export function formatNaturalDuration(totalSeconds: number | null): string {
  if (totalSeconds === null || totalSeconds < 0) {
    return "Unknown";
  }

  const seconds = Math.floor(totalSeconds);
  if (seconds < 60) {
    return `${seconds} sec`;
  }

  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);

  if (hours > 0 && minutes > 0) {
    return `${hours} hr ${minutes} min`;
  }

  if (hours > 0) {
    return `${hours} hr`;
  }

  return `${minutes} min`;
}

export function formatDateTime(value: string | null): string {
  if (value === null) {
    return "Not ended";
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}

export function formatMoney(value: string | number | null): string {
  if (value === null) {
    return "₱0.00";
  }

  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) {
    return `₱${String(value)}`;
  }

  return new Intl.NumberFormat("en-PH", {
    currency: "PHP",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    style: "currency"
  }).format(numericValue);
}

export function secondsFromScheduledEnd(
  scheduledEndAt: string | null,
  nowMs = Date.now()
): number | null {
  if (scheduledEndAt === null) {
    return null;
  }

  return Math.max(0, Math.floor((new Date(scheduledEndAt).getTime() - nowMs) / 1000));
}

export function secondsFromStartedAt(startedAt: string | null, nowMs = Date.now()): number | null {
  if (startedAt === null) {
    return null;
  }

  return Math.max(0, Math.floor((nowMs - new Date(startedAt).getTime()) / 1000));
}

function parseMoneyToCents(value: string | null): bigint {
  if (value === null) {
    return 0n;
  }

  const trimmedValue = value.trim();
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(trimmedValue);
  if (!match) {
    return 0n;
  }

  const pesos = BigInt(match[1]);
  const cents = BigInt((match[2] ?? "").padEnd(2, "0"));
  return pesos * 100n + cents;
}

function formatCents(cents: bigint): string {
  const safeCents = cents < 0n ? 0n : cents;
  const pesos = safeCents / 100n;
  const remainder = safeCents % 100n;
  return `${pesos}.${remainder.toString().padStart(2, "0")}`;
}

export interface OpenTimeEstimate {
  actualElapsedSeconds: number;
  billedSeconds: number;
  estimatedTotalAmount: string;
  estimatedBalanceDue: string;
}

export function calculateOpenTimeEstimate(
  startedAt: string | null,
  nowMs: number,
  rateDurationSeconds: number | null,
  ratePrice: string | null,
  paidAmount: string
): OpenTimeEstimate | null {
  const elapsedSeconds = secondsFromStartedAt(startedAt, nowMs);
  if (
    elapsedSeconds === null ||
    rateDurationSeconds === null ||
    rateDurationSeconds <= 0 ||
    ratePrice === null
  ) {
    return null;
  }

  const billingUnits = elapsedSeconds === 0 ? 0 : Math.ceil(elapsedSeconds / rateDurationSeconds);
  const billedSeconds = billingUnits * rateDurationSeconds;
  const estimatedTotalCents = parseMoneyToCents(ratePrice) * BigInt(billingUnits);
  const estimatedBalanceCents = estimatedTotalCents - parseMoneyToCents(paidAmount);

  return {
    actualElapsedSeconds: elapsedSeconds,
    billedSeconds,
    estimatedTotalAmount: formatCents(estimatedTotalCents),
    estimatedBalanceDue: formatCents(estimatedBalanceCents)
  };
}

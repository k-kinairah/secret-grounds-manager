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

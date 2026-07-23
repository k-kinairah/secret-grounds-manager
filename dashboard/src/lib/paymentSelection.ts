import type { PaymentMethod } from "./types";

export function parseMoneyInput(value: string): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : Number.NaN;
}

export function calculateCashChange(paymentAmount: string, cashReceived: string): number | null {
  const amount = parseMoneyInput(paymentAmount);
  const received = parseMoneyInput(cashReceived);

  if (!Number.isFinite(amount) || !Number.isFinite(received)) {
    return null;
  }

  return received - amount;
}

export function isPaymentInputValid(
  method: PaymentMethod,
  paymentAmount: string,
  balanceDue: string,
  cashReceived: string
): boolean {
  const amount = parseMoneyInput(paymentAmount);
  const balance = parseMoneyInput(balanceDue);

  if (!Number.isFinite(amount) || amount <= 0 || !Number.isFinite(balance) || amount > balance) {
    return false;
  }

  if (method === "cash") {
    const change = calculateCashChange(paymentAmount, cashReceived || paymentAmount);
    return change !== null && change >= 0;
  }

  return true;
}

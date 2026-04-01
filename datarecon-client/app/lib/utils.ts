import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import { format } from "date-fns";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(date: Date): string {
  return format(date, "dd-MMM-yyyy");
}

export function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(value);
}

export function calculateDifference(
  invoiceWeight: number,
  recordedWeight: number
): number {
  return Math.abs(invoiceWeight - recordedWeight);
}

const DEFAULT_THRESHOLD = 0.05;

export function getThreshold(): number {
  if (typeof window === "undefined") return DEFAULT_THRESHOLD;
  const stored = localStorage.getItem("reconciliation-threshold");
  return stored ? parseFloat(stored) : DEFAULT_THRESHOLD;
}

export function setThreshold(value: number): void {
  localStorage.setItem("reconciliation-threshold", value.toString());
}

export function isWithinThreshold(
  difference: number,
  totalWeight: number
): boolean {
  if (totalWeight <= 0) return true;
  const threshold = getThreshold();
  return difference / totalWeight <= threshold;
}

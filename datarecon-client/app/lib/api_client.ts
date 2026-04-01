import type {
  Vendor,
  ReconciliationResponse,
} from "@shared/schema";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8002";

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store", mode: "cors" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

export const api = {
  health: {
    async check(): Promise<{ status: string; service: string }> {
      return fetchJson(`${API_BASE_URL}/health`);
    },
  },
  vendors: {
    async list(): Promise<Vendor[]> {
      return fetchJson(`${API_BASE_URL}/v1/vendors`);
    },
  },
  reconciliation: {
    async run(params: {
      year?: number;
      month?: number;
      tolerance_pct?: number;
      vendor?: string;
      category?: string;
    }): Promise<ReconciliationResponse> {
      const searchParams = new URLSearchParams();
      if (params.year) searchParams.append("year", params.year.toString());
      if (params.month) searchParams.append("month", params.month.toString());
      if (params.tolerance_pct != null)
        searchParams.append("tolerance_pct", params.tolerance_pct.toString());
      if (params.vendor) searchParams.append("vendor", params.vendor);
      if (params.category) searchParams.append("category", params.category);
      const qs = searchParams.toString();
      return fetchJson(
        `${API_BASE_URL}/v1/reconcile${qs ? `?${qs}` : ""}`
      );
    },
  },
};

export function useApiReady() {
  // Simple health check hook — can be expanded with React Query
  return { isReady: true, isChecking: false, error: null as string | null };
}

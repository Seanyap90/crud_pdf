"use client";

import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { format } from "date-fns";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import InvoiceTable from "@/components/invoice-table";
import { queryClient } from "@/lib/queryClient";
import { api } from "@/lib/api_client";
import { getThreshold, setThreshold } from "@/lib/utils";
import type { Vendor, ReconciliationResponse } from "@shared/schema";

function Dashboard() {
  const now = new Date();
  const [selectedYear, setSelectedYear] = useState(now.getFullYear());
  const [selectedMonth, setSelectedMonth] = useState(now.getMonth() + 1);
  const [selectedVendor, setSelectedVendor] = useState<string>("all");
  const [threshold, setLocalThreshold] = useState(() => getThreshold() * 100);
  const [appliedThreshold, setAppliedThreshold] = useState(
    () => getThreshold() * 100
  );
  const [shouldFetch, setShouldFetch] = useState(false);

  const handleThresholdApply = () => {
    setThreshold(threshold / 100);
    setAppliedThreshold(threshold);
  };

  const { data: vendors = [] } = useQuery<Vendor[]>({
    queryKey: ["vendors"],
    queryFn: () => api.vendors.list(),
  });

  const {
    data: reconciliation,
    isLoading,
    refetch,
  } = useQuery<ReconciliationResponse>({
    queryKey: [
      "reconciliation",
      selectedYear,
      selectedMonth,
      selectedVendor,
      appliedThreshold,
    ],
    queryFn: () =>
      api.reconciliation.run({
        year: selectedYear,
        month: selectedMonth,
        tolerance_pct: appliedThreshold,
        vendor: selectedVendor !== "all" ? selectedVendor : undefined,
      }),
    enabled: shouldFetch,
  });

  const months = Array.from({ length: 3 }, (_, i) => {
    const date = new Date(now.getFullYear(), now.getMonth() - i, 1);
    return {
      year: date.getFullYear(),
      month: date.getMonth() + 1,
      label: format(date, "MMMM yyyy"),
    };
  });

  const handleReconcile = () => {
    setShouldFetch(true);
    refetch();
  };

  return (
    <div className="container mx-auto py-8">
      <Card>
        <CardHeader>
          <div className="flex justify-between items-center">
            <CardTitle>Data Reconciliation Dashboard</CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-4 mb-6">
            <div className="flex-1 flex flex-wrap gap-4">
              <Select
                value={`${selectedYear}-${selectedMonth}`}
                onValueChange={(val) => {
                  const [y, m] = val.split("-").map(Number);
                  setSelectedYear(y);
                  setSelectedMonth(m);
                }}
              >
                <SelectTrigger className="w-48">
                  <SelectValue placeholder="Select month" />
                </SelectTrigger>
                <SelectContent>
                  {months.map((m) => (
                    <SelectItem
                      key={`${m.year}-${m.month}`}
                      value={`${m.year}-${m.month}`}
                    >
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Select value={selectedVendor} onValueChange={setSelectedVendor}>
                <SelectTrigger className="w-48">
                  <SelectValue placeholder="Select vendor" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Vendors</SelectItem>
                  {vendors.map((vendor) => (
                    <SelectItem
                      key={vendor.vendor_id}
                      value={vendor.vendor_name}
                    >
                      {vendor.vendor_name} - {vendor.waste_type}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex items-center gap-2">
              <Label htmlFor="threshold">Threshold (%)</Label>
              <div className="flex gap-2">
                <Input
                  id="threshold"
                  type="number"
                  min="0"
                  max="100"
                  step="0.5"
                  value={threshold}
                  onChange={(e) =>
                    setLocalThreshold(parseFloat(e.target.value) || 0)
                  }
                  className="w-24"
                />
                <Button
                  variant="outline"
                  onClick={handleThresholdApply}
                  disabled={threshold === appliedThreshold}
                >
                  Apply
                </Button>
              </div>
            </div>

            <Button onClick={handleReconcile}>Reconcile</Button>
          </div>

          {/* Summary cards */}
          {reconciliation?.summary && (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
              <Card>
                <CardContent className="pt-4 pb-4">
                  <p className="text-sm text-muted-foreground">Total Items</p>
                  <p className="text-2xl font-bold">
                    {reconciliation.summary.total_line_items}
                  </p>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="pt-4 pb-4">
                  <p className="text-sm text-green-600">Within Tolerance</p>
                  <p className="text-2xl font-bold text-green-600">
                    {reconciliation.summary.within_tolerance}
                  </p>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="pt-4 pb-4">
                  <p className="text-sm text-red-600">Over Tolerance</p>
                  <p className="text-2xl font-bold text-red-600">
                    {reconciliation.summary.over_tolerance}
                  </p>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="pt-4 pb-4">
                  <p className="text-sm text-muted-foreground">
                    Invoice Weight
                  </p>
                  <p className="text-2xl font-bold">
                    {reconciliation.summary.total_invoice_weight_kg.toFixed(2)}{" "}
                    kg
                  </p>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="pt-4 pb-4">
                  <p className="text-sm text-muted-foreground">
                    Measured Weight
                  </p>
                  <p className="text-2xl font-bold">
                    {reconciliation.summary.total_measured_weight_kg.toFixed(2)}{" "}
                    kg
                  </p>
                </CardContent>
              </Card>
            </div>
          )}

          <InvoiceTable
            data={reconciliation?.line_items ?? []}
            isLoading={isLoading && shouldFetch}
            tolerancePct={appliedThreshold}
          />
        </CardContent>
      </Card>
    </div>
  );
}

export default function Home() {
  return (
    <QueryClientProvider client={queryClient}>
      <Dashboard />
    </QueryClientProvider>
  );
}

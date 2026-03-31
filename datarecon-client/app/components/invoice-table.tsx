"use client";

import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { AlertCircle, CheckCircle2, FileDown } from "lucide-react";
import { useRouter } from "next/navigation";
import { formatDate } from "@/lib/utils";
import type { ReconciliationLineItem } from "@shared/schema";

interface InvoiceTableProps {
  data: ReconciliationLineItem[];
  isLoading: boolean;
  tolerancePct: number;
}

function getStatusDisplay(status: ReconciliationLineItem["status"]) {
  switch (status) {
    case "within_tolerance":
      return (
        <div className="flex items-center text-green-600">
          <CheckCircle2 className="w-5 h-5 mr-1" />
          Within Tolerance
        </div>
      );
    case "over_tolerance":
      return (
        <div className="flex items-center text-red-600">
          <AlertCircle className="w-5 h-5 mr-1" />
          Over Tolerance
        </div>
      );
    case "invoice_only":
      return (
        <div className="flex items-center text-yellow-600">
          <AlertCircle className="w-5 h-5 mr-1" />
          Invoice Only
        </div>
      );
    case "measurement_only":
      return (
        <div className="flex items-center text-blue-600">
          <AlertCircle className="w-5 h-5 mr-1" />
          Measurement Only
        </div>
      );
  }
}

export default function InvoiceTable({
  data,
  isLoading,
  tolerancePct,
}: InvoiceTableProps) {
  const router = useRouter();

  const handleExport = () => {
    const headers = [
      "Vendor",
      "Category",
      "Invoice Weight (kg)",
      "Measured Weight (kg)",
      "Discrepancy (kg)",
      "Discrepancy (%)",
      "Status",
      "Invoice Count",
      "Measurement Count",
    ];
    const csvData = data.map((row) => [
      row.vendor_name,
      row.category,
      row.invoice_weight_kg ?? "N/A",
      row.measured_weight_kg ?? "N/A",
      row.discrepancy_kg != null ? row.discrepancy_kg.toFixed(2) : "N/A",
      row.discrepancy_pct != null ? row.discrepancy_pct.toFixed(2) : "N/A",
      row.status,
      row.invoice_count,
      row.measurement_count,
    ]);

    const csv = [
      headers.join(","),
      ...csvData.map((row) => row.join(",")),
    ].join("\n");

    const blob = new Blob([csv], { type: "text/csv" });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `reconciliation-${formatDate(new Date())}.csv`;
    a.click();
    window.URL.revokeObjectURL(url);
  };

  if (isLoading) {
    return <div className="py-8 text-center text-muted-foreground">Loading...</div>;
  }

  if (data.length === 0) {
    return (
      <div className="py-8 text-center text-muted-foreground">
        No reconciliation data available. Select a period and click Reconcile.
      </div>
    );
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <p className="text-sm text-muted-foreground">
          {data.length} line item{data.length !== 1 ? "s" : ""} | Tolerance: {tolerancePct}%
        </p>
        <Button variant="outline" onClick={handleExport}>
          <FileDown className="w-4 h-4 mr-2" />
          Export CSV
        </Button>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Vendor</TableHead>
            <TableHead>Category</TableHead>
            <TableHead className="text-right">Invoice Weight (kg)</TableHead>
            <TableHead className="text-right">Measured Weight (kg)</TableHead>
            <TableHead className="text-right">Discrepancy (kg)</TableHead>
            <TableHead className="text-right">Discrepancy (%)</TableHead>
            <TableHead>Status</TableHead>
            <TableHead></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.map((entry, idx) => (
            <TableRow key={`${entry.vendor_name}-${entry.category}-${idx}`}>
              <TableCell className="font-medium">{entry.vendor_name}</TableCell>
              <TableCell>{entry.category}</TableCell>
              <TableCell className="text-right">
                {entry.invoice_weight_kg != null
                  ? `${entry.invoice_weight_kg.toFixed(2)}`
                  : "—"}
              </TableCell>
              <TableCell className="text-right">
                {entry.measured_weight_kg != null
                  ? `${entry.measured_weight_kg.toFixed(2)}`
                  : "—"}
              </TableCell>
              <TableCell className="text-right">
                {entry.discrepancy_kg != null
                  ? `${entry.discrepancy_kg.toFixed(2)}`
                  : "—"}
              </TableCell>
              <TableCell className="text-right">
                {entry.discrepancy_pct != null
                  ? `${entry.discrepancy_pct.toFixed(2)}%`
                  : "—"}
              </TableCell>
              <TableCell>{getStatusDisplay(entry.status)}</TableCell>
              <TableCell>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    router.push(
                      `/reconciliation/${encodeURIComponent(entry.vendor_name)}?category=${encodeURIComponent(entry.category)}`
                    )
                  }
                >
                  View
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

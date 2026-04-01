"use client";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Card, CardContent } from "@/components/ui/card";
import { AlertCircle, CheckCircle2 } from "lucide-react";
import type { ReconciliationLineItem } from "@shared/schema";

interface WeightComparisonProps {
  lineItem: ReconciliationLineItem;
}

export default function WeightComparison({ lineItem }: WeightComparisonProps) {
  const statusLabel =
    lineItem.status === "within_tolerance"
      ? "Within Tolerance"
      : lineItem.status === "over_tolerance"
        ? "Over Tolerance"
        : lineItem.status === "invoice_only"
          ? "Invoice Only"
          : "Measurement Only";

  const isOk = lineItem.status === "within_tolerance";

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-8">
        {/* Measured weights by device */}
        <div>
          <h3 className="text-lg font-semibold mb-4">
            IoT Measurements ({lineItem.measurement_count} event
            {lineItem.measurement_count !== 1 ? "s" : ""})
          </h3>
          {lineItem.measurements_by_device.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Device ID</TableHead>
                  <TableHead>Gateway ID</TableHead>
                  <TableHead className="text-right">Weight (kg)</TableHead>
                  <TableHead className="text-right">Events</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {lineItem.measurements_by_device.map((device) => (
                  <TableRow key={`${device.gateway_id}-${device.device_id}`}>
                    <TableCell>{device.device_id}</TableCell>
                    <TableCell>{device.gateway_id}</TableCell>
                    <TableCell className="text-right">
                      {device.weight_kg.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right">
                      {device.event_count}
                    </TableCell>
                  </TableRow>
                ))}
                <TableRow>
                  <TableCell colSpan={2} className="font-medium">
                    Total
                  </TableCell>
                  <TableCell className="text-right font-medium">
                    {lineItem.measured_weight_kg != null
                      ? `${lineItem.measured_weight_kg.toFixed(2)} kg`
                      : "—"}
                  </TableCell>
                  <TableCell className="text-right font-medium">
                    {lineItem.measurement_count}
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          ) : (
            <p className="text-muted-foreground">No measurements recorded.</p>
          )}
        </div>

        {/* Invoice data */}
        <div>
          <h3 className="text-lg font-semibold mb-4">
            Invoice Data ({lineItem.invoice_count} invoice
            {lineItem.invoice_count !== 1 ? "s" : ""})
          </h3>
          {lineItem.invoice_weight_kg != null ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Category</TableHead>
                  <TableHead className="text-right">Total Weight (kg)</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow>
                  <TableCell>{lineItem.category}</TableCell>
                  <TableCell className="text-right">
                    {lineItem.invoice_weight_kg.toFixed(2)}
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          ) : (
            <p className="text-muted-foreground">No invoice data available.</p>
          )}
        </div>
      </div>

      {/* Summary card */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">Weight Discrepancy</p>
              <p className="text-2xl font-bold">
                {lineItem.discrepancy_kg != null
                  ? `${lineItem.discrepancy_kg.toFixed(2)} kg`
                  : "N/A"}
                {lineItem.discrepancy_pct != null && (
                  <span className="text-base font-normal text-muted-foreground ml-2">
                    ({lineItem.discrepancy_pct.toFixed(2)}%)
                  </span>
                )}
              </p>
            </div>
            <div className="flex items-center">
              {isOk ? (
                <div className="flex items-center text-green-600">
                  <CheckCircle2 className="w-6 h-6 mr-2" />
                  {statusLabel}
                </div>
              ) : (
                <div className="flex items-center text-red-600">
                  <AlertCircle className="w-6 h-6 mr-2" />
                  {statusLabel}
                </div>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

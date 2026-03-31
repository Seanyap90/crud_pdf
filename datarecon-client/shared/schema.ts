import { z } from "zod";

export const VendorSchema = z.object({
  vendor_id: z.string(),
  vendor_name: z.string(),
  waste_type: z.string(),
});

export const DeviceMeasurementSummarySchema = z.object({
  device_id: z.string(),
  gateway_id: z.string(),
  weight_kg: z.number(),
  event_count: z.number(),
});

export const ReconciliationLineItemSchema = z.object({
  vendor_name: z.string(),
  category: z.string(),
  invoice_weight_kg: z.number().nullable(),
  measured_weight_kg: z.number().nullable(),
  discrepancy_kg: z.number().nullable(),
  discrepancy_pct: z.number().nullable(),
  status: z.enum([
    "within_tolerance",
    "over_tolerance",
    "invoice_only",
    "measurement_only",
  ]),
  invoice_count: z.number(),
  measurement_count: z.number(),
  measurements_by_device: z.array(DeviceMeasurementSummarySchema),
});

export const ReconciliationSummarySchema = z.object({
  total_line_items: z.number(),
  within_tolerance: z.number(),
  over_tolerance: z.number(),
  invoice_only: z.number(),
  measurement_only: z.number(),
  total_invoice_weight_kg: z.number(),
  total_measured_weight_kg: z.number(),
});

export const ReconciliationResponseSchema = z.object({
  period: z.string(),
  tolerance_pct: z.number(),
  generated_at: z.string(),
  summary: ReconciliationSummarySchema,
  line_items: z.array(ReconciliationLineItemSchema),
});

export type Vendor = z.infer<typeof VendorSchema>;
export type DeviceMeasurementSummary = z.infer<typeof DeviceMeasurementSummarySchema>;
export type ReconciliationLineItem = z.infer<typeof ReconciliationLineItemSchema>;
export type ReconciliationSummary = z.infer<typeof ReconciliationSummarySchema>;
export type ReconciliationResponse = z.infer<typeof ReconciliationResponseSchema>;

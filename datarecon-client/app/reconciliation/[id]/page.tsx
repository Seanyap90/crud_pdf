"use client";

import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { useParams, useSearchParams, useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import WeightComparison from "@/components/weight-comparison";
import { queryClient } from "@/lib/queryClient";
import { api } from "@/lib/api_client";
import { getThreshold } from "@/lib/utils";
import type { ReconciliationResponse, ReconciliationLineItem } from "@shared/schema";
import { ArrowLeft } from "lucide-react";

function ReconciliationDetail() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();

  const vendorName = decodeURIComponent(params.id as string);
  const category = searchParams.get("category") ?? "";

  const now = new Date();
  const threshold = getThreshold() * 100;

  const { data: reconciliation, isLoading } = useQuery<ReconciliationResponse>({
    queryKey: ["reconciliation-detail", vendorName, category],
    queryFn: () =>
      api.reconciliation.run({
        year: now.getFullYear(),
        month: now.getMonth() + 1,
        tolerance_pct: threshold,
        vendor: vendorName,
        category: category || undefined,
      }),
  });

  const lineItem: ReconciliationLineItem | undefined =
    reconciliation?.line_items.find(
      (item) =>
        item.vendor_name.toLowerCase() === vendorName.toLowerCase() &&
        item.category.toLowerCase() === category.toLowerCase()
    );

  return (
    <div className="container mx-auto py-8">
      <div className="mb-4">
        <Button variant="ghost" onClick={() => router.back()}>
          <ArrowLeft className="w-4 h-4 mr-2" />
          Back to Dashboard
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            {vendorName} — {category}
          </CardTitle>
          <p className="text-sm text-muted-foreground">
            Period: {reconciliation?.period ?? "Loading..."}
          </p>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="py-8 text-center text-muted-foreground">
              Loading...
            </div>
          ) : lineItem ? (
            <WeightComparison lineItem={lineItem} />
          ) : (
            <div className="py-8 text-center text-muted-foreground">
              No matching line item found for this vendor and category.
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export default function ReconciliationDetailPage() {
  return (
    <QueryClientProvider client={queryClient}>
      <ReconciliationDetail />
    </QueryClientProvider>
  );
}

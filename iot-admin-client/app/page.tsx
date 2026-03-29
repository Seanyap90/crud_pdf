'use client';

import { useState } from 'react';
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import GatewayDashboard from "@/components/gateway-dashboard";

export default function Home() {
  // Create a client
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false,
        staleTime: Infinity,
      },
    },
  }));

  return (
    <QueryClientProvider client={queryClient}>
      <GatewayDashboard />
    </QueryClientProvider>
  );
}
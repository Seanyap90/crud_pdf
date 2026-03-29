import React, { useState } from 'react';
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Plus, Router, BarChart2 } from 'lucide-react';
import { Dialog, DialogTrigger } from "./ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { Toaster } from "./ui/toaster";
import GatewayForm from "./gateway-form";
import GatewayTable from "./gateway-table";
import { api, useApiReady } from "../lib/api_client";
import type { Gateway } from "../../shared/schema";

export default function GatewayDashboard() {
  const [open, setOpen] = useState(false);
  const { isReady, isChecking, error: apiError } = useApiReady();

  if (isChecking) {
    return (
      <div className="flex h-screen justify-center items-center bg-gray-100">
        <div className="bg-white p-8 rounded-lg shadow-lg text-center">
          <h2 className="text-xl font-bold mb-4">Connecting to API</h2>
          <div className="animate-pulse flex space-x-4 justify-center">
            <div className="rounded-full bg-blue-400 h-3 w-3"></div>
            <div className="rounded-full bg-blue-400 h-3 w-3"></div>
            <div className="rounded-full bg-blue-400 h-3 w-3"></div>
          </div>
        </div>
      </div>
    );
  }

  if (apiError) {
    return (
      <div className="flex h-screen justify-center items-center bg-gray-100">
        <div className="bg-white p-8 rounded-lg shadow-lg text-center max-w-md">
          <h2 className="text-xl font-bold text-red-600 mb-4">API Connection Error</h2>
          <p className="text-gray-700 mb-4">Unable to connect to the API server. Please check that the backend is running and try again.</p>
          <p className="text-sm text-gray-500">Error: {apiError}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-gray-100">
      {/* Sidebar */}
      <div className="w-64 bg-white shadow-lg">
        <div className="p-6">
          <h2 className="text-xl font-bold text-blue-500 mb-6">IoT Gateway Manager</h2>
          <div className="mb-3 px-2 py-1 text-xs bg-green-50 text-green-600 rounded-md border border-green-200">
            System Online
          </div>
          <nav className="space-y-2">
            <div
              className="w-full flex items-center space-x-2 px-4 py-3 rounded-lg bg-blue-500 text-white"
            >
              <Router size={20} />
              <span>Gateways</span>
            </div>
            <a
              href="/analytics"
              className="w-full flex items-center space-x-2 px-4 py-3 rounded-lg text-gray-600 hover:bg-gray-100 cursor-pointer"
            >
              <BarChart2 size={20} />
              <span>Analytics</span>
            </a>
          </nav>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-auto">
        <div className="p-8">
          <MainContent open={open} setOpen={setOpen} />
        </div>
      </div>
      <Toaster />
    </div>
  );
}

interface MainContentProps {
  open: boolean;
  setOpen: React.Dispatch<React.SetStateAction<boolean>>;
}

function MainContent({ open, setOpen }: MainContentProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const { data, isLoading, isError, error } = useQuery<Gateway[]>({
    queryKey: ["gateways"],
    queryFn: async () => {
      try {
        // Make a single API call
        const gateways = await api.gateways.list();
        
        // Log the processed data for debugging
        console.log('Fetched gateways:', gateways);
        
        // If no gateways were returned, log a warning
        if (!gateways || gateways.length === 0) {
          console.warn('No gateways returned from API');
        }
        
        return gateways;
      } catch (err) {
        console.error("Error fetching gateways:", err);
        throw err;
      }
    },
    refetchInterval: 15000, // Auto-refresh every 15 seconds for more responsive updates
  });

  const handleRefresh = () => {
    queryClient.invalidateQueries({ queryKey: ["gateways"] });
    toast({
      title: "Refreshed",
      description: "Gateway statuses have been updated",
    });
  };

  if (isError) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-6">
        <h2 className="text-red-600 text-xl font-bold mb-2">Connection Error</h2>
        <p className="text-gray-700">Unable to load gateway data. Please try again later.</p>
        <p className="text-sm text-gray-500 mt-2">Error: {error instanceof Error ? error.message : 'Unknown error'}</p>
        <button 
          onClick={handleRefresh}
          className="mt-4 px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors"
        >
          Try Again
        </button>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl shadow-lg p-6">
      {/* Dashboard Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">IoT Gateway Management</h1>
        <p className="text-sm text-gray-600">Monitor and control your IoT gateway infrastructure</p>
      </div>

      {/* Table Header */}
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-xl font-bold text-gray-800">Gateway Status</h2>
        <div className="flex gap-4">
          <button
            onClick={handleRefresh}
            className="flex items-center space-x-2 px-4 py-2 rounded-lg bg-blue-100 text-blue-600 hover:bg-blue-200 transition-colors"
          >
            <RefreshCw className="h-5 w-5" />
            <span>Refresh</span>
          </button>
          
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <button className="flex items-center space-x-2 px-4 py-2 rounded-lg bg-blue-500 text-white hover:bg-blue-600 transition-colors">
                <Plus className="h-5 w-5" />
                <span>Add Gateway</span>
              </button>
            </DialogTrigger>
            <GatewayForm onSuccess={() => setOpen(false)} />
          </Dialog>
        </div>
      </div>

      <GatewayTable gateways={data || []} isLoading={isLoading} />
    </div>
  );
}
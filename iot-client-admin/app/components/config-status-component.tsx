import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Check, X, Clock, HelpCircle, AlertTriangle, Upload } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "./ui/dialog";

interface ConfigStatusBadgeProps {
  state: string;
}

function ConfigStatusBadge({ state }: ConfigStatusBadgeProps) {
  // Define status badge styles based on state
  const getStatusStyles = () => {
    const baseClasses = "flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium";
    
    switch (state) {
      case 'completed':
        return `${baseClasses} bg-green-100 text-green-800`;
      case 'failed':
        return `${baseClasses} bg-red-100 text-red-800`;
      case 'waiting':
      case 'waiting_ack':
        return `${baseClasses} bg-yellow-100 text-yellow-800`;
      case 'notifying':
      case 'stored':
        return `${baseClasses} bg-blue-100 text-blue-800`;
      default:
        return `${baseClasses} bg-gray-100 text-gray-800`;
    }
  };
  
  // Define icon based on state
  const getIcon = () => {
    switch (state) {
      case 'completed':
        return <Check className="h-3 w-3" />;
      case 'failed':
        return <X className="h-3 w-3" />;
      case 'waiting':
      case 'waiting_ack':
        return <Clock className="h-3 w-3" />;
      case 'notifying':
        return <Upload className="h-3 w-3" />;
      case 'stored':
        return <Upload className="h-3 w-3" />;
      default:
        return <HelpCircle className="h-3 w-3" />;
    }
  };
  
  // Make display text more user-friendly
  const getDisplayText = () => {
    switch (state) {
      case 'completed':
        return 'Completed';
      case 'failed':
        return 'Failed';
      case 'waiting':
        return 'Waiting';
      case 'waiting_ack':
        return 'Waiting';
      case 'notifying':
        return 'Sending to Device';
      case 'stored':
        return 'Stored';
      default:
        // Capitalize first letter and replace underscores with spaces
        return state.charAt(0).toUpperCase() + 
               state.slice(1).replace(/_/g, ' ');
    }
  };
  
  return (
    <span className={getStatusStyles()}>
      {getIcon()}
      {getDisplayText()}
    </span>
  );
}

interface ConfigStatusProps {
  gatewayId: string;
  updateId?: string;
}

export default function ConfigStatus({ gatewayId, updateId }: ConfigStatusProps) {
  const [isDetailsOpen, setIsDetailsOpen] = useState(false);
  
  // Function to fetch config status
  // Add this function to your api_client.ts
  const fetchLatestConfig = async (gatewayId: string) => {
    try {
      const response = await fetch(`http://localhost:8000/api/config/gateway/${gatewayId}/latest`);
      if (!response.ok) {
        throw new Error('Failed to fetch configuration status');
      }
      return response.json();
    } catch (error) {
      console.error('Error fetching config status:', error);
      throw error;
    }
  };
  
  // Query for latest config
  const { data, isLoading, isError } = useQuery({
    queryKey: ['config', gatewayId],
    queryFn: () => fetchLatestConfig(gatewayId),
    enabled: !!gatewayId,
    refetchInterval: 10000, // Refetch every 10 seconds
  });
  
  if (isLoading) {
    return <span className="text-sm text-gray-500">Loading config status...</span>;
  }
  
  if (isError || !data) {
    return <span className="text-sm text-gray-500">No configuration data available</span>;
  }
  
  const handleViewDetails = () => {
    setIsDetailsOpen(true);
  };
  
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <ConfigStatusBadge state={data.state} />
        <button 
          className="text-xs text-blue-500 underline"
          onClick={handleViewDetails}
        >
          View Details
        </button>
      </div>
      
      <Dialog open={isDetailsOpen} onOpenChange={setIsDetailsOpen}>
        <DialogContent className="sm:max-w-lg bg-white">
          <DialogHeader>
            <DialogTitle>Configuration Details</DialogTitle>
            <DialogDescription>
              Information about the latest configuration for this gateway
            </DialogDescription>
          </DialogHeader>
          
          <div className="py-4 space-y-4">
            <div className="flex items-center justify-between">
              <span className="font-medium">Status:</span>
              <ConfigStatusBadge state={data.state} />
            </div>
            
            <div className="grid grid-cols-2 gap-y-2 text-sm">
              <span className="text-gray-500">Update ID:</span>
              <span className="font-mono">{data.update_id}</span>
              
              <span className="text-gray-500">Created At:</span>
              <span>{new Date(data.created_at).toLocaleString()}</span>
              
              {data.published_at && (
                <>
                  <span className="text-gray-500">Published At:</span>
                  <span>{new Date(data.published_at).toLocaleString()}</span>
                </>
              )}
              
              {data.requested_at && (
                <>
                  <span className="text-gray-500">Requested At:</span>
                  <span>{new Date(data.requested_at).toLocaleString()}</span>
                </>
              )}
              
              {data.completed_at && (
                <>
                  <span className="text-gray-500">Completed At:</span>
                  <span>{new Date(data.completed_at).toLocaleString()}</span>
                </>
              )}
              
              {data.failed_at && (
                <>
                  <span className="text-gray-500">Failed At:</span>
                  <span>{new Date(data.failed_at).toLocaleString()}</span>
                </>
              )}
            </div>
            
            {data.error && (
              <div className="mt-4 p-3 bg-red-50 border border-red-100 rounded-md">
                <div className="flex items-center gap-2 text-red-600 mb-1">
                  <AlertTriangle size={16} />
                  <span className="font-medium">Error</span>
                </div>
                <p className="text-sm text-red-700">{data.error}</p>
              </div>
            )}
            
            {data.yaml_config && (
              <div className="mt-4">
                <h4 className="font-medium mb-2">Configuration Content:</h4>
                <pre className="p-3 bg-gray-50 border border-gray-200 rounded-md text-xs overflow-auto max-h-48">
                  {data.yaml_config}
                </pre>
              </div>
            )}
          </div>
          
          <DialogFooter>
            <button
              onClick={() => setIsDetailsOpen(false)}
              className="px-4 py-2 bg-blue-500 text-white rounded-md hover:bg-blue-600"
            >
              Close
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
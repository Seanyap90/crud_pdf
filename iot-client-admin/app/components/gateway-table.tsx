import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "./ui/accordion";
import { Gateway } from "../../shared/schema";
import StatusBadge from "./status-badge";
import { Skeleton } from "./ui/skeleton";
import { useState } from "react";
import { useToast } from "@/hooks/use-toast";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "./ui/dialog";
import { Info, Server, Clock, MapPin, Activity, Trash, RefreshCw, Eye, HardDrive } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useMutation } from "@tanstack/react-query";
import { api } from "../lib/api_client";
import GatewayDetails from "./gateway-details";

interface GatewayTableProps {
  gateways: Gateway[];
  isLoading: boolean;
}

export default function GatewayTable({ gateways, isLoading }: GatewayTableProps) {
  const [selectedGateway, setSelectedGateway] = useState<string | null>(null);
  const [isAlertOpen, setIsAlertOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [selectedGatewayDetails, setSelectedGatewayDetails] = useState<Gateway | null>(null);
  const [firmwareDialogOpen, setFirmwareDialogOpen] = useState(false);
  const [selectedFirmwareGateway, setSelectedFirmwareGateway] = useState<Gateway | null>(null);
  const queryClient = useQueryClient();
  const { toast } = useToast();

  // Format date strings safely using native JavaScript
  const formatDate = (dateString?: string | null): string => {
    // Check both camelCase and snake_case property names
    if (!dateString) return "N/A";
    try {
      const date = new Date(dateString);
      return new Intl.DateTimeFormat('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: 'numeric',
        minute: 'numeric',
        hour12: true
      }).format(date);
    } catch (error) {
      console.error("Error formatting date:", dateString, error);
      return dateString;
    }
  };

  const deleteGateway = useMutation({
    mutationFn: async (id: string) => {
      return api.gateways.delete(id);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["gateways"] });
      toast({
        title: "Gateway Deleted",
        description: "The gateway has been successfully removed",
      });
    },
    onError: (error) => {
      toast({
        title: "Error",
        description: `Failed to delete gateway: ${error instanceof Error ? error.message : 'Unknown error'}`,
        variant: "destructive",
      });
    }
  });

  const resetGateway = useMutation({
    mutationFn: async (id: string) => {
      return api.gateways.reset(id);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["gateways"] });
      toast({
        title: "Gateway Reset",
        description: "The gateway connection has been reset",
      });
    },
    onError: (error) => {
      toast({
        title: "Error",
        description: `Failed to reset gateway: ${error instanceof Error ? error.message : 'Unknown error'}`,
        variant: "destructive",
      });
    }
  });

  const handleDelete = (id: string) => {
    setSelectedGateway(id);
    setIsAlertOpen(true);
  };

  const confirmDelete = () => {
    if (selectedGateway) {
      deleteGateway.mutate(selectedGateway);
    }
    setIsAlertOpen(false);
  };

  const handleReset = (id: string) => {
    resetGateway.mutate(id);
  };
  
  const handleViewDetails = (gateway: Gateway) => {
    setSelectedGatewayDetails(gateway);
    setDetailsOpen(true);
  };

  const handleViewFirmware = (gateway: Gateway) => {
    setSelectedFirmwareGateway(gateway);
    setFirmwareDialogOpen(true);
  };

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead className="bg-gray-50">
          <tr>
            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">ID</th>
            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Name</th>
            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Location</th>
            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Last Updated</th>
            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Firmware</th>
            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
            <th className="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
          </tr>
        </thead>
        <tbody className="bg-white divide-y divide-gray-200">
          {gateways.length === 0 ? (
            <tr>
              <td colSpan={7} className="px-6 py-8 text-center text-gray-500">
                No gateways found. Add a new gateway to get started.
              </td>
            </tr>
          ) : (
            gateways.map((gateway) => (
              <tr key={gateway.id} className="hover:bg-gray-50">
                <td className="px-6 py-4 whitespace-nowrap text-sm font-mono text-gray-500">
                  {gateway.id.slice(0, 8)}
                </td>
                <td className="px-6 py-4 whitespace-nowrap">
                  <Accordion type="single" collapsible className="w-full">
                    <AccordionItem value={gateway.id} className="border-none">
                      <AccordionTrigger className="py-0 text-sm font-medium text-gray-900 hover:no-underline">
                        {gateway.name}
                      </AccordionTrigger>
                      <AccordionContent className="pt-2 pb-0">
                        <div className="p-4 bg-gray-50 rounded-md space-y-3 text-sm">
                          <div className="flex items-start space-x-2">
                            <Server className="h-4 w-4 text-gray-500 mt-0.5" />
                            <div>
                              <p className="font-medium text-gray-900">Gateway Details</p>
                              <p className="text-gray-500 mt-1"><strong>Full ID:</strong> {gateway.id}</p>
                              <p className="text-gray-500"><strong>Name:</strong> {gateway.name}</p>
                              {gateway.container_id && (
                                <p className="text-gray-500"><strong>Container ID:</strong> {gateway.container_id.slice(0, 12)}</p>
                              )}
                            </div>
                          </div>
                          
                          <div className="flex items-start space-x-2">
                            <MapPin className="h-4 w-4 text-gray-500 mt-0.5" />
                            <div>
                              <p className="font-medium text-gray-900">Location Information</p>
                              <p className="text-gray-500 mt-1">{gateway.location}</p>
                            </div>
                          </div>
                          
                          <div className="flex items-start space-x-2">
                            <Activity className="h-4 w-4 text-gray-500 mt-0.5" />
                            <div>
                              <p className="font-medium text-gray-900">System Information</p>
                              {gateway.uptime && <p className="text-gray-500"><strong>Uptime:</strong> {gateway.uptime}</p>}
                              {gateway.health && <p className="text-gray-500"><strong>Health:</strong> {gateway.health}</p>}
                              <p className="text-gray-500"><strong>Status:</strong> {gateway.status}</p>
                              {gateway.error && (
                                <p className="text-red-500"><strong>Error:</strong> {gateway.error}</p>
                              )}
                            </div>
                          </div>
                          
                          <div className="flex items-start space-x-2">
                            <Clock className="h-4 w-4 text-gray-500 mt-0.5" />
                            <div>
                              <p className="font-medium text-gray-900">Activity Information</p>
                              <p className="text-gray-500 mt-1">
                                <strong>Last Updated:</strong> {formatDate(gateway.lastUpdated || gateway.last_updated)}
                              </p>
                              {gateway.connected_at && (
                                <p className="text-gray-500">
                                  <strong>Connected At:</strong> {formatDate(gateway.connected_at)}
                                </p>
                              )}
                              {gateway.disconnected_at && (
                                <p className="text-gray-500">
                                  <strong>Disconnected At:</strong> {formatDate(gateway.disconnected_at)}
                                </p>
                              )}
                            </div>
                          </div>
                        </div>
                      </AccordionContent>
                    </AccordionItem>
                  </Accordion>
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                  {gateway.location}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                  {formatDate(gateway.lastUpdated || gateway.last_updated)}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm">
                  <button
                    onClick={() => handleViewFirmware(gateway)}
                    className="flex items-center text-blue-500 hover:text-blue-700"
                  >
                    <HardDrive size={14} className="mr-1" />
                    <span className="underline">View Updates</span>
                  </button>
                </td>
                <td className="px-6 py-4 whitespace-nowrap">
                  <StatusBadge status={gateway.status} />
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-center">
                  <div className="flex justify-center space-x-3">
                    <button 
                      className="text-blue-500 hover:text-blue-700 tooltip"
                      onClick={() => handleViewDetails(gateway)}
                      title="View Details"
                    >
                      <Eye size={16} />
                    </button>
                    <button 
                      className="text-green-500 hover:text-green-700 tooltip"
                      onClick={() => handleReset(gateway.id)}
                      title="Reset Gateway"
                    >
                      <RefreshCw size={16} />
                    </button>
                    <button 
                      className="text-red-500 hover:text-red-700 tooltip"
                      onClick={() => handleDelete(gateway.id)}
                      title="Delete Gateway"
                    >
                      <Trash size={16} />
                    </button>
                  </div>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
      <div className="mt-4 text-sm text-gray-500">
        Showing {gateways.length} gateway{gateways.length !== 1 ? 's' : ''}
      </div>

      {/* Delete Confirmation Dialog */}
      <Dialog open={isAlertOpen} onOpenChange={setIsAlertOpen}>
        <DialogContent className="sm:max-w-md bg-white">
          <DialogHeader>
            <DialogTitle>Are you sure?</DialogTitle>
            <DialogDescription>
              This action will delete the gateway. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="flex justify-between mt-4">
            <button 
              onClick={() => setIsAlertOpen(false)}
              className="px-4 py-2 rounded-lg bg-gray-200 text-gray-800 hover:bg-gray-300 transition-colors"
            >
              Cancel
            </button>
            <button 
              onClick={confirmDelete} 
              className="px-4 py-2 rounded-lg bg-red-500 text-white hover:bg-red-600 transition-colors"
            >
              Delete
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      
      <GatewayDetails 
        gateway={selectedGatewayDetails} 
        isOpen={detailsOpen} 
        onClose={() => setDetailsOpen(false)} 
      />

      {/* Firmware Update Dialog */}
      <Dialog open={firmwareDialogOpen} onOpenChange={setFirmwareDialogOpen}>
        <DialogContent className="sm:max-w-lg bg-white">
          <DialogHeader>
            <DialogTitle>Firmware Updates</DialogTitle>
            <DialogDescription>
              Firmware information for {selectedFirmwareGateway?.name}
            </DialogDescription>
          </DialogHeader>

          <div className="mt-4 space-y-4">
            {/* Gateway Firmware Section */}
            <div className="border rounded-lg p-4">
              <h3 className="font-medium mb-3 flex items-center">
                <Server size={16} className="mr-2 text-blue-500" />
                Gateway Firmware
              </h3>
              
              <div className="space-y-2 text-sm">
                <div className="grid grid-cols-2 gap-2">
                  <span className="text-gray-500">Current Version:</span>
                  <span className="text-gray-700 italic">No version data</span>
                </div>
                
                <div className="grid grid-cols-2 gap-2">
                  <span className="text-gray-500">Last Updated:</span>
                  <span className="text-gray-700 italic">No update history</span>
                </div>
                
                <div className="grid grid-cols-2 gap-2">
                  <span className="text-gray-500">Firmware File:</span>
                  <span className="text-gray-700 italic">No file data</span>
                </div>
              </div>
            </div>
            
            {/* End Devices Firmware Section */}
            <div className="border rounded-lg p-4">
              <h3 className="font-medium mb-3 flex items-center">
                <HardDrive size={16} className="mr-2 text-blue-500" />
                End Devices Firmware
              </h3>
              
              <div className="bg-gray-50 p-4 rounded-md text-center text-sm text-gray-500">
                <p>No end devices connected to this gateway.</p>
                <p className="text-xs mt-1">End device firmware information will appear here when available.</p>
              </div>
            </div>
          </div>

          <DialogFooter className="mt-6">
            <button 
              onClick={() => setFirmwareDialogOpen(false)}
              className="px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600"
            >
              Close
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
import React from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "./ui/dialog";
import { Info, Server, Clock, MapPin, Activity, Cpu, Cloud, Shield, HardDrive, Settings } from "lucide-react";
import StatusBadge from "./status-badge";
import { Gateway } from "../../shared/schema";

interface GatewayDetailsProps {
  gateway: Gateway | null;
  isOpen: boolean;
  onClose: () => void;
}

export default function GatewayDetails({ gateway, isOpen, onClose }: GatewayDetailsProps) {
  if (!gateway) return null;

  // Format date strings safely
  const formatDate = (dateString?: string | null): string => {
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
      return dateString;
    }
  };

  // Helper to check if certificate is installed
  const isCertificateInstalled = gateway.certificate_info && 
    gateway.certificate_info.status === 'installed';

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-lg bg-white">
        <DialogHeader>
          <DialogTitle>Gateway Details</DialogTitle>
          <DialogDescription>
            Complete information about gateway {gateway.name}
          </DialogDescription>
        </DialogHeader>

        <div className="mt-4 space-y-6">
          {/* Header with status */}
          <div className="flex justify-between items-center pb-4 border-b">
            <div>
              <h3 className="text-lg font-bold">{gateway.name}</h3>
              <p className="text-sm text-gray-500">ID: {gateway.id}</p>
            </div>
            <StatusBadge status={gateway.status} />
          </div>
          
          {/* Basic Information */}
          <div className="space-y-1">
            <h4 className="font-medium flex items-center gap-2">
              <Server className="h-4 w-4 text-blue-500" />
              Basic Information
            </h4>
            <div className="grid grid-cols-2 gap-2 ml-6 text-sm">
              <span className="text-gray-500">Location:</span>
              <span>{gateway.location}</span>
              
              <span className="text-gray-500">Status:</span>
              <span>{gateway.status}</span>
              
              {gateway.container_id && (
                <>
                  <span className="text-gray-500">Container ID:</span>
                  <span className="font-mono text-xs">{gateway.container_id}</span>
                </>
              )}
            </div>
          </div>
          
          {/* System Metrics */}
          <div className="space-y-1">
            <h4 className="font-medium flex items-center gap-2">
              <Cpu className="h-4 w-4 text-blue-500" />
              System Metrics
            </h4>
            <div className="grid grid-cols-2 gap-2 ml-6 text-sm">
              {gateway.uptime && (
                <>
                  <span className="text-gray-500">Uptime:</span>
                  <span>{gateway.uptime}</span>
                </>
              )}
              
              {gateway.health && (
                <>
                  <span className="text-gray-500">Health:</span>
                  <span>{gateway.health}</span>
                </>
              )}
            </div>
          </div>

          {/* Certificate Information */}
          <div className="space-y-1">
            <h4 className="font-medium flex items-center gap-2">
              <Shield className="h-4 w-4 text-blue-500" />
              Certificate Status
            </h4>
            <div className="grid grid-cols-2 gap-2 ml-6 text-sm">
              <span className="text-gray-500">Status:</span>
              <span>
                {isCertificateInstalled ? (
                  <span className="text-green-600">Installed</span>
                ) : (
                  <span className="text-yellow-600">Not Installed</span>
                )}
              </span>
              
              {isCertificateInstalled && gateway.certificate_info?.installed_at && (
                <>
                  <span className="text-gray-500">Installed At:</span>
                  <span>{formatDate(gateway.certificate_info.installed_at)}</span>
                </>
              )}
            </div>
          </div>
          
          {/* Firmware Information */}
          <div className="space-y-1">
            <h4 className="font-medium flex items-center gap-2">
              <HardDrive className="h-4 w-4 text-blue-500" />
              Firmware Information
            </h4>
            <div className="ml-6 text-sm">
              {gateway.firmware ? (
                <div className="grid grid-cols-2 gap-2">
                  <span className="text-gray-500">Version:</span>
                  <span>{gateway.firmware.version || "Unknown"}</span>
                  
                  <span className="text-gray-500">Last Updated:</span>
                  <span>{gateway.firmware.lastUpdated ? formatDate(gateway.firmware.lastUpdated) : "Never"}</span>
                  
                  <span className="text-gray-500">File:</span>
                  <span>{gateway.firmware.file || "No file information"}</span>
                </div>
              ) : (
                <p className="text-gray-500 italic">No firmware information available</p>
              )}
            </div>
          </div>
          
          {/* Connection Timestamps */}
          <div className="space-y-1">
            <h4 className="font-medium flex items-center gap-2">
              <Clock className="h-4 w-4 text-blue-500" />
              Connection Timeline
            </h4>
            <div className="grid grid-cols-2 gap-2 ml-6 text-sm">
              <span className="text-gray-500">Last Updated:</span>
              <span>{formatDate(gateway.lastUpdated || gateway.last_updated)}</span>
              
              {gateway.created_at && (
                <>
                  <span className="text-gray-500">Created At:</span>
                  <span>{formatDate(gateway.created_at)}</span>
                </>
              )}
              
              {gateway.connected_at && (
                <>
                  <span className="text-gray-500">Connected At:</span>
                  <span>{formatDate(gateway.connected_at)}</span>
                </>
              )}
              
              {gateway.disconnected_at && (
                <>
                  <span className="text-gray-500">Disconnected At:</span>
                  <span>{formatDate(gateway.disconnected_at)}</span>
                </>
              )}
              
              {gateway.deleted_at && (
                <>
                  <span className="text-gray-500">Deleted At:</span>
                  <span>{formatDate(gateway.deleted_at)}</span>
                </>
              )}
            </div>
          </div>
          
          {/* Error Information */}
          {gateway.error && (
            <div className="space-y-1">
              <h4 className="font-medium flex items-center gap-2 text-red-500">
                <Info className="h-4 w-4" />
                Error Information
              </h4>
              <div className="ml-6 p-2 bg-red-50 border border-red-100 rounded text-sm text-red-800">
                {gateway.error}
              </div>
            </div>
          )}
        </div>

        <DialogFooter className="mt-6">
          <button 
            onClick={onClose}
            className="px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600"
          >
            Close
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
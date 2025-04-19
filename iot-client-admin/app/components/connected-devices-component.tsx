import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Laptop } from 'lucide-react';
import { Skeleton } from './ui/skeleton';

interface ConnectedDevicesProps {
  gateway_id: string;
}

interface EndDevice {
  device_id: string;
  gateway_id: string;
  device_type: string;
  name?: string;
  location?: string;
  status: string;
  last_updated: string;
  last_measurement?: string;
}

export default function ConnectedDevices({ gateway_id }: ConnectedDevicesProps) {
  // Function to fetch devices from backend
  const { data: devices, isLoading, error } = useQuery({
    queryKey: ['devices', gateway_id],
    queryFn: async () => {
      try {
        const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
        const response = await fetch(`${API_BASE_URL}/api/devices?gateway_id=${gateway_id}`);
        
        if (!response.ok) {
          throw new Error('Failed to fetch connected devices');
        }
        
        return await response.json();
      } catch (error) {
        console.error('Error fetching devices:', error);
        throw error;
      }
    },
    enabled: !!gateway_id,
    refetchInterval: 30000, // Refresh every 30 seconds
  });

  if (isLoading) {
    return <Skeleton className="h-4 w-24" />;
  }

  if (error || !devices) {
    return (
      <div className="flex items-center text-gray-500 text-sm">
        <Laptop size={16} className="mr-2" />
        <span>No connected devices</span>
      </div>
    );
  }

  // Count online/connected devices
  const onlineDevices = Array.isArray(devices) 
    ? devices.filter(device => device.status === 'online')
    : [];
  
  const totalDevices = Array.isArray(devices) ? devices.length : 0;

  if (totalDevices === 0) {
    return (
      <div className="flex items-center text-gray-500 text-sm">
        <Laptop size={16} className="mr-2" />
        <span>No end devices registered</span>
      </div>
    );
  }

  return (
    <div className="flex items-center text-gray-700 text-sm">
      <Laptop size={16} className="mr-2" />
      <span>
        <strong>{onlineDevices.length}</strong> online / <strong>{totalDevices}</strong> total
        {onlineDevices.length === 0 && totalDevices > 0 && (
          <span className="ml-2 text-amber-600 text-xs">(All devices offline)</span>
        )}
      </span>
    </div>
  );
}
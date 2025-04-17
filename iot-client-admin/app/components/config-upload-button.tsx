import React, { useState } from 'react';
import { Upload } from 'lucide-react';
import { Dialog, DialogTrigger } from './ui/dialog';
import DeviceConfigForm from './device-config-form';
import type { Gateway } from '../../shared/schema';

interface ConfigUploadButtonProps {
  gateway: Gateway;
  onSuccess?: () => void;
}

export default function ConfigUploadButton({ gateway, onSuccess }: ConfigUploadButtonProps) {
  const [isDialogOpen, setIsDialogOpen] = useState(false);

  const handleSuccess = () => {
    if (onSuccess) onSuccess();
    setIsDialogOpen(false);
  };

  return (
    <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
      <DialogTrigger asChild>
        <button 
          className="flex items-center gap-1 text-sm px-3 py-1.5 bg-blue-100 text-blue-700 rounded-md hover:bg-blue-200 transition-colors"
          disabled={gateway.status !== 'connected' && gateway.status !== 'online'}
          title={gateway.status !== 'connected' && gateway.status !== 'online' ? 
            "Gateway must be connected to upload configurations" : 
            "Upload device configuration"}
        >
          <Upload size={14} />
          <span>Upload End Device Config</span>
        </button>
      </DialogTrigger>
      <DeviceConfigForm gateway={gateway} onSuccess={handleSuccess} />
    </Dialog>
  );
}
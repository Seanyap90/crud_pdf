import { useState, useRef, useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "./ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { Upload, AlertCircle, FileText, Tag, Laptop } from "lucide-react";
import { api } from "../lib/api_client";
import type { Gateway } from "../../shared/schema";

interface DeviceConfigFormProps {
  gateway: Gateway;
  onSuccess: () => void;
}

export default function DeviceConfigForm({ gateway, onSuccess }: DeviceConfigFormProps) {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [configVersion, setConfigVersion] = useState<string | null>(null);
  const [deviceCount, setDeviceCount] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { toast } = useToast();
  const queryClient = useQueryClient();

  // Parse YAML file when selected
  useEffect(() => {
    if (selectedFile) {
      const reader = new FileReader();
      reader.onload = (e) => {
        const content = e.target?.result as string;
        setFileContent(content);
        
        try {
          // Extract version
          const versionMatch = content.match(/version:\s*['"]?([0-9.]+)['"]?/);
          if (versionMatch && versionMatch[1]) {
            setConfigVersion(versionMatch[1]);
          } else {
            setConfigVersion(null);
          }
          
          // Extract device count
          const countMatch = content.match(/devices:\s*\n\s*count:\s*([0-9]+)/);
          if (countMatch && countMatch[1]) {
            setDeviceCount(parseInt(countMatch[1], 10));
          } else {
            setDeviceCount(null);
          }
        } catch (err) {
          console.error("Error parsing YAML:", err);
        }
      };
      reader.readAsText(selectedFile);
    }
  }, [selectedFile]);

  // Add this function to api_client.ts if not already present
  const uploadConfig = async (gatewayId: string, file: File) => {
    const formData = new FormData();
    formData.append('gateway_id', gatewayId);
    formData.append('file', file);
    
    const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
    const response = await fetch(`${API_BASE_URL}/api/config`, {
      method: 'POST',
      body: formData,
      mode: 'cors',
    });
    
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Configuration upload failed: ${response.status} - ${errorText}`);
    }
    
    return response.json();
  };

  const mutation = useMutation({
    mutationFn: async () => {
      if (!selectedFile) {
        throw new Error("Please select a configuration file");
      }
      
      // Check file extension
      const fileExtension = selectedFile.name.split('.').pop()?.toLowerCase();
      if (fileExtension !== 'yaml' && fileExtension !== 'yml') {
        throw new Error("Only YAML files (.yaml, .yml) are supported");
      }
      
      return uploadConfig(gateway.id, selectedFile);
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["gateways"] });
      queryClient.invalidateQueries({ queryKey: ["config", gateway.id] });
      
      toast({
        title: "Configuration Uploaded",
        description: `Update ID: ${data.update_id}`,
      });
      onSuccess();
    },
    onError: (error) => {
      setError(error instanceof Error ? error.message : 'Unknown error');
      toast({
        title: "Upload Failed",
        description: error instanceof Error ? error.message : 'Unknown error',
        variant: "destructive",
      });
    },
  });

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setError(null);
    const files = e.target.files;
    if (files && files.length > 0) {
      setSelectedFile(files[0]);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    try {
      await mutation.mutateAsync();
    } catch (error) {
      console.error("Error uploading configuration:", error);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <DialogContent className="sm:max-w-md bg-white">
      <DialogHeader>
        <DialogTitle>Upload Device Configuration</DialogTitle>
        <DialogDescription>
          Upload a configuration file for devices connected to {gateway.name}
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={handleSubmit} className="space-y-4 py-4">
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="block text-sm font-medium text-gray-700">Configuration File (YAML)</label>
            <span className="text-xs text-blue-500">Gateway ID: {gateway.id}</span>
          </div>
          
          <div className="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center">
            <input
              type="file"
              id="config-file"
              ref={fileInputRef}
              className="hidden"
              accept=".yaml,.yml"
              onChange={handleFileChange}
            />
            <label 
              htmlFor="config-file" 
              className="flex flex-col items-center justify-center cursor-pointer"
            >
              <Upload className="h-10 w-10 text-blue-500 mb-3" />
              {selectedFile ? (
                <span className="text-blue-500 font-medium">{selectedFile.name}</span>
              ) : (
                <>
                  <span className="text-gray-700 font-medium">Click to select a YAML file</span>
                  <span className="text-xs text-gray-500 mt-1">or drag and drop</span>
                </>
              )}
            </label>
          </div>
          
          {/* Show extracted configuration details */}
          {selectedFile && (
            <div className="mt-3 bg-blue-50 p-3 rounded-md border border-blue-100">
              <h4 className="text-sm font-medium flex items-center gap-1 text-blue-700">
                <FileText size={16} />
                Configuration Details
              </h4>
              <div className="mt-2 text-xs space-y-1">
                <div className="flex items-center gap-1">
                  <Tag size={12} className="text-blue-500" />
                  <span className="text-gray-600">Version:</span>
                  <span className="font-medium">{configVersion || 'Unknown'}</span>
                </div>
                {deviceCount !== null && (
                  <div className="flex items-center gap-1">
                    <Laptop size={12} className="text-blue-500" />
                    <span className="text-gray-600">End Devices:</span>
                    <span className="font-medium">{deviceCount}</span>
                  </div>
                )}
              </div>
            </div>
          )}
          
          {error && (
            <div className="text-sm text-red-500 flex items-center gap-1">
              <AlertCircle size={14} />
              <span>{error}</span>
            </div>
          )}
          
          <p className="text-xs text-gray-500">
            Upload a YAML configuration file for end devices connected to this gateway.
            Only .yaml or .yml files are supported.
          </p>
        </div>

        <DialogFooter>
          <button
            type="submit"
            className="px-4 py-2 rounded-lg font-medium transition-colors duration-300 
                      bg-blue-500 text-white hover:bg-blue-700 
                      disabled:opacity-50 disabled:cursor-not-allowed"
            disabled={isSubmitting || !selectedFile}
          >
            {isSubmitting ? "Uploading..." : "Upload Configuration"}
          </button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
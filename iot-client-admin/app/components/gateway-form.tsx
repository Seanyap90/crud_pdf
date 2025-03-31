"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "./ui/dialog";
import { Input } from "./ui/input";
import { useToast } from "@/hooks/use-toast";
import { Download, Info } from "lucide-react";
import type { InsertGateway } from "../../shared/schema";
import { api } from "../lib/api_client";

interface GatewayFormProps {
  onSuccess: () => void;
}

export default function GatewayForm({ onSuccess }: GatewayFormProps) {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formData, setFormData] = useState<InsertGateway>({
    name: "",
    location: "",
  });
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: async (data: InsertGateway) => {
      return api.gateways.create(data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["gateways"] });
      toast({
        title: "Success",
        description: "Gateway created successfully",
      });
      onSuccess();
    },
    onError: (error) => {
      toast({
        title: "Error",
        description: `Failed to create gateway: ${error instanceof Error ? error.message : 'Unknown error'}`,
        variant: "destructive",
      });
    },
  });

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    try {
      await mutation.mutateAsync(formData);
    } catch (error) {
      console.error("Error submitting form:", error);
    } finally {
      setIsSubmitting(false);
    }
  };

  // Placeholder function for certificate download (currently disabled)
  const handleCertificateDownload = () => {
    toast({
      title: "Feature Not Available",
      description: "Certificate download functionality is coming soon.",
    });
  };

  return (
    <DialogContent className="sm:max-w-md bg-white">
      <DialogHeader>
        <DialogTitle className="text-xl font-bold text-gray-900">Add New Gateway</DialogTitle>
        <DialogDescription className="text-gray-500">
          Enter the details for the new gateway device.
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={handleSubmit} className="space-y-4 mt-4">
        <div className="space-y-2">
          <label className="block text-sm font-medium text-gray-700">Gateway Name</label>
          <Input
            name="name"
            value={formData.name}
            onChange={handleChange}
            className="w-full px-4 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500" 
            placeholder="Enter gateway name"
            required
          />
        </div>

        <div className="space-y-2">
          <label className="block text-sm font-medium text-gray-700">Location</label>
          <Input
            name="location"
            value={formData.location}
            onChange={handleChange}
            className="w-full px-4 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            placeholder="Enter gateway location"
            required
          />
        </div>

        {/* Certificate Section */}
        <div className="pt-4 border-t border-gray-200">
          <div className="flex items-start space-x-2">
            <Info size={16} className="text-blue-500 mt-1" />
            <div className="flex-1">
              <h4 className="text-sm font-medium text-gray-900">Device Certificates</h4>
              <p className="text-xs text-gray-500 mt-1">
                Security certificates are required for encrypted communication with the gateway.
              </p>
            </div>
          </div>
          
          <button
            type="button"
            onClick={handleCertificateDownload}
            disabled={true}
            className="mt-3 flex items-center px-3 py-2 text-sm border border-gray-300 rounded-lg text-gray-500 bg-gray-100 cursor-not-allowed"
          >
            <Download size={16} className="mr-2" />
            Download Certificate Package
          </button>
          
          <div className="mt-2 text-xs text-amber-600 flex items-center">
            <Info size={12} className="mr-1" />
            Certificate download is not available yet
          </div>
        </div>

        <button
          type="submit"
          className="w-full px-5 py-3 rounded-lg font-semibold transition-colors duration-300 
                     bg-blue-500 text-white font-bold hover:bg-blue-700 
                     disabled:opacity-50 disabled:cursor-not-allowed"
          disabled={isSubmitting}
        >
          {isSubmitting ? "Creating..." : "Create Gateway"}
        </button>
      </form>
    </DialogContent>
  );
}
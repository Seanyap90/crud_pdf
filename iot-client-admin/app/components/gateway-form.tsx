"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { Input } from "./ui/input";
import { useToast } from "@/hooks/use-toast";
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

  return (
    <DialogContent className="sm:max-w-md bg-white">
      <DialogHeader>
        <DialogTitle className="text-xl font-bold text-gray-900">Add New Gateway</DialogTitle>
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
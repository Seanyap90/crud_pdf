'use client';

import { useState, useEffect } from 'react';
import type { 
  Invoice, 
  InvoiceListResponse, 
  InvoiceResponse,
  InvoiceUploadParams,
  InvoiceUploadResponse,
  VendorStatistics,
  CategoryStatistics,
  InvoiceFilters,
  HealthResponse
} from '../types/invoice';

// Base API URL
// Production API Gateway URL
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'https://s8vzo1ia2k.execute-api.us-east-1.amazonaws.com/dev';
// Local development URL (commented out)
//const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

/**
 * API client for FastAPI backend
 */
class ApiClient {
  private baseUrl: string;
  
  constructor(baseUrl = API_BASE_URL) {
    this.baseUrl = baseUrl;
  }

  /**
   * Check health status of the backend API
   */
  async checkHealth() {
    try {
      const response = await fetch(`${this.baseUrl}/health`, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
        cache: 'no-store',
      });
      
      if (!response.ok) {
        throw new Error(`Health check failed with status: ${response.status}`);
      }
      
      return await response.json();
    } catch (error) {
      throw error;
    }
  }

  /**
   * Make a GET request
   */
  async get(path: string, options = {}) {
    const url = `${this.baseUrl}${path.startsWith('/') ? path : `/${path}`}`;
    const response = await fetch(url, {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' },
      ...options
    });
    
    if (!response.ok) {
      throw new Error(`GET request failed: ${response.status}`);
    }
    
    return response.json();
  }

  /**
   * Upload a file with FormData
   */
  async uploadFile(path: string, formData: FormData, queryParams = {}) {
    // Build query string
    const searchParams = new URLSearchParams();
    Object.entries(queryParams).forEach(([key, value]) => {
      searchParams.append(key, String(value));
    });
    
    const queryString = searchParams.toString();
    const url = `${this.baseUrl}${path.startsWith('/') ? path : `/${path}`}${queryString ? `?${queryString}` : ''}`;
    
    const response = await fetch(url, {
      method: 'PUT',
      body: formData,
    });
    
    if (!response.ok) {
      throw new Error(`File upload failed: ${response.status}`);
    }
    
    return response.json();
  }
}

// Create a singleton instance
const apiClient = new ApiClient();

// Define domain-specific API functions for NoSQL embedded documents
export const api = {
  invoices: {
    // Get invoices for a vendor with embedded vendor and category objects
    getList: async (vendorId: string, filters?: InvoiceFilters): Promise<InvoiceListResponse> => {
      const queryParams = new URLSearchParams();
      if (filters) {
        Object.entries(filters).forEach(([key, value]) => {
          if (value !== undefined && value !== null) {
            queryParams.append(key, String(value));
          }
        });
      }
      const queryString = queryParams.toString();
      const path = `/v1/${vendorId}/invoices${queryString ? `?${queryString}` : ''}`;
      return apiClient.get(path);
    },

    // Get a single invoice with embedded objects
    getById: async (invoiceId: number): Promise<InvoiceResponse> => {
      return apiClient.get(`/v1/invoices/${invoiceId}`);
    },

    // Upload invoice with enhanced metadata for embedded documents
    upload: async (filePath: string, formData: FormData, params: InvoiceUploadParams): Promise<InvoiceUploadResponse> => {
      return apiClient.uploadFile(`/v1/files/${filePath}`, formData, params);
    },

    // Search invoices across vendors using embedded document fields
    search: async (query: string, filters?: InvoiceFilters): Promise<InvoiceListResponse> => {
      const queryParams = new URLSearchParams({ q: query });
      if (filters) {
        Object.entries(filters).forEach(([key, value]) => {
          if (value !== undefined && value !== null) {
            queryParams.append(key, String(value));
          }
        });
      }
      return apiClient.get(`/v1/invoices/search?${queryParams.toString()}`);
    }
  },

  vendors: {
    // Get vendor statistics from embedded documents
    getStatistics: async (vendorId: string): Promise<VendorStatistics> => {
      return apiClient.get(`/v1/vendors/${vendorId}/statistics`);
    },

    // List all vendors from embedded documents
    list: async (): Promise<{ vendors: Array<{ vendor_id: string; vendor_name: string; is_active: boolean }> }> => {
      return apiClient.get('/v1/vendors');
    },

    // Search vendors by name
    search: async (query: string): Promise<{ vendors: Array<{ vendor_id: string; vendor_name: string; is_active: boolean }> }> => {
      return apiClient.get(`/v1/vendors/search?q=${encodeURIComponent(query)}`);
    }
  },

  categories: {
    // Get category statistics from embedded documents
    getStatistics: async (categoryId: number): Promise<CategoryStatistics> => {
      return apiClient.get(`/v1/categories/${categoryId}/statistics`);
    },

    // List all categories
    list: async (): Promise<{ categories: Array<{ category_id: number; category_name: string; description: string }> }> => {
      return apiClient.get('/v1/categories');
    },

    // Get top categories by various metrics
    getTopByAmount: async (limit: number = 10): Promise<{ categories: CategoryStatistics[] }> => {
      return apiClient.get(`/v1/categories/top/amount?limit=${limit}`);
    },

    getTopByWeight: async (limit: number = 10): Promise<{ categories: CategoryStatistics[] }> => {
      return apiClient.get(`/v1/categories/top/weight?limit=${limit}`);
    }
  }
};

/**
 * Hook for checking if API backend is ready
 */
export function useApiReady() {
  const [isReady, setIsReady] = useState(false);
  const [isChecking, setIsChecking] = useState(true);
  const [error, setError] = useState(null);
  
  useEffect(() => {
    let mounted = true;
    let retryCount = 0;
    let timeoutId: NodeJS.Timeout | null = null;
    
    const checkHealth = async () => {
      try {
        const healthData = await apiClient.checkHealth();
        
        if (!mounted) return;
        
        if (healthData.ready === true || healthData.status === "healthy") {
          setIsReady(true);
          setIsChecking(false);
        } else {
          // Not ready yet, retry with exponential backoff
          const delay = Math.min(2000 * Math.pow(1.5, retryCount), 10000); // Max 10 seconds
          retryCount++;
          timeoutId = setTimeout(checkHealth, delay);
        }
      } catch (error) {
        if (!mounted) return;
        
        retryCount++;
        
        if (retryCount < 15) { // Fewer retries with exponential backoff
          const delay = Math.min(2000 * Math.pow(1.5, retryCount), 10000);
          timeoutId = setTimeout(checkHealth, delay);
        } else {
          // Give up after too many retries
          setIsChecking(false);
          setError(error.message);
        }
      }
    };
    
    checkHealth();
    
    return () => {
      mounted = false;
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, []);
  
  return { isReady, isChecking, error };
}
export default apiClient;
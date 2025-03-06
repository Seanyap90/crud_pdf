'use client';

import { useState, useEffect } from 'react';

// Base API URL
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

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

// Define domain-specific API functions
export const api = {
  invoices: {
    getList: (vendorId: string) => apiClient.get(`/v1/${vendorId}/invoices`),
    upload: (filePath: string, formData: FormData, metadata: Record<string, string>) => 
      apiClient.uploadFile(`/v1/files/${filePath}`, formData, metadata),
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
        
        if (healthData.ready === true) {
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
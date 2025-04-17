'use client';

import { useState, useEffect } from 'react';
import type { Gateway, InsertGateway, MQTTEvent } from '../../shared/schema';
import { 
  normalizeGatewayData, 
  normalizeGateway,
  extractGatewaysFromResponse 
} from '../lib/gateway-normalizer';

// Base API URL - ensure this is correctly set in your environment
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// Enable debug mode for development
const DEBUG = process.env.NODE_ENV !== 'production';

if (DEBUG) {
  console.log(`API client initialized with base URL: ${API_BASE_URL}`);
}

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
        mode: 'cors', // Enable CORS
      });
      
      if (!response.ok) {
        throw new Error(`Health check failed with status: ${response.status}`);
      }
      
      return await response.json();
    } catch (error) {
      console.error('Health check error:', error);
      throw error;
    }
  }

  /**
   * Make a GET request
   */
  async get(path: string, options = {}) {
    const url = `${this.baseUrl}${path.startsWith('/') ? path : `/${path}`}`;
    
    if (DEBUG) console.log(`GET request to ${url}`);
    
    const response = await fetch(url, {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' },
      mode: 'cors', // Enable CORS
      ...options
    });
    
    if (!response.ok) {
      const errorText = await response.text().catch(() => 'No error details');
      throw new Error(`GET request failed: ${response.status} - ${errorText}`);
    }
    
    try {
      const data = await response.json();
      return data;
    } catch (e) {
      console.error(`Error parsing JSON from ${path}:`, e);
      throw new Error(`Failed to parse JSON response: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  /**
   * Make a POST request
   */
  async post(path: string, data: any, options = {}) {
    const url = `${this.baseUrl}${path.startsWith('/') ? path : `/${path}`}`;
    
    if (DEBUG) console.log(`POST request to ${url} with data:`, data);
    
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
      mode: 'cors', // Enable CORS
      ...options
    });
    
    if (!response.ok) {
      const errorText = await response.text().catch(() => 'No error details');
      throw new Error(`POST request failed: ${response.status} - ${errorText}`);
    }
    
    try {
      const responseData = await response.json();
      return responseData;
    } catch (e) {
      console.error(`Error parsing JSON from ${path}:`, e);
      throw new Error(`Failed to parse JSON response: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  /**
   * Make a DELETE request
   */
  async delete(path: string, options = {}) {
    const url = `${this.baseUrl}${path.startsWith('/') ? path : `/${path}`}`;
    
    if (DEBUG) console.log(`DELETE request to ${url}`);
    
    const response = await fetch(url, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      mode: 'cors', // Enable CORS
      ...options
    });
    
    if (!response.ok) {
      const errorText = await response.text().catch(() => 'No error details');
      throw new Error(`DELETE request failed: ${response.status} - ${errorText}`);
    }
    
    try {
      return await response.json();
    } catch (e) {
      console.error(`Error parsing JSON from ${path}:`, e);
      throw new Error(`Failed to parse JSON response: ${e instanceof Error ? e.message : String(e)}`);
    }
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
      mode: 'cors', // Enable CORS
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
  gateways: {
    // List all gateways
    list: async (): Promise<Gateway[]> => {
      try {
        // Make the API request
        const response = await apiClient.get('/api/gateways');
        
        if (DEBUG) console.log('Gateway list raw response:', response);
        
        // Extract gateways data from response (handles different formats)
        const gatewayData = extractGatewaysFromResponse(response);
        
        // Normalize the gateway data to ensure proper format
        const normalizedGateways = normalizeGatewayData(gatewayData);
        
        if (DEBUG) console.log('Normalized gateways:', normalizedGateways);
        
        return normalizedGateways;
      } catch (error) {
        console.error('Error in gateway.list():', error);
        throw error; // Rethrow to let React Query handle it
      }
    },
    
    // Get a specific gateway by ID
    getById: async (gatewayId: string): Promise<Gateway> => {
      try {
        const response = await apiClient.get(`/api/gateways/${gatewayId}`);
        return normalizeGateway(response);
      } catch (error) {
        console.error(`Error fetching gateway ${gatewayId}:`, error);
        throw error;
      }
    },
    
    // Create a new gateway
    create: async (gateway: InsertGateway): Promise<Gateway> => {
      try {
        const response = await apiClient.post('/api/gateways', gateway);
        return normalizeGateway(response);
      } catch (error) {
        console.error('Error creating gateway:', error);
        throw error;
      }
    },
    
    // Delete a gateway
    delete: async (gatewayId: string): Promise<any> => {
      try {
        const response = await apiClient.delete(`/api/gateways/${gatewayId}`);
        return response;
      } catch (error) {
        console.error(`Error deleting gateway ${gatewayId}:`, error);
        throw error;
      }
    },
    
    // Reset a gateway connection
    reset: async (gatewayId: string): Promise<any> => {
      try {
        const response = await apiClient.post(`/api/gateways/${gatewayId}/reset`, {});
        return response;
      } catch (error) {
        console.error(`Error resetting gateway ${gatewayId}:`, error);
        throw error;
      }
    },
    
    // Connect a gateway
    connect: async (gatewayId: string): Promise<any> => {
      try {
        const response = await apiClient.post(`/api/gateways/${gatewayId}/connect`, {});
        return response;
      } catch (error) {
        console.error(`Error connecting gateway ${gatewayId}:`, error);
        throw error;
      }
    },
    
    // Disconnect a gateway
    disconnect: async (gatewayId: string): Promise<any> => {
      try {
        const response = await apiClient.post(`/api/gateways/${gatewayId}/disconnect`, {});
        return response;
      } catch (error) {
        console.error(`Error disconnecting gateway ${gatewayId}:`, error);
        throw error;
      }
    },
    
    // Send a heartbeat
    sendHeartbeat: async (gatewayId: string): Promise<any> => {
      try {
        const response = await apiClient.post(`/api/gateways/${gatewayId}/heartbeat`, {});
        return response;
      } catch (error) {
        console.error(`Error sending heartbeat for gateway ${gatewayId}:`, error);
        throw error;
      }
    },
    
    // Update certificate status
    updateCertificate: async (gatewayId: string, isInstalled: boolean): Promise<any> => {
      try {
        const response = await apiClient.post(`/api/gateways/${gatewayId}/certificate?status=${isInstalled}`, {});
        return response;
      } catch (error) {
        console.error(`Error updating certificate for gateway ${gatewayId}:`, error);
        throw error;
      }
    }
  },
  
  mqtt: {
    // Send MQTT event to the backend
    sendEvent: async (event: MQTTEvent) => {
      try {
        return await apiClient.post('/api/mqtt/events', event);
      } catch (error) {
        console.error('Error sending MQTT event:', error);
        throw error;
      }
    }
  },
  
  config: {
    // Upload a configuration file
    uploadFile: async (gatewayId: string, file: File): Promise<any> => {
      try {
        const formData = new FormData();
        formData.append('gateway_id', gatewayId);
        formData.append('file', file);
        
        const url = `${API_BASE_URL}/api/config`;
        const response = await fetch(url, {
          method: 'POST',
          body: formData,
          mode: 'cors',
        });
        
        if (!response.ok) {
          const errorText = await response.text().catch(() => 'No error details');
          throw new Error(`Configuration file upload failed: ${response.status} - ${errorText}`);
        }
        
        return await response.json();
      } catch (error) {
        console.error('Error uploading configuration file:', error);
        throw error;
      }
    },
    
    // Get configuration update status
    getStatus: async (updateId: string): Promise<any> => {
      try {
        const response = await apiClient.get(`/api/config/${updateId}`);
        return response;
      } catch (error) {
        console.error(`Error fetching config update ${updateId}:`, error);
        throw error;
      }
    },
    
    // Get the latest configuration for a gateway
    getLatest: async (gatewayId: string): Promise<any> => {
      try {
        const url = `/api/config/gateway/${gatewayId}/latest`;
        const response = await apiClient.get(url);
        return response;
      } catch (error) {
        console.error(`Error fetching latest config for gateway ${gatewayId}:`, error);
        throw error;
      }
    }
  }
};

/**
 * Hook for checking if API backend is ready
 */
export function useApiReady() {
  const [isReady, setIsReady] = useState(false);
  const [isChecking, setIsChecking] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  useEffect(() => {
    let mounted = true;
    let retryCount = 0;
    let timeoutId: NodeJS.Timeout | null = null;
    
    const checkHealth = async () => {
      try {
        const healthData = await apiClient.checkHealth();
        
        if (!mounted) return;
        
        if (healthData.status === "healthy") {
          setIsReady(true);
          setIsChecking(false);
        } else {
          // Not ready yet, retry with exponential backoff
          const delay = Math.min(2000 * Math.pow(1.5, retryCount), 10000); // Max 10 seconds
          retryCount++;
          timeoutId = setTimeout(checkHealth, delay);
        }
      } catch (err) {
        if (!mounted) return;
        
        retryCount++;
        
        if (retryCount < 15) { // Fewer retries with exponential backoff
          const delay = Math.min(2000 * Math.pow(1.5, retryCount), 10000);
          timeoutId = setTimeout(checkHealth, delay);
        } else {
          // Give up after too many retries
          setIsChecking(false);
          
          // Safely extract the error message
          let errorMessage = 'Unknown error connecting to API';
          if (err instanceof Error) {
            errorMessage = err.message;
          } else if (err && typeof err === 'object') {
            errorMessage = String(err);
          } else if (typeof err === 'string') {
            errorMessage = err;
          }
          
          setError(errorMessage);
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
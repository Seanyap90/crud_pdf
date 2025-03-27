'use client';

import type { Gateway } from '../../shared/schema';

// Enable debug mode during development
const DEBUG = process.env.NODE_ENV !== 'production';

/**
 * Normalizes gateway data from the backend to ensure it's properly formatted for the frontend
 */
export function normalizeGateway(gateway: any): Gateway {
  if (DEBUG) {
    console.log('Normalizing gateway:', gateway);
  }
  
  if (!gateway || typeof gateway !== 'object') {
    console.warn('Invalid gateway object:', gateway);
    return {
      id: 'invalid-gateway',
      name: 'Invalid Gateway',
      location: 'Unknown',
      status: 'error'
    };
  }
  
  // Extract backend gateway ID (using gateway_id or id)
  const gatewayId = gateway.gateway_id || gateway.id || '';
  
  // Handle field name discrepancies (backend uses snake_case, frontend uses camelCase)
  const status = gateway.status || 'unknown';
  const lastUpdated = gateway.last_updated || gateway.lastUpdated || null;
  
  // For the table component, keep both camelCase and snake_case versions for backward compatibility
  const last_updated = lastUpdated;
  
  // Handle error field which might be a JSON string
  const error = gateway.error || null;
  
  // Parse error JSON if it's a string
  let parsedError = error;
  if (typeof error === 'string' && error.startsWith('{')) {
    try {
      const errorObj = JSON.parse(error);
      parsedError = errorObj.message || errorObj.status || error;
    } catch (e) {
      // Keep original error if parsing fails
    }
  }
  
  // Map connection timestamps
  const connectedAt = gateway.connected_at || null;
  const disconnectedAt = gateway.disconnected_at || null;
  const createdAt = gateway.created_at || null;
  const deletedAt = gateway.deleted_at || null;
  
  // Create a normalized gateway object with both camelCase and snake_case properties
  const normalizedGateway: Gateway = {
    id: gatewayId,
    name: gateway.name || 'Unknown Gateway',
    location: gateway.location || 'Unknown Location',
    status: status,
    lastUpdated: lastUpdated,
    last_updated: lastUpdated, // Include snake_case version for compatibility
    container_id: gateway.container_id || null,
    health: gateway.health || null,
    uptime: gateway.uptime || null,
    error: parsedError,
    connected_at: connectedAt,
    disconnected_at: disconnectedAt,
    created_at: createdAt,
    deleted_at: deletedAt
  };
  
  // Add certificate info if present
  if (gateway.certificate_info) {
    let certInfo = gateway.certificate_info;
    
    // Parse certificate info if it's a string
    if (typeof certInfo === 'string') {
      try {
        certInfo = JSON.parse(certInfo);
      } catch (e) {
        certInfo = { status: 'unknown' };
      }
    }
    
    normalizedGateway.certificate_info = certInfo;
  }
  
  if (DEBUG) {
    console.log('Normalized gateway:', normalizedGateway);
  }
  
  return normalizedGateway;
}

/**
 * Normalizes an array of gateways from the server response
 */
export function normalizeGatewayData(data: any): Gateway[] {
  if (DEBUG) {
    console.log('Normalizing gateway data:', data);
  }
  
  if (!data) {
    console.warn('No data to normalize');
    return [];
  }
  
  // Handle single gateway object
  if (!Array.isArray(data) && typeof data === 'object') {
    // Check if this is a response with gateways property
    if (data.gateways && Array.isArray(data.gateways)) {
      return normalizeGatewayData(data.gateways);
    }
    
    // Check if this is a response with gateway property
    if (data.gateway && typeof data.gateway === 'object') {
      return [normalizeGateway(data.gateway)];
    }
    
    // Otherwise treat as a single gateway object
    return [normalizeGateway(data)];
  }
  
  // Handle array of gateways
  if (Array.isArray(data)) {
    const normalized = data.map(gateway => normalizeGateway(gateway));
    if (DEBUG) {
      console.log(`Normalized ${normalized.length} gateways`);
    }
    return normalized;
  }
  
  console.warn('Unhandled data format:', data);
  return [];
}

/**
 * Helper function to extract gateway array from various backend response formats
 */
export function extractGatewaysFromResponse(response: any): any[] {
  // Direct array - the new format 
  if (Array.isArray(response)) {
    return response;
  }
  
  // Format with gateways property
  if (response && response.gateways && Array.isArray(response.gateways)) {
    return response.gateways;
  }
  
  // Response from API with gateway object
  if (response && response.gateway && typeof response.gateway === 'object') {
    return [response.gateway];
  }
  
  // Single gateway object
  if (response && typeof response === 'object' && 
     (response.id || response.gateway_id)) {
    return [response];
  }
  
  // Empty object or unexpected format
  console.warn('Could not extract gateways from response:', response);
  return [];
}
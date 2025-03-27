// Types for backend state machine states
export type GatewayStateType = 'created' | 'connected' | 'disconnected' | 'deleted';

// Types for gateway statuses (may include more UI statuses)
export type GatewayStatusType = 'online' | 'offline' | 'warning' | 'error' | GatewayStateType;

// Gateway representation for the frontend
export interface Gateway {
  id: string;
  name: string;
  location: string;
  status: GatewayStatusType | string;
  lastUpdated?: string | null;
  last_updated?: string | null; // Snake_case version for backend compatibility
  container_id?: string | null;
  health?: string | null;
  uptime?: string | null;
  error?: string | null;
  connected_at?: string | null;
  disconnected_at?: string | null;
  created_at?: string | null;
  deleted_at?: string | null;
  certificate_info?: {
    status: string;
    installed_at?: string;
  } | null;
}

// Data structure for creating a new gateway
export interface InsertGateway {
  name: string;
  location: string;
  gateway_id?: string;
}

// MQTT event message format for API
export interface MQTTEvent {
  gateway_id: string;
  event_type: string;
  update_type?: string;
  payload?: any;
  timestamp?: string;
}

// API response format
export interface ApiResponse<T> {
  status: string;
  gateway?: Gateway;
  gateways?: Gateway[];
  total?: number;
  data?: T;
  error?: string;
}
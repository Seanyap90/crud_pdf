// Types for backend state machine states
export type GatewayStateType = 'created' | 'connected' | 'disconnected' | 'deleted';

// Types for gateway statuses (may include more UI statuses)
export type GatewayStatusType = 'online' | 'offline' | 'warning' | 'error' | GatewayStateType;

// Device information embedded in measurements (NoSQL structure)
export interface DeviceInfo {
  device_id: string;
  gateway_id: string;
  device_type: string;
  name: string;
  location: string;
  status: string;
}

// Firmware information for gateway and end devices
export interface FirmwareInfo {
  version?: string;
  lastUpdated?: string;
  file?: string;
}

// End device information for gateway
export interface EndDevice {
  id: string;
  name: string;
  type: string;
  status: string;
  firmware?: FirmwareInfo;
}

// Device representation with NoSQL document structure
export interface Device {
  device_id: string;
  gateway_id: string;
  device_type: string;
  name: string;
  location: string;
  status: string;
  last_updated: string;
  last_measurement?: string | null;
  last_config_fetch?: string | null;
  config_version?: string | null;
  config_hash?: string | null;
  device_config?: Record<string, any>;
}

// Measurement with embedded device info (NoSQL structure)
export interface Measurement {
  measurement_id: number;
  device_info: DeviceInfo;              // Embedded device + gateway information
  measurement_type: string;
  timestamp: string;
  processed: boolean;
  uploaded_to_cloud: boolean;
  payload: Record<string, any>;
}

// Gateway representation for the frontend (unchanged for NoSQL)
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
  firmware?: FirmwareInfo;
  endDevices?: EndDevice[];
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

// API response formats for NoSQL document operations
export interface ApiResponse<T> {
  status: string;
  gateway?: Gateway;
  gateways?: Gateway[];
  devices?: Device[];
  measurements?: Measurement[];
  total?: number;
  data?: T;
  error?: string;
}

// Device list response
export interface DeviceListResponse {
  devices: Device[];
  total?: number;
}

// Measurement list response with embedded device info
export interface MeasurementListResponse {
  measurements: Measurement[];
  total?: number;
}
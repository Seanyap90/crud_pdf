package main

import (
    "bytes"
    "crypto/tls"
    "encoding/json"
    "fmt"
    "io/ioutil"
    "log"
    "net"
    "net/http"
    "os"
    "os/exec"
    "os/signal"
    "strings"
    "syscall"
    "time"
    "sync"
    "crypto/sha256"
	"math/rand"
    "gopkg.in/yaml.v3"
    "math"

    mqtt "github.com/eclipse/paho.mqtt.golang"
)

// Event types for internal communication
type EventType int

const (
    EventCertificateFound EventType = iota
    EventCertificateRemoved
    EventMQTTConnected
    EventMQTTDisconnected
    EventHeartbeatDue
    EventShutdown
    EventMQTTMessage
    EventConfigUpdate      // New event type
    EventConfigRequest     // New event type
)

// Event represents an internal event in the system
type Event struct {
    Type    EventType
    Data    interface{}
    Time    time.Time
}

// MQTTEvent represents the event to send to the API
type MQTTEvent struct {
    GatewayID  string      `json:"gateway_id"`
    EventType  string      `json:"event_type"`
    Payload    interface{} `json:"payload"`
    Timestamp  string      `json:"timestamp"`
}

// Constants
const (
    CertPath          = "/app/certificates/cert.pem"
    KeyPath           = "/app/certificates/key.pem"
    CheckInterval     = 5 * time.Second
    HeartbeatInterval = 30 * time.Second
)

// Config represents a YAML configuration for end devices
type Config struct {
    YAML      string    // The raw YAML configuration
    UpdatedAt time.Time // When the config was last updated
}

// ConfiguredEndDevice represents a simulated end device
type ConfiguredEndDevice struct {
	ID              string                 // Unique device identifier
	Type            string                 // Type of device (scale)
	LastConfigFetch time.Time              // When configuration was last fetched
	ConfigVersion   string                 // Hash of current configuration
	Status          string                 // online, offline, error
	LastMeasurement time.Time              // When last measurement was taken
	DeviceConfig    map[string]interface{} // Device-specific configuration
	StopChan        chan bool              // Channel to signal shutdown
}

// DeviceManager manages the lifecycle of simulated end devices
type DeviceManager struct {
	Devices     map[string]*ConfiguredEndDevice // Map of device ID to device
	DeviceMutex sync.RWMutex                    // Protect access to devices map
	ConfigMutex sync.RWMutex                    // Protect access to configuration
}

// Global variables
var (
    gatewayID       string
    brokerAddress   string
    mqttClient      mqtt.Client
    eventChan       chan Event = make(chan Event, 100) // Buffered channel for events
    hasCertificates bool = false
    isMqttConnected bool = false
    mtx             http.ServeMux
    currentConfig   Config                  // Store the current configuration
    configMutex     sync.RWMutex            // Mutex to protect access to the configuration
    endDeviceManager *DeviceManager
)

func main() {
    log.SetFlags(log.LstdFlags | log.Lmicroseconds)
    rand.Seed(time.Now().UnixNano())
    setupSignalHandling()
    setupGatewayID()
    setupBrokerAddress()
    
    // Start HTTP server in a goroutine
    go startHTTPServer()
    
    // Start certificate watcher in a goroutine
    go watchCertificates()
    
    // Start heartbeat timer in a goroutine
    go heartbeatTimer()
    
    // Main event loop
    mainEventLoop()
}

// setupSignalHandling sets up handlers for system signals
func setupSignalHandling() {
    c := make(chan os.Signal, 1)
    signal.Notify(c, os.Interrupt, syscall.SIGTERM)
    
    go func() {
        sig := <-c
        log.Printf("Received signal %v, shutting down...", sig)
        eventChan <- Event{Type: EventShutdown, Time: time.Now()}
    }()
}

// setupGatewayID gets the gateway ID from environment
func setupGatewayID() {
    gatewayID = os.Getenv("GATEWAY_ID")
    if gatewayID == "" {
        gatewayID = fmt.Sprintf("gateway-%d", time.Now().Unix())
        log.Printf("GATEWAY_ID not set, using generated ID: %s", gatewayID)
    }
}

// setupBrokerAddress gets the MQTT broker address from environment
func setupBrokerAddress() {
    // Check environment variable
    envBroker := os.Getenv("MQTT_BROKER_ADDRESS")
    
    // Detect environment type
    isDockerDesktop := false
    
    // Check for WSL existence
    if _, err := os.Stat("/proc/sys/fs/binfmt_misc/WSLInterop"); err == nil {
        isDockerDesktop = true
        log.Printf("WSL environment detected")
    }
    
    // Check if host.docker.internal is resolvable
    pingCmd := exec.Command("ping", "-c", "1", "-W", "1", "host.docker.internal")
    if pingCmd.Run() == nil {
        isDockerDesktop = true
        log.Printf("host.docker.internal is reachable, Docker Desktop detected")
    }
    
    // In Docker Desktop, always prioritize using host.docker.internal
    if isDockerDesktop && (envBroker == "" || envBroker == "mqtt-broker:1883") {
        brokerAddress = "host.docker.internal:1883"
        log.Printf("Docker Desktop detected, using host.docker.internal:1883")
    } else if envBroker != "" {
        // Use whatever broker address was provided
        brokerAddress = envBroker
        log.Printf("Using MQTT broker address from environment: %s", brokerAddress)
    } else {
        // Default to service name for Docker DNS resolution
        brokerAddress = "mqtt-broker:1883"
        log.Printf("No broker address specified, using service name: %s", brokerAddress)
    }
    
    // Extract host for resolution checks
    hostname := brokerAddress
    if strings.Contains(brokerAddress, ":") {
        parts := strings.Split(brokerAddress, ":")
        hostname = parts[0]
    }
    
    // Try DNS lookup first to validate the hostname
    if net.ParseIP(hostname) == nil {
        // It's a hostname, try to resolve it
        ips, err := net.LookupHost(hostname)
        if err != nil {
            log.Printf("Warning: Cannot resolve hostname %s: %v", hostname, err)
            
            // Don't try alternative approaches in Docker Desktop
            if !isDockerDesktop {
                // Try to verify the MQTT service is accessible
                if !checkTCPConnectivity(brokerAddress) {
                    log.Printf("MQTT broker at %s is not accessible, checking Docker DNS", brokerAddress)
                    // This might be a Docker DNS service name issue
                    log.Printf("Note: In Docker environments, ensure all containers are on the same network")
                    log.Printf("Check that 'mqtt-broker' service is running and on the 'iot-network'")
                }
            }
        } else {
            log.Printf("Successfully resolved hostname %s to IPs: %v", hostname, ips)
        }
    }
    
    log.Printf("Final MQTT broker address: %s", brokerAddress)
}

// checkTCPConnectivity tries to establish a TCP connection to verify the address is reachable
func checkTCPConnectivity(address string) bool {
    // Ensure we have a port
    if !strings.Contains(address, ":") {
        address = address + ":1883"
    }
    
    log.Printf("Testing TCP connectivity to %s", address)
    conn, err := net.DialTimeout("tcp", address, 3*time.Second)
    if err != nil {
        log.Printf("Warning: Cannot connect to %s: %v", address, err)
        return false
    }
    
    conn.Close()
    log.Printf("Successfully connected to %s", address)
    return true
}

// setupApiUrl chooses the best API URL based on environment
func setupApiUrl() string {
    // Get API URL from environment
    apiURL := os.Getenv("API_URL")
    
    // Default fallback address for Docker environments
    defaultApiUrl := "http://172.17.0.1:8000"
    
    if apiURL == "" || apiURL == "http://0.0.0.0:8000" || apiURL == "https://0.0.0.0:8000" {
        // No valid API URL specified, use default
        log.Printf("API_URL is not set or using 0.0.0.0, using %s instead", defaultApiUrl)
        return defaultApiUrl
    }
    
    // Check if we're using host.docker.internal but it's not accessible
    if strings.Contains(apiURL, "host.docker.internal") {
        // Try to ping host.docker.internal
        pingCmd := exec.Command("ping", "-c", "1", "-W", "1", "host.docker.internal")
        if pingCmd.Run() != nil {
            // Cannot reach host.docker.internal, use Docker bridge IP instead
            log.Printf("host.docker.internal not accessible, using %s instead", defaultApiUrl)
            return defaultApiUrl
        }
    }
    
    log.Printf("Using API URL: %s", apiURL)
    return apiURL
}

// watchCertificates monitors certificate files and sends events when they change
func watchCertificates() {
    ticker := time.NewTicker(CheckInterval)
    defer ticker.Stop()
    
    var prevHasCerts bool = hasCertificates
    
    for {
        select {
        case <-ticker.C:
            currHasCerts := fileExists(CertPath) && fileExists(KeyPath)
            
            // Only send events on state change
            if currHasCerts != prevHasCerts {
                if currHasCerts {
                    log.Printf("Certificates found")
                    eventChan <- Event{Type: EventCertificateFound, Time: time.Now()}
                } else {
                    log.Printf("Certificates removed")
                    eventChan <- Event{Type: EventCertificateRemoved, Time: time.Now()}
                }
                prevHasCerts = currHasCerts
            }
        }
    }
}

// heartbeatTimer triggers heartbeat events at regular intervals
func heartbeatTimer() {
    ticker := time.NewTicker(HeartbeatInterval)
    defer ticker.Stop()
    
    for {
        select {
        case <-ticker.C:
            eventChan <- Event{Type: EventHeartbeatDue, Time: time.Now()}
        }
    }
}

// requestConfig sends a request for the latest configuration
func requestConfig() {
    if !isMqttConnected || mqttClient == nil {
        log.Printf("Cannot request config: MQTT not connected")
        return
    }
    
    topic := fmt.Sprintf("gateway/%s/request_config", gatewayID)
    payload := map[string]interface{}{
        "timestamp": time.Now().Format(time.RFC3339),
    }
    
    jsonData, err := json.Marshal(payload)
    if err != nil {
        log.Printf("Error marshaling config request: %v", err)
        return
    }
    
    token := mqttClient.Publish(topic, 0, false, jsonData)
    token.Wait()
    
    if token.Error() != nil {
        log.Printf("Error requesting config: %v", token.Error())
    } else {
        log.Printf("Configuration request sent to topic: %s", topic)
    }
}

// storeConfig safely stores a new configuration
func storeConfig(yamlConfig string) {
    configMutex.Lock()
    defer configMutex.Unlock()
    
    currentConfig = Config{
        YAML:      yamlConfig,
        UpdatedAt: time.Now(),
    }
    
    // Update device manager with the new configuration
    if endDeviceManager != nil {
        if err := endDeviceManager.UpdateConfiguration(yamlConfig); err != nil {
            log.Printf("Error updating device manager configuration: %v", err)
        } else {
            log.Printf("Device manager configuration updated successfully")
        }
    }
    
    log.Printf("New configuration stored, size: %d bytes", len(yamlConfig))
}

// getConfig safely retrieves the current configuration
func getConfig() Config {
    configMutex.RLock()
    defer configMutex.RUnlock()
    
    return currentConfig
}

// sendConfigAcknowledgment sends an acknowledgment for a received configuration
func sendConfigAcknowledgment(status string) {
    if !isMqttConnected || mqttClient == nil {
        log.Printf("Cannot send config acknowledgment: MQTT not connected")
        return
    }
    
    topic := fmt.Sprintf("gateway/%s/config/delivered", gatewayID)
    payload := map[string]interface{}{
        "status": status,
        "timestamp": time.Now().Format(time.RFC3339),
    }
    
    jsonData, err := json.Marshal(payload)
    if err != nil {
        log.Printf("Error marshaling config acknowledgment: %v", err)
        return
    }
    
    token := mqttClient.Publish(topic, 0, false, jsonData)
    token.Wait()
    
    if token.Error() != nil {
        log.Printf("Error sending config acknowledgment: %v", token.Error())
    } else {
        log.Printf("Configuration acknowledgment sent to topic: %s", topic)
    }
}

// NewDeviceManager creates a new device manager
func NewDeviceManager() *DeviceManager {
	manager := &DeviceManager{
		Devices: make(map[string]*ConfiguredEndDevice),
	}
	return manager
}

// UpdateConfiguration updates the device manager configuration
func (dm *DeviceManager) UpdateConfiguration(yamlConfig string) error {
    // Parse YAML into a map for configuration values
    var configMap map[string]interface{}
    if err := yaml.Unmarshal([]byte(yamlConfig), &configMap); err != nil {
        return fmt.Errorf("failed to parse configuration: %v", err)
    }
    
    // Get devices configuration
    deviceCount := 5 // Default
    if devicesConfig, ok := configMap["devices"].(map[string]interface{}); ok {
        if count, ok := devicesConfig["count"].(int); ok && count > 0 {
            deviceCount = count
        }
    }
    
    // Update devices based on new configuration
    dm.updateDevices(deviceCount, configMap)
    
    log.Printf("Device manager updated with new configuration: %d devices", deviceCount)
    return nil
}

// updateDevices updates the device list based on the configuration
func (dm *DeviceManager) updateDevices(targetCount int, config map[string]interface{}) {
	dm.DeviceMutex.Lock()
	defer dm.DeviceMutex.Unlock()
	
	currentCount := len(dm.Devices)
	
	// Create new devices if needed
	for i := currentCount + 1; i <= targetCount; i++ {
		deviceID := fmt.Sprintf("scale-%s-%d", gatewayID, i)
		log.Printf("Creating new device: %s", deviceID)
		
		device := &ConfiguredEndDevice{
			ID:      deviceID,
			Type:    "scale",
			Status:  "online",
			StopChan: make(chan bool),
		}
		
		dm.Devices[deviceID] = device
		
		// Configure the device based on config
		device.DeviceConfig = getDeviceConfig(device.Type, config)
		
		// Start the device simulation in a goroutine
		go dm.runDeviceSimulation(device)
	}
	
	// Remove excess devices if needed
	if currentCount > targetCount {
		// Find devices to remove
		var toRemove []string
		count := 0
		for id := range dm.Devices {
			if count >= (currentCount - targetCount) {
				break
			}
			toRemove = append(toRemove, id)
			count++
		}
		
		// Stop and remove each device
		for _, id := range toRemove {
			device := dm.Devices[id]
			close(device.StopChan) // Signal to stop
			delete(dm.Devices, id)
			log.Printf("Removed device: %s", id)
		}
	}
}

// getDeviceConfig extracts device-specific configuration
func getDeviceConfig(deviceType string, config map[string]interface{}) map[string]interface{} {
	// Extract relevant configuration for this device type
	deviceConfig := make(map[string]interface{})
	
	// Copy global configuration that applies to all devices
	if materials, ok := config["materials"]; ok {
		deviceConfig["materials"] = materials
	}
	if vendors, ok := config["vendors"]; ok {
		deviceConfig["vendors"] = vendors
	}
	if measurement, ok := config["measurement"]; ok {
		deviceConfig["measurement"] = measurement
	}
	if reporting, ok := config["reporting"]; ok {
		deviceConfig["reporting"] = reporting
	}
	
	// Extract device-specific configuration based on type
	if devicesConfig, ok := config["devices"].(map[string]interface{}); ok {
		if behaviors, ok := devicesConfig["behaviors"].(map[string]interface{}); ok {
			if behavior, ok := behaviors[deviceType].(map[string]interface{}); ok {
				deviceConfig["behavior"] = behavior
			}
		}
	}
	
	return deviceConfig
}

func handleDevicesRequest(w http.ResponseWriter, r *http.Request) {
    if endDeviceManager == nil {
        http.Error(w, "End device manager not initialized", http.StatusInternalServerError)
        return
    }
    
    w.Header().Set("Content-Type", "application/json")
    devices := endDeviceManager.GetDeviceStatus()
    json.NewEncoder(w).Encode(map[string]interface{}{
        "devices": devices,
        "count":   len(devices),
    })
}

func handleMeasurementRequest(w http.ResponseWriter, r *http.Request) {
    if r.Method != http.MethodPost {
        http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
        return
    }
    
    var measurement map[string]interface{}
    if err := json.NewDecoder(r.Body).Decode(&measurement); err != nil {
        http.Error(w, "Invalid request body", http.StatusBadRequest)
        return
    }
    
    deviceID, ok := measurement["device_id"].(string)
    if !ok || deviceID == "" {
        http.Error(w, "Missing device_id", http.StatusBadRequest)
        return
    }
    
    log.Printf("Received measurement from device %s via HTTP", deviceID)
    
    if isMqttConnected && mqttClient != nil {
        measurement["gateway_id"] = gatewayID
        jsonData, err := json.Marshal(measurement)
        if err != nil {
            http.Error(w, "Error encoding measurement", http.StatusInternalServerError)
            return
        }
        
        topic := fmt.Sprintf("gateway/%s/device/%s/measurement", gatewayID, deviceID)
        token := mqttClient.Publish(topic, 0, false, jsonData)
        token.Wait()
        
        if token.Error() != nil {
            log.Printf("Error publishing measurement: %v", token.Error())
            http.Error(w, "Error publishing measurement", http.StatusInternalServerError)
            return
        }
    }
    
    w.WriteHeader(http.StatusOK)
    w.Write([]byte("{\"status\":\"ok\"}"))
}

// startHTTPServer initializes and starts the HTTP server
func startHTTPServer() {
    mtx.HandleFunc("/status", handleStatusRequest)
    mtx.HandleFunc("/health", handleHealthRequest)
    mtx.HandleFunc("/reset", handleResetRequest)
    mtx.HandleFunc("/config", handleConfigRequest)
    mtx.HandleFunc("/devices", handleDevicesRequest)
    mtx.HandleFunc("/measurement", handleMeasurementRequest)
    
    port := os.Getenv("GATEWAY_PORT")
    if port == "" {
        port = "6000"
    }
    
    log.Printf("Starting HTTP server on port %s", port)
    if err := http.ListenAndServe(":"+port, &mtx); err != nil {
        log.Fatalf("HTTP server failed: %v", err)
    }
}

// handleStatusRequest handles HTTP status endpoint
func handleStatusRequest(w http.ResponseWriter, r *http.Request) {
    w.Header().Set("Content-Type", "text/plain")
    
    fmt.Fprintf(w, "Gateway Simulator Status\n")
    fmt.Fprintf(w, "======================\n\n")
    fmt.Fprintf(w, "Gateway ID: %s\n", gatewayID)
    fmt.Fprintf(w, "MQTT Broker: %s\n", brokerAddress)
    fmt.Fprintf(w, "Certificates: %s\n", map[bool]string{true: "FOUND", false: "NOT FOUND"}[hasCertificates])
    fmt.Fprintf(w, "MQTT Connected: %s\n", map[bool]string{true: "YES", false: "NO"}[isMqttConnected])
    
    // Add container information
    fmt.Fprintf(w, "\nContainer Information:\n")
    fmt.Fprintf(w, "Container ID: %s\n", os.Getenv("HOSTNAME"))
    fmt.Fprintf(w, "API URL: %s\n", setupApiUrl())
    
    // Show certificate details if present
    if hasCertificates {
        fmt.Fprintf(w, "\nCertificate Information:\n")
        fmt.Fprintf(w, "Certificate Path: %s\n", CertPath)
        fmt.Fprintf(w, "Private Key Path: %s\n", KeyPath)
    }
}

// handleHealthRequest handles HTTP health endpoint
func handleHealthRequest(w http.ResponseWriter, r *http.Request) {
    w.WriteHeader(http.StatusOK)
    fmt.Fprintf(w, "healthy")
}

// handleResetRequest handles HTTP reset endpoint
func handleResetRequest(w http.ResponseWriter, r *http.Request) {
    log.Printf("Reset requested via HTTP")
    
    // Disconnect MQTT if connected
    if isMqttConnected && mqttClient != nil {
        mqttClient.Disconnect(250)
    }
    
    // Try to reconnect if certificates are available
    if hasCertificates {
        eventChan <- Event{Type: EventCertificateFound, Time: time.Now()}
    }
    
    w.WriteHeader(http.StatusOK)
    fmt.Fprintf(w, "reset initiated")
}

// runDeviceSimulation runs the simulation for a device
func (dm *DeviceManager) runDeviceSimulation(device *ConfiguredEndDevice) {
	// Get behavior configuration
	measurementInterval := 60 // Default: 60 seconds
	if behaviorConfig, ok := device.DeviceConfig["behavior"].(map[string]interface{}); ok {
		if frequency, ok := behaviorConfig["measurement_frequency_seconds"].(int); ok && frequency > 0 {
			measurementInterval = frequency
		}
	}
	
	// Add some randomness
	jitter := rand.Intn(measurementInterval / 4)
	measurementInterval = measurementInterval + jitter
	
	// Create ticker for periodic measurements
	ticker := time.NewTicker(time.Duration(measurementInterval) * time.Second)
	defer ticker.Stop()
	
	// Fetch configuration immediately
	dm.fetchDeviceConfig(device)
	
	log.Printf("Started simulation for device %s with interval %d seconds", 
		device.ID, measurementInterval)
	
	// Main simulation loop
	for {
		select {
		case <-ticker.C:
			// Generate and send measurement
			if config := getConfig(); config.YAML != "" {
				measurement := dm.generateMeasurement(device)
				dm.publishMeasurement(device, measurement)
				// Also send to HTTP endpoint on gateway
				go dm.sendMeasurementToGateway(device, measurement)
			}
		
		case <-device.StopChan:
			// Stop simulation
			log.Printf("Stopping simulation for device %s", device.ID)
			return
		}
	}
}

// fetchDeviceConfig fetches device configuration from gateway
func (dm *DeviceManager) fetchDeviceConfig(device *ConfiguredEndDevice) {
	// In a real device, this would make an HTTP request to the gateway
	// For simulation, we access the configuration directly
	configData := getConfig()
    if configData.YAML == "" {
        return
    }
	
	// Generate a version hash for the configuration
	h := sha256.New()
	h.Write([]byte(configData.YAML))
	newVersion := fmt.Sprintf("%x", h.Sum(nil))[:8]
	
	// Only update if version changed
    if newVersion != device.ConfigVersion {
        // Parse YAML into map
        var configMap map[string]interface{}
        if err := yaml.Unmarshal([]byte(configData.YAML), &configMap); err != nil {
            log.Printf("Error parsing config for device %s: %v", device.ID, err)
            return
        }
        
        device.DeviceConfig = getDeviceConfig(device.Type, configMap)
        device.ConfigVersion = newVersion
        device.LastConfigFetch = time.Now()
        
        log.Printf("Device %s updated configuration to version %s", 
            device.ID, device.ConfigVersion)
    }
}

// mainEventLoop processes events and coordinates actions
func mainEventLoop() {
    for {
        event := <-eventChan
        
        switch event.Type {
        case EventCertificateFound:
            hasCertificates = true
            handleCertificateFound()
            
        case EventCertificateRemoved:
            hasCertificates = false
            // Only disconnect if connected
            if isMqttConnected && mqttClient != nil {
                mqttClient.Disconnect(250)
            }
            
        case EventMQTTConnected:
            isMqttConnected = true
            // Send connected status along with certificate info
            sendStatusUpdate("connected", "Connected to MQTT broker", map[string]interface{}{
                "certificate_status": "installed",
                "status": "online",
            })

            // Initialize device manager if not already done
            if endDeviceManager == nil {
                endDeviceManager = NewDeviceManager()
                log.Printf("Device manager initialized")
                
                // If we already have a configuration, apply it
                if config := getConfig(); config.YAML != "" {
                    if err := endDeviceManager.UpdateConfiguration(config.YAML); err != nil {
                        log.Printf("Error applying existing configuration to device manager: %v", err)
                    } else {
                        log.Printf("Applied existing configuration to device manager")
                    }
                }
            }

            // Request configuration after connection
            time.Sleep(500 * time.Millisecond) // Small delay to ensure subscriptions are set up
            requestConfig()
            
        case EventMQTTDisconnected:
            isMqttConnected = false
            // Send disconnection event to API
            if data, ok := event.Data.(error); ok {
                log.Printf("MQTT disconnected due to: %v", data)
                sendStatusUpdate("disconnected", fmt.Sprintf("MQTT connection lost: %v", data), map[string]interface{}{
                    "status": "offline",
                    "error": data.Error(),
                })
            } else {
                sendStatusUpdate("disconnected", "MQTT connection lost", map[string]interface{}{
                    "status": "offline",
                })
            }
            
        case EventHeartbeatDue:
            if isMqttConnected && mqttClient != nil {
                sendHeartbeat()
            }
            
        case EventMQTTMessage:
            if msg, ok := event.Data.(mqtt.Message); ok {
                handleMQTTMessage(msg)
            }
        
        case EventConfigUpdate:
            if msg, ok := event.Data.(mqtt.Message); ok {
                log.Printf("Processing configuration update")
                
                // Try to parse as JSON first
                var configData map[string]interface{}
                if err := json.Unmarshal(msg.Payload(), &configData); err == nil {
                    // Check if there's a yaml_config field in the JSON
                    if yamlConfig, ok := configData["yaml_config"].(string); ok {
                        storeConfig(yamlConfig)
                        sendConfigAcknowledgment("success")
                        continue
                    }
                }
                
                // If not JSON or no yaml_config field, treat payload as raw YAML
                yamlConfig := string(msg.Payload())
                storeConfig(yamlConfig)
                sendConfigAcknowledgment("success")
            }
            
        case EventShutdown:
            // Shutdown device manager if it exists
            if endDeviceManager != nil {
                endDeviceManager.Shutdown()
            }
            // Send offline status
            sendStatusUpdate("shutdown", "Gateway shutting down", map[string]interface{}{
                "status": "offline",
            })
            if isMqttConnected && mqttClient != nil {
                mqttClient.Disconnect(1000)
            }
            log.Println("Gateway shutdown completed")
            os.Exit(0)
        }
    }
}

// handleCertificateFound handles certificate discovery
func handleCertificateFound() {
    log.Printf("Certificate found event - setting up MQTT connection")
    
    // Notify API about certificate discovery
    sendStatusUpdate("certificate_found", "Certificates found, starting MQTT connection", map[string]interface{}{
        "certificate_status": "installed",
    })
    
    // Setup MQTT connection
    setupMQTTClient()
}

// setupMQTTClient creates and configures an MQTT client with improved error handling
func setupMQTTClient() {
    // Verify broker connectivity before attempting MQTT connection
    testBrokerConnectivity()
    
    // Create TLS config if certificates exist
    var tlsConfig *tls.Config
    if hasCertificates {
        cert, err := tls.LoadX509KeyPair(CertPath, KeyPath)
        if err != nil {
            log.Printf("WARNING: Error loading certificates: %v", err)
            
            // Check if certificate files exist and have proper permissions
            checkCertificatePermissions()
        } else {
            tlsConfig = &tls.Config{
                ClientCAs:          nil,
                InsecureSkipVerify: true,
                Certificates:       []tls.Certificate{cert},
            }
            log.Printf("TLS certificates loaded successfully")
        }
    }
    
    // Extract broker details for logging
    brokerHost := brokerAddress
    brokerPort := "1883"
    if strings.Contains(brokerAddress, ":") {
        parts := strings.Split(brokerAddress, ":")
        brokerHost = parts[0]
        if len(parts) > 1 {
            brokerPort = parts[1]
        }
    }
    
    // Setup MQTT options with detailed logging
    opts := mqtt.NewClientOptions()
    opts.AddBroker(fmt.Sprintf("tcp://%s", brokerAddress))
    opts.SetClientID(gatewayID)
    opts.SetKeepAlive(60 * time.Second)
    opts.SetPingTimeout(10 * time.Second)
    opts.SetAutoReconnect(true)
    opts.SetMaxReconnectInterval(10 * time.Second)
    opts.SetConnectTimeout(10 * time.Second) // More reasonable timeout
    
    // Add connection handlers
    opts.SetOnConnectHandler(func(client mqtt.Client) {
        log.Printf("MQTT connected successfully to %s", brokerAddress)
        
        // Subscribe to control topic
        controlTopic := fmt.Sprintf("control/%s", gatewayID)
        log.Printf("Subscribing to control topic: %s", controlTopic)
        
        if token := client.Subscribe(controlTopic, 1, func(client mqtt.Client, msg mqtt.Message) {
            log.Printf("Received message on topic %s: %s", msg.Topic(), string(msg.Payload()))
            eventChan <- Event{Type: EventMQTTMessage, Data: msg, Time: time.Now()}
        }); token.Wait() && token.Error() != nil {
            log.Printf("Error subscribing to control topic: %v", token.Error())
        }

        // Subscribe to config update topic
        configTopic := fmt.Sprintf("gateway/%s/config/update", gatewayID)
        log.Printf("Subscribing to config topic: %s", configTopic)
        
        if token := client.Subscribe(configTopic, 1, func(client mqtt.Client, msg mqtt.Message) {
            log.Printf("Received config update on topic %s", msg.Topic())
            eventChan <- Event{Type: EventConfigUpdate, Data: msg, Time: time.Now()}
        }); token.Wait() && token.Error() != nil {
            log.Printf("Error subscribing to config topic: %v", token.Error())
        }
        
        eventChan <- Event{Type: EventMQTTConnected, Time: time.Now()}
    })
    
    opts.SetConnectionLostHandler(func(client mqtt.Client, err error) {
        log.Printf("MQTT connection lost: %v", err)
        eventChan <- Event{Type: EventMQTTDisconnected, Data: err, Time: time.Now()}
    })
    
    // Add default handler for unexpected messages
    opts.SetDefaultPublishHandler(func(client mqtt.Client, msg mqtt.Message) {
        log.Printf("Received unexpected message on topic %s: %s", msg.Topic(), string(msg.Payload()))
    })
    
    // Add TLS config if available
    if tlsConfig != nil {
        opts.SetTLSConfig(tlsConfig)
        log.Printf("MQTT configured with TLS")
    } else {
        log.Printf("MQTT configured without TLS")
    }
    
    // Create client and connect with retry logic
    log.Printf("Attempting MQTT connection to %s:%s", brokerHost, brokerPort)
    mqttClient = mqtt.NewClient(opts)
    
    // Connect with improved error handling and retry
    connectWithRetry(mqttClient, 3)
}

// connectWithRetry attempts to connect to MQTT with retries
func connectWithRetry(client mqtt.Client, maxRetries int) {
    var err error
    
    for attempt := 1; attempt <= maxRetries; attempt++ {
        log.Printf("MQTT connection attempt %d of %d", attempt, maxRetries)
        
        token := client.Connect()
        tokenSuccess := token.WaitTimeout(10 * time.Second)
        
        if !tokenSuccess {
            log.Printf("MQTT connection attempt %d timed out", attempt)
            err = fmt.Errorf("connection timeout")
            time.Sleep(time.Duration(attempt) * time.Second)  // Exponential backoff
            continue
        }
        
        if token.Error() != nil {
            log.Printf("MQTT connection attempt %d failed: %v", attempt, token.Error())
            err = token.Error()
            time.Sleep(time.Duration(attempt) * time.Second)  // Exponential backoff
            continue
        }
        
        // Success
        log.Printf("MQTT connection successful on attempt %d", attempt)
        return
    }
    
    // All attempts failed
    log.Printf("All MQTT connection attempts failed, last error: %v", err)
}

// testBrokerConnectivity tests if the broker is actually accessible before trying MQTT
func testBrokerConnectivity() {
    host := brokerAddress
    port := "1883"
    
    if strings.Contains(brokerAddress, ":") {
        parts := strings.Split(brokerAddress, ":")
        host = parts[0]
        if len(parts) > 1 {
            port = parts[1]
        }
    }
    
    // Try TCP connection to verify broker is reachable
    address := fmt.Sprintf("%s:%s", host, port)
    log.Printf("Testing TCP connectivity to MQTT broker at %s", address)
    
    conn, err := net.DialTimeout("tcp", address, 5*time.Second)
    if err != nil {
        log.Printf("WARNING: Cannot establish TCP connection to MQTT broker at %s: %v", address, err)
        
        // Print network configuration for debugging
        printNetworkInfo()
    } else {
        conn.Close()
        log.Printf("Successfully established TCP connection to MQTT broker at %s", address)
    }
}

// checkCertificatePermissions checks if certificates exist and have correct permissions
func checkCertificatePermissions() {
    // Check certificate file
    if certInfo, err := os.Stat(CertPath); err != nil {
        log.Printf("Certificate file issue at %s: %v", CertPath, err)
    } else {
        mode := certInfo.Mode()
        log.Printf("Certificate file exists with permissions: %v", mode)
    }
    
    // Check key file
    if keyInfo, err := os.Stat(KeyPath); err != nil {
        log.Printf("Key file issue at %s: %v", KeyPath, err)
    } else {
        mode := keyInfo.Mode()
        log.Printf("Key file exists with permissions: %v", mode)
    }
}

// printNetworkInfo prints debugging information about the network configuration
func printNetworkInfo() {
    // Get interfaces
    interfaces, err := net.Interfaces()
    if err != nil {
        log.Printf("Error getting network interfaces: %v", err)
        return
    }
    
    log.Printf("Network interfaces:")
    for _, iface := range interfaces {
        addrs, err := iface.Addrs()
        if err != nil {
            continue
        }
        
        for _, addr := range addrs {
            log.Printf("  Interface %s: %s", iface.Name, addr.String())
        }
    }
    
    // Try to ping common Docker gateway addresses
    log.Printf("Trying to ping common Docker addresses:")
    hosts := []string{"172.28.1.2", "172.17.0.1", "172.17.0.2", "172.17.0.3"}
    for _, host := range hosts {
        cmd := exec.Command("ping", "-c", "1", "-W", "1", host)
        if err := cmd.Run(); err == nil {
            log.Printf("  Successfully pinged %s", host)
        } else {
            log.Printf("  Failed to ping %s", host)
        }
    }
}

// handleMQTTMessage processes messages received on MQTT topics
func handleMQTTMessage(msg mqtt.Message) {
    topic := msg.Topic()

    // Check for configuration-related topics
    if strings.Contains(topic, "/config/") {
        if strings.HasSuffix(topic, "/config/update") {
            eventChan <- Event{Type: EventConfigUpdate, Data: msg, Time: time.Now()}
            return
        }
    }

    // Parse message
    var command map[string]interface{}
    if err := json.Unmarshal(msg.Payload(), &command); err != nil {
        log.Printf("Error parsing MQTT message: %v", err)
        return
    }
    
    // Check command type
    if cmdType, ok := command["type"].(string); ok {
        log.Printf("Received command type: %s", cmdType)
        
        switch cmdType {
        case "acknowledge":
            // Send certificate status and connection info
            log.Printf("Sending acknowledge event as requested")
            certInfo := map[string]interface{}{
                "certificate_status": "installed",
                "tls_enabled": hasCertificates,
                "timestamp": time.Now().Format(time.RFC3339),
            }
            sendStatusUpdate("online", "Gateway online and ready", certInfo)
            
        case "reset":
            // Backend wants us to reset connection
            log.Printf("Resetting connection as requested")
            if isMqttConnected && mqttClient != nil {
                mqttClient.Disconnect(250)
            }
            if hasCertificates {
                setupMQTTClient()
            }
            
        case "delete":
            // Backend wants to delete this gateway
            log.Printf("Received delete command, shutting down")
            // Send a final deletion notice
            sendStatusUpdate("deleted", "Gateway received deletion command", map[string]interface{}{
                "status": "deleted",
            })
            
            // Allow time for message to be delivered
            time.Sleep(500 * time.Millisecond)
            
            eventChan <- Event{Type: EventShutdown, Time: time.Now()}
        }
    }
}

// sendAcknowledgment sends an acknowledgment event to the API
func sendAcknowledgment() {
    log.Printf("Sending acknowledgment to API")
    payload := map[string]string{
        "status": "connected",
        "message": "Gateway connected to MQTT broker",
        "timestamp": time.Now().Format(time.RFC3339),
    }
    
    response, err := sendEventToAPI(gatewayID, "acknowledge", payload)
    if err != nil {
        log.Printf("Error sending acknowledgment: %v", err)
        return
    }
    
    // Check if we have an operation ID to respond to
    if response != nil && response.Gateway.OperationID != "" {
        operationID := response.Gateway.OperationID
        
        // Wait a short time before sending response (simulate processing)
        time.Sleep(2 * time.Second)
        
        // Send success response
        responsePayload := map[string]string{
            "status": "success",
            "operation_id": operationID,
            "message": "Configuration applied successfully",
            "timestamp": time.Now().Format(time.RFC3339),
        }
        
        // Send response event
        if _, err := sendEventToAPI(gatewayID, "response", responsePayload); err != nil {
            log.Printf("Error sending response event: %v", err)
        } else {
            log.Printf("Successfully sent response for operation %s", operationID)
        }
    }
}

// sendHeartbeat sends a heartbeat to both MQTT and API
func sendHeartbeat() {
    timeStr := time.Now().Format(time.RFC3339)
    uptime := getUptime()
    
    // Prepare heartbeat data
    heartbeatData := map[string]interface{}{
        "timestamp": timeStr,
        "uptime": uptime,
        "memory": "75MB",
        "cpu": "5%",
        "tls_enabled": fmt.Sprintf("%v", hasCertificates),
        "status": "online",
        "certificate_status": map[string]string{
            "status": "installed",
            "installed_at": timeStr,
        },
    }
    
    // Convert to JSON for MQTT
    jsonData, err := json.Marshal(heartbeatData)
    if err != nil {
        log.Printf("Error marshaling heartbeat data: %v", err)
        return
    }
    
    // Send to MQTT
    if isMqttConnected && mqttClient != nil {
        topic := fmt.Sprintf("gateway/%s/heartbeat", gatewayID)
        token := mqttClient.Publish(topic, 0, false, jsonData)
        token.Wait()
        log.Printf("Published heartbeat to MQTT topic: %s", topic)
    }
    
    // Send to API
    sendEventToAPI(gatewayID, "heartbeat", heartbeatData)
}

// sendStatusUpdate sends a status update to the API
func sendStatusUpdate(status string, message string, additionalData ...map[string]interface{}) {
    payload := map[string]interface{}{
        "status": status,
        "message": message,
        "timestamp": time.Now().Format(time.RFC3339),
    }
    
    // Merge additional data if provided
    if len(additionalData) > 0 && additionalData[0] != nil {
        for k, v := range additionalData[0] {
            payload[k] = v
        }
    }
    
    sendEventToAPI(gatewayID, "status", payload)
}

// generateMeasurement creates a simulated measurement
func (dm *DeviceManager) generateMeasurement(device *ConfiguredEndDevice) map[string]interface{} {
	// Get measurement parameters from configuration
	var minWeight float64 = 0.1
	var maxWeight float64 = 25.0
	var precision float64 = 0.1
	
	if measurementConfig, ok := device.DeviceConfig["measurement"].(map[string]interface{}); ok {
		if min, ok := measurementConfig["min_weight_kg"].(float64); ok {
			minWeight = min
		}
		if max, ok := measurementConfig["max_weight_kg"].(float64); ok {
			maxWeight = max
		}
		if prec, ok := measurementConfig["precision"].(float64); ok {
			precision = prec
		}
	}
	
	// Get materials from configuration
	materials := []string{"Plastic", "Paper", "Glass", "Metal", "Organic"}
	if materialsConfig, ok := device.DeviceConfig["materials"].([]interface{}); ok && len(materialsConfig) > 0 {
		materials = make([]string, 0, len(materialsConfig))
		for _, m := range materialsConfig {
			if material, ok := m.(map[string]interface{}); ok {
				if name, ok := material["name"].(string); ok {
					materials = append(materials, name)
				}
			} else if name, ok := m.(string); ok {
				materials = append(materials, name)
			}
		}
	}
	
	// Get vendors from configuration
	vendors := []string{"Vendor A", "Vendor B", "Vendor C"}
	if vendorsConfig, ok := device.DeviceConfig["vendors"].([]interface{}); ok && len(vendorsConfig) > 0 {
		vendors = make([]string, 0, len(vendorsConfig))
		for _, v := range vendorsConfig {
			if name, ok := v.(string); ok {
				vendors = append(vendors, name)
			}
		}
	}
	
	// Generate random weight value
	// Convert to precision level
	precisionMultiplier := 1.0 / precision
	randomValue := minWeight + rand.Float64()*(maxWeight-minWeight)
	roundedValue := math.Round(randomValue*precisionMultiplier) / precisionMultiplier
	
	// Pick random material and vendor
	material := materials[rand.Intn(len(materials))]
	vendor := vendors[rand.Intn(len(vendors))]
	
	// Update device timestamp
	now := time.Now()
	device.LastMeasurement = now
	
	// Create measurement payload (similar to MQTTEvent format)
	return map[string]interface{}{
		"gateway_id": gatewayID,
		"device_id":  device.ID,
		"event_type": "measurement",
		"type":       "weight_measurement",
		"timestamp":  now.Format(time.RFC3339),
		"payload": map[string]interface{}{
			"weight_kg": roundedValue,
			"material":  material,
			"vendor":    vendor,
		},
	}
}

// publishMeasurement publishes a measurement via MQTT
func (dm *DeviceManager) publishMeasurement(device *ConfiguredEndDevice, measurement map[string]interface{}) {
	// Only publish if connected to MQTT
	if !isMqttConnected || mqttClient == nil {
		log.Printf("Cannot publish measurement: MQTT not connected")
		return
	}
	
	// Convert to JSON
	jsonData, err := json.Marshal(measurement)
	if err != nil {
		log.Printf("Error marshaling measurement: %v", err)
		return
	}
	
	// Create topic
	topic := fmt.Sprintf("gateway/%s/device/%s/measurement", gatewayID, device.ID)
	
	// Publish to MQTT
	token := mqttClient.Publish(topic, 0, false, jsonData)
	token.Wait()
	
	if token.Error() != nil {
		log.Printf("Error publishing measurement: %v", token.Error())
	} else {
		payload, _ := measurement["payload"].(map[string]interface{})
		if payload != nil {
			weight, _ := payload["weight_kg"].(float64)
			material, _ := payload["material"].(string)
			log.Printf("Published measurement from device %s: %.1f kg of %s", 
				device.ID, weight, material)
		}
	}
}

// sendMeasurementToGateway sends a measurement to the gateway's HTTP endpoint
func (dm *DeviceManager) sendMeasurementToGateway(device *ConfiguredEndDevice, measurement map[string]interface{}) {
	// In a real implementation, this would be an HTTP POST to the gateway
	// For our simulation, we'll just log it
	payload, _ := measurement["payload"].(map[string]interface{})
	if payload != nil {
		weight, _ := payload["weight_kg"].(float64)
		material, _ := payload["material"].(string)
		log.Printf("Device %s sent measurement to gateway HTTP endpoint: %.1f kg of %s", 
			device.ID, weight, material)
	}
}

// GetDeviceStatus returns status information for all devices
func (dm *DeviceManager) GetDeviceStatus() []map[string]interface{} {
	dm.DeviceMutex.RLock()
	defer dm.DeviceMutex.RUnlock()
	
	devices := make([]map[string]interface{}, 0, len(dm.Devices))
	
	for _, device := range dm.Devices {
		status := map[string]interface{}{
			"id":                device.ID,
			"type":              device.Type,
			"status":            device.Status,
			"last_config_fetch": device.LastConfigFetch,
			"config_version":    device.ConfigVersion,
			"last_measurement":  device.LastMeasurement,
		}
		devices = append(devices, status)
	}
	
	return devices
}

// Shutdown stops all device simulations
func (dm *DeviceManager) Shutdown() {
	dm.DeviceMutex.Lock()
	defer dm.DeviceMutex.Unlock()
	
	for id, device := range dm.Devices {
		close(device.StopChan)
		log.Printf("Stopped simulation for device %s", id)
	}
}

// GatewayInfo represents information about a gateway from API responses
type GatewayInfo struct {
    GatewayID   string `json:"gateway_id"`
    Status      string `json:"status"`
    OperationID string `json:"operation_id"`
}

// ApiResponse represents a response from the API
type ApiResponse struct {
    Status  string     `json:"status"`
    Gateway GatewayInfo `json:"gateway"`
}

// sendEventToAPI sends an event to the API
func sendEventToAPI(gatewayID string, eventType string, payload interface{}) (*ApiResponse, error) {
    // Get API URL with adaptive handling
    apiURL := setupApiUrl()
    
    // Create event
    event := MQTTEvent{
        GatewayID: gatewayID,
        EventType: eventType,
        Payload:   payload,
        Timestamp: time.Now().Format(time.RFC3339),
    }
    
    // Convert to JSON
    jsonData, err := json.Marshal(event)
    if err != nil {
        log.Printf("Error marshaling event data: %v", err)
        return nil, err
    }
    
    // Send to API
    url := fmt.Sprintf("%s/api/mqtt/events", apiURL)
    log.Printf("Sending %s event to API: %s", eventType, url)
    
    // Create client with timeout
    client := &http.Client{
        Timeout: 5 * time.Second,
    }
    
    resp, err := client.Post(url, "application/json", bytes.NewBuffer(jsonData))
    if err != nil {
        log.Printf("Error sending event to API: %v", err)
        return nil, err
    }
    defer resp.Body.Close()
    
    if resp.StatusCode >= 200 && resp.StatusCode < 300 {
        log.Printf("Successfully sent %s event to API", eventType)
        
        // Parse response body
        var apiResp ApiResponse
        if err := json.NewDecoder(resp.Body).Decode(&apiResp); err == nil {
            return &apiResp, nil
        } else {
            log.Printf("Warning: Could not parse API response: %v", err)
            return nil, nil
        }
    } else {
        // Try to read error response
        respBody, _ := ioutil.ReadAll(resp.Body)
        log.Printf("API returned status code: %d, body: %s", resp.StatusCode, string(respBody))
        return nil, fmt.Errorf("API returned status code: %d", resp.StatusCode)
    }
}

// fileExists checks if a file exists
func fileExists(filename string) bool {
    info, err := os.Stat(filename)
    if os.IsNotExist(err) {
        return false
    }
    return !info.IsDir()
}

// getUptime returns the uptime as a string
func getUptime() string {
    uptime := os.Getenv("UPTIME")
    if uptime == "" {
        return fmt.Sprintf("%ds", time.Now().Unix()%86400)
    }
    return uptime
}

func handleConfigRequest(w http.ResponseWriter, r *http.Request) {
    // Only allow GET requests for end devices
    if r.Method != http.MethodGet && r.Method != http.MethodPost {
        http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
        return
    }
    
    // Get the current configuration
    config := getConfig()
    
    // Check if we have a configuration
    if config.YAML == "" {
        http.Error(w, "No configuration available", http.StatusNotFound)
        return
    }
    
    // Set appropriate content type and send the YAML config
    w.Header().Set("Content-Type", "application/x-yaml")
    w.WriteHeader(http.StatusOK)
    fmt.Fprintf(w, "%s", config.YAML)
    
    log.Printf("Served configuration to end device (IP: %s)", r.RemoteAddr)
}
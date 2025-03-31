package main

import (
    "bytes"
    "crypto/tls"
    "encoding/json"
    "fmt"
    "io/ioutil"
    "log"
    "net/http"
    "os"
    "os/signal"
    "syscall"
    "time"
    "os/exec"

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
    CertPath          = "/app/certs/cert.pem"
    KeyPath           = "/app/certs/key.pem"
    CheckInterval     = 5 * time.Second
    HeartbeatInterval = 20 * time.Second
)

// Global variables
var (
    gatewayID       string
    brokerAddress   string
    mqttClient      mqtt.Client
    eventChan       chan Event = make(chan Event, 100) // Buffered channel for events
    hasCertificates bool = false
    isMqttConnected bool = false
    mtx             http.ServeMux
)

func main() {
    log.SetFlags(log.LstdFlags | log.Lmicroseconds)
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
    
    // Detect WSL or Docker Desktop (Windows/Mac) environment
    isDockerDesktop := false
    _, err := os.Stat("/proc/sys/fs/binfmt_misc/WSLInterop")
    if err == nil {
        // We're in WSL
        isDockerDesktop = true
        log.Printf("WSL environment detected")
    }
    
    // Try to ping host.docker.internal as another detection method
    cmd := exec.Command("ping", "-c", "1", "-W", "1", "host.docker.internal")
    if err := cmd.Run(); err == nil {
        // host.docker.internal is pingable, must be Docker Desktop
        isDockerDesktop = true
        log.Printf("host.docker.internal is reachable, Docker Desktop detected")
    }
    
    // Logic for setting broker address
    if envBroker == "mqtt-broker:1883" && isDockerDesktop {
        // For Docker Desktop environments, when mqtt-broker:1883 is specified, use host.docker.internal
        brokerAddress = "host.docker.internal:1883"
        log.Printf("Docker Desktop environment detected, using host.docker.internal:1883")
    } else if envBroker != "" {
        // For any other environment variable, use it as is
        brokerAddress = envBroker
        log.Printf("Using MQTT broker address from environment: %s", brokerAddress)
    } else {
        // Default fallback, just use mqtt-broker:1883
        brokerAddress = "mqtt-broker:1883"
        log.Printf("No MQTT broker address specified in environment, using default: %s", brokerAddress)
    }
    
    log.Printf("Final MQTT broker address: %s", brokerAddress)
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

// startHTTPServer initializes and starts the HTTP server
func startHTTPServer() {
    mtx.HandleFunc("/status", handleStatusRequest)
    mtx.HandleFunc("/health", handleHealthRequest)
    mtx.HandleFunc("/reset", handleResetRequest)
    
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
    fmt.Fprintf(w, "API URL: %s\n", os.Getenv("API_URL"))
    
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
            
        case EventShutdown:
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

// setupMQTTClient creates and configures an MQTT client
func setupMQTTClient() {
    // Create TLS config if certificates exist
    var tlsConfig *tls.Config
    if hasCertificates {
        cert, err := tls.LoadX509KeyPair(CertPath, KeyPath)
        if err != nil {
            log.Printf("WARNING: Error loading certificates: %v", err)
        } else {
            tlsConfig = &tls.Config{
                ClientCAs:          nil,
                InsecureSkipVerify: true,
                Certificates:       []tls.Certificate{cert},
            }
            log.Printf("TLS certificates loaded successfully")
        }
    }
    
    // Setup MQTT options
    opts := mqtt.NewClientOptions()
    opts.AddBroker(fmt.Sprintf("tcp://%s", brokerAddress))
    opts.SetClientID(gatewayID)
    opts.SetKeepAlive(60 * time.Second)
    opts.SetPingTimeout(10 * time.Second)
    opts.SetAutoReconnect(true)
    opts.SetMaxReconnectInterval(10 * time.Second)
    opts.SetOnConnectHandler(func(client mqtt.Client) {
        log.Printf("MQTT connected")
        
        // Subscribe to control topic
        controlTopic := fmt.Sprintf("control/%s", gatewayID)
        log.Printf("Subscribing to control topic: %s", controlTopic)
        
        if token := client.Subscribe(controlTopic, 1, func(client mqtt.Client, msg mqtt.Message) {
            log.Printf("Received message on topic %s: %s", msg.Topic(), string(msg.Payload()))
            eventChan <- Event{Type: EventMQTTMessage, Data: msg, Time: time.Now()}
        }); token.Wait() && token.Error() != nil {
            log.Printf("Error subscribing to control topic: %v", token.Error())
        }
        
        eventChan <- Event{Type: EventMQTTConnected, Time: time.Now()}
    })
    opts.SetConnectionLostHandler(func(client mqtt.Client, err error) {
        log.Printf("MQTT connection lost: %v", err)
        eventChan <- Event{Type: EventMQTTDisconnected, Data: err, Time: time.Now()}
    })
    
    // Add TLS config if available
    if tlsConfig != nil {
        opts.SetTLSConfig(tlsConfig)
        log.Printf("MQTT configured with TLS")
    }
    
    // Create client and connect
    mqttClient = mqtt.NewClient(opts)
    if token := mqttClient.Connect(); token.Wait() && token.Error() != nil {
        log.Printf("MQTT connection error: %v", token.Error())
    }
}

// handleMQTTMessage processes messages received on MQTT topics
func handleMQTTMessage(msg mqtt.Message) {
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
    // Get API URL from environment or use default
    apiURL := os.Getenv("API_URL")
    if apiURL == "" || apiURL == "http://0.0.0.0:8000" || apiURL == "https://0.0.0.0:8000" {
        // Use a more reliable default that works in Docker
        apiURL = "http://host.docker.internal:8000"
        log.Printf("API_URL is not set or using 0.0.0.0, using %s instead", apiURL)
    }
    
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
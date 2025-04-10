package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io/ioutil"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"
	"gopkg.in/yaml.v3"
)

// Configuration structs
type Config struct {
	MQTT  MQTTConfig  `yaml:"mqtt"`
	API   APIConfig   `yaml:"api"`
	Rules []RuleConfig `yaml:"rules"`
}

type MQTTConfig struct {
	Host     string `yaml:"host"`
	Port     int    `yaml:"port"`
	ClientID string `yaml:"client_id"`
	Username string `yaml:"username"`
	Password string `yaml:"password"`
}

type APIConfig struct {
	BaseURL string `yaml:"base_url"`
}

type RuleConfig struct {
	Name         string        `yaml:"name"`
	Description  string        `yaml:"description"`
	TopicPattern string        `yaml:"topic_pattern"`
	Enabled      bool          `yaml:"enabled"`
	SQL          string        `yaml:"sql"`
	Transform    string        `yaml:"transform"`
	Actions      []ActionConfig `yaml:"actions"`
}

type ActionConfig struct {
	Type      string                 `yaml:"type"`
	URL       string                 `yaml:"url"`
	Method    string                 `yaml:"method"`
	Headers   map[string]string      `yaml:"headers"`
	Timeout   int                    `yaml:"timeout"`
	Function  string                 `yaml:"function"`
	Topic     string                 `yaml:"topic"`
	QoS       int                    `yaml:"qos"`
	Retain    bool                   `yaml:"retain"`
	Payload   map[string]interface{} `yaml:"payload"`
}

// Configuration message types
type ConfigUpdateMessage struct {
	GatewayID  string `json:"gateway_id"`
	YAMLConfig string `json:"yaml_config"`
	Timestamp  string `json:"timestamp"`
}

type ConfigRequestMessage struct {
	Timestamp string `json:"timestamp"`
}

type ConfigDeliveryMessage struct {
	Status    string `json:"status"`
	Timestamp string `json:"timestamp"`
}

// Rule represents a processing rule for MQTT messages
type Rule struct {
	Name         string
	Description  string
	TopicPattern string
	Enabled      bool
	SQL          string
	Transform    string
	Actions      []ActionConfig
}

// MatchesTopic checks if a topic matches the rule's pattern
func (r *Rule) MatchesTopic(topic string) bool {
	// Handle direct match
	if r.TopicPattern == topic {
		return true
	}

	// Handle wildcard '#' (multi-level)
	if strings.HasSuffix(r.TopicPattern, "/#") {
		prefix := strings.TrimSuffix(r.TopicPattern, "/#")
		return strings.HasPrefix(topic, prefix)
	}

	// Handle wildcard '+' (single-level)
	if strings.Contains(r.TopicPattern, "+") {
		patternParts := strings.Split(r.TopicPattern, "/")
		topicParts := strings.Split(topic, "/")

		if len(patternParts) != len(topicParts) {
			return false
		}

		for i := range patternParts {
			if patternParts[i] != "+" && patternParts[i] != topicParts[i] {
				return false
			}
		}
		return true
	}

	// Simple wildcard '#' for all topics
	if r.TopicPattern == "#" {
		return true
	}

	return false
}

// ShouldProcessMessage determines if a message should be processed by this rule
func (r *Rule) ShouldProcessMessage(topic string, payload map[string]interface{}) bool {
	if !r.Enabled {
		return false
	}

	if !r.MatchesTopic(topic) {
		return false
	}

	// TODO: Add SQL query evaluation if needed
	// This would be a more complex implementation to support filtering

	return true
}

// RulesEngine manages MQTT message processing rules
type RulesEngine struct {
	Config          Config
	Rules           []*Rule
	MQTTClient      mqtt.Client
	RepublishClient mqtt.Client
	ExitChan        chan struct{}
	WaitGroup       sync.WaitGroup
	ConfigStorage   map[string]string // Maps gateway_id to YAML config
	ConfigMutex     sync.RWMutex      // Protects access to ConfigStorage
}

// NewRulesEngine creates a new RulesEngine
func NewRulesEngine(configPath string) (*RulesEngine, error) {
	config, err := loadConfig(configPath)
	if err != nil {
		return nil, err
	}

	// Initialize rules from config
	rules := make([]*Rule, 0, len(config.Rules))
	for _, ruleConfig := range config.Rules {
		if ruleConfig.Enabled {
			rule := &Rule{
				Name:         ruleConfig.Name,
				Description:  ruleConfig.Description,
				TopicPattern: ruleConfig.TopicPattern,
				Enabled:      ruleConfig.Enabled,
				SQL:          ruleConfig.SQL,
				Transform:    ruleConfig.Transform,
				Actions:      ruleConfig.Actions,
			}
			rules = append(rules, rule)
		}
	}

	return &RulesEngine{
		Config:        config,
		Rules:         rules,
		ExitChan:      make(chan struct{}),
		WaitGroup:     sync.WaitGroup{},
		ConfigStorage: make(map[string]string),
	}, nil
}

// Start starts the rules engine
func (engine *RulesEngine) Start() error {
	log.Println("Starting IoT Rules Engine")

	// Setup MQTT client
	if err := engine.setupMQTTClient(); err != nil {
		return fmt.Errorf("failed to setup MQTT client: %v", err)
	}

	// Setup republish client if needed
	if engine.needsRepublishClient() {
		if err := engine.setupRepublishClient(); err != nil {
			return fmt.Errorf("failed to setup republish client: %v", err)
		}
	}

	// Handle graceful shutdown
	signalChan := make(chan os.Signal, 1)
	signal.Notify(signalChan, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		sig := <-signalChan
		log.Printf("Received signal %v, shutting down...", sig)
		close(engine.ExitChan)
	}()

	// Wait for exit signal
	<-engine.ExitChan

	// Clean up
	engine.Shutdown()

	return nil
}

// Shutdown cleans up resources
func (engine *RulesEngine) Shutdown() {
	log.Println("Shutting down IoT Rules Engine")

	// Disconnect MQTT clients
	if engine.MQTTClient != nil && engine.MQTTClient.IsConnected() {
		engine.MQTTClient.Disconnect(250)
	}

	if engine.RepublishClient != nil && engine.RepublishClient.IsConnected() {
		engine.RepublishClient.Disconnect(250)
	}

	// Wait for all goroutines to finish
	engine.WaitGroup.Wait()

	log.Println("IoT Rules Engine shutdown complete")
}

// needsRepublishClient checks if any rule needs to republish messages
func (engine *RulesEngine) needsRepublishClient() bool {
	for _, rule := range engine.Rules {
		for _, action := range rule.Actions {
			if action.Type == "republish" {
				return true
			}
		}
	}
	return false
}

// setupMQTTClient sets up the MQTT client
func (engine *RulesEngine) setupMQTTClient() error {
	log.Printf("Setting up MQTT client to connect to %s:%d", engine.Config.MQTT.Host, engine.Config.MQTT.Port)

	// Create options
	opts := mqtt.NewClientOptions()
	opts.AddBroker(fmt.Sprintf("tcp://%s:%d", engine.Config.MQTT.Host, engine.Config.MQTT.Port))
	
	// Set client ID with uniqueness if not provided
	clientID := engine.Config.MQTT.ClientID
	if clientID == "" {
		clientID = fmt.Sprintf("rules-engine-%d", time.Now().Unix())
	}
	opts.SetClientID(clientID)
	
	// Set credentials if provided
	if engine.Config.MQTT.Username != "" && engine.Config.MQTT.Password != "" {
		opts.SetUsername(engine.Config.MQTT.Username)
		opts.SetPassword(engine.Config.MQTT.Password)
	}
	
	// Set handlers
	opts.SetOnConnectHandler(engine.onConnect)
	opts.SetConnectionLostHandler(engine.onConnectionLost)
	opts.SetDefaultPublishHandler(engine.defaultMessageHandler)
	
	// Set other options
	opts.SetKeepAlive(60 * time.Second)
	opts.SetPingTimeout(10 * time.Second)
	opts.SetAutoReconnect(true)
	opts.SetMaxReconnectInterval(10 * time.Second)
	
	// Create and connect client
	engine.MQTTClient = mqtt.NewClient(opts)
	token := engine.MQTTClient.Connect()
	if token.Wait() && token.Error() != nil {
		return fmt.Errorf("error connecting to MQTT broker: %v", token.Error())
	}
	
	return nil
}

// setupRepublishClient sets up a separate MQTT client for republishing messages
func (engine *RulesEngine) setupRepublishClient() error {
	log.Println("Setting up MQTT client for republishing messages")

	// Create options
	opts := mqtt.NewClientOptions()
	opts.AddBroker(fmt.Sprintf("tcp://%s:%d", engine.Config.MQTT.Host, engine.Config.MQTT.Port))
	
	// Set client ID with uniqueness
	clientID := fmt.Sprintf("%s-republish", engine.Config.MQTT.ClientID)
	if engine.Config.MQTT.ClientID == "" {
		clientID = fmt.Sprintf("rules-engine-republish-%d", time.Now().Unix())
	}
	opts.SetClientID(clientID)
	
	// Set credentials if provided
	if engine.Config.MQTT.Username != "" && engine.Config.MQTT.Password != "" {
		opts.SetUsername(engine.Config.MQTT.Username)
		opts.SetPassword(engine.Config.MQTT.Password)
	}
	
	// Set other options
	opts.SetKeepAlive(60 * time.Second)
	opts.SetPingTimeout(10 * time.Second)
	opts.SetAutoReconnect(true)
	opts.SetMaxReconnectInterval(10 * time.Second)
	
	// Create and connect client
	engine.RepublishClient = mqtt.NewClient(opts)
	token := engine.RepublishClient.Connect()
	if token.Wait() && token.Error() != nil {
		return fmt.Errorf("error connecting republish client to MQTT broker: %v", token.Error())
	}
	
	return nil
}

// onConnect is called when the MQTT client connects
func (engine *RulesEngine) onConnect(client mqtt.Client) {
	log.Println("Connected to MQTT broker")

	// Get a unique set of topic patterns to subscribe to
	topics := make(map[string]byte)
	for _, rule := range engine.Rules {
		if rule.Enabled {
			topics[rule.TopicPattern] = 0 // QoS 0
		}
	}

	// Subscribe to each unique topic
	for topic, qos := range topics {
		log.Printf("Subscribing to topic: %s", topic)
		token := client.Subscribe(topic, qos, engine.messageHandler)
		if token.Wait() && token.Error() != nil {
			log.Printf("Error subscribing to topic %s: %v", topic, token.Error())
		}
	}
}

// onConnectionLost is called when the MQTT connection is lost
func (engine *RulesEngine) onConnectionLost(client mqtt.Client, err error) {
	log.Printf("Connection to MQTT broker lost: %v", err)
}

// defaultMessageHandler handles unexpected messages
func (engine *RulesEngine) defaultMessageHandler(client mqtt.Client, msg mqtt.Message) {
	topic := msg.Topic()
	
	// More descriptive logging for config-related topics
    if strings.Contains(topic, "config") {
        log.Printf("Config-related message on topic: %s", topic)
        return
    }
    
	log.Printf("Received unexpected message on topic %s", msg.Topic())
}

// messageHandler handles MQTT messages
func (engine *RulesEngine) messageHandler(client mqtt.Client, msg mqtt.Message) {
	topic := msg.Topic()
	payload := msg.Payload()

	log.Printf("Received message on topic: %s", topic)

	// Parse JSON payload
	var payloadMap map[string]interface{}
	if err := json.Unmarshal(payload, &payloadMap); err != nil {
		log.Printf("Error parsing message payload as JSON: %v", err)
		// If not JSON, create a simple payload with raw content
		payloadMap = map[string]interface{}{
			"raw": string(payload),
		}
	}

	// Check each rule
	for _, rule := range engine.Rules {
		if rule.ShouldProcessMessage(topic, payloadMap) {
			log.Printf("Rule '%s' matched for topic: %s", rule.Name, topic)
			
			// Process the message with this rule
			engine.processMessage(rule, topic, payloadMap)
		}
	}
}

// processMessage processes a message according to a rule
func (engine *RulesEngine) processMessage(rule *Rule, topic string, payload map[string]interface{}) {
	// Apply transformation if configured (placeholder for now)
	processedPayload := payload
	if rule.Transform != "" {
		log.Printf("Transform '%s' not implemented yet, using original payload", rule.Transform)
	}

	// Execute actions
	for _, action := range rule.Actions {
		switch action.Type {
		case "http":
			engine.executeHTTPAction(action, topic, processedPayload)
		case "republish":
			engine.executeRepublishAction(action, topic, processedPayload)
		case "lambda":
			engine.executeLambdaAction(action, topic, processedPayload)
		case "function": // New action type
			engine.executeFunctionAction(action, topic, processedPayload)
		default:
			log.Printf("Unknown action type: %s", action.Type)
		}
	}
}

// executeHTTPAction executes an HTTP action
func (engine *RulesEngine) executeHTTPAction(action ActionConfig, topic string, payload map[string]interface{}) {
	// Start a new goroutine for HTTP request to avoid blocking
	engine.WaitGroup.Add(1)
	go func() {
		defer engine.WaitGroup.Done()

		url := action.URL
		method := action.Method
		if method == "" {
			method = "POST" // Default to POST
		}

		// Prepare headers
		headers := action.Headers
		if headers == nil {
			headers = map[string]string{
				"Content-Type": "application/json",
			}
		}

		// Prepare timeout
		timeout := action.Timeout
		if timeout == 0 {
			timeout = 10 // Default to 10 seconds
		}

		// Extract gateway_id from topic if possible (expected format: gateway/{gateway_id}/...)
		topicParts := strings.Split(topic, "/")
		gatewayID := ""
		eventType := ""
		if len(topicParts) >= 2 && topicParts[0] == "gateway" {
			gatewayID = topicParts[1]
		}
		
		// Extract event_type from topic if possible
		if len(topicParts) >= 3 {
			eventType = topicParts[2]
		}

		// Prepare the request payload
		requestPayload := map[string]interface{}{
			"topic":    topic,
			"payload":  payload,
			"timestamp": time.Now().Format(time.RFC3339),
		}

		// Add gateway_id and event_type for FastAPI backend compatibility
		if gatewayID != "" {
			requestPayload["gateway_id"] = gatewayID
		}
		if eventType != "" {
			requestPayload["event_type"] = eventType
		}

		// Convert to JSON
		jsonPayload, err := json.Marshal(requestPayload)
		if err != nil {
			log.Printf("Error marshaling HTTP request payload: %v", err)
			return
		}

		log.Printf("Executing HTTP %s request to %s", method, url)

		// Create HTTP client with timeout
		client := &http.Client{
			Timeout: time.Duration(timeout) * time.Second,
		}

		// Create request
		req, err := http.NewRequest(method, url, bytes.NewBuffer(jsonPayload))
		if err != nil {
			log.Printf("Error creating HTTP request: %v", err)
			return
		}

		// Set headers
		for key, value := range headers {
			req.Header.Set(key, value)
		}

		// Execute request
		resp, err := client.Do(req)
		if err != nil {
			log.Printf("Error executing HTTP request: %v", err)
			return
		}
		defer resp.Body.Close()

		// Check response
		if resp.StatusCode >= 200 && resp.StatusCode < 300 {
			log.Printf("HTTP request successful: %d", resp.StatusCode)
		} else {
			body, _ := ioutil.ReadAll(resp.Body)
			log.Printf("HTTP request failed: %d - %s", resp.StatusCode, string(body))
		}
	}()
}

// executeRepublishAction executes a republish action
func (engine *RulesEngine) executeRepublishAction(action ActionConfig, originalTopic string, payload map[string]interface{}) {
	if engine.RepublishClient == nil || !engine.RepublishClient.IsConnected() {
		log.Println("Republish client not available")
		return
	}

	// Get target topic
	targetTopic := action.Topic
	if targetTopic == "" {
		log.Println("Republish action missing target topic")
		return
	}

	// Apply topic transformations
	if strings.Contains(targetTopic, "{original_topic}") {
		targetTopic = strings.Replace(targetTopic, "{original_topic}", originalTopic, -1)
	}

	// Get QoS and retain flag
	qos := byte(action.QoS)
	retain := action.Retain

	// Convert payload to JSON
	jsonPayload, err := json.Marshal(payload)
	if err != nil {
		log.Printf("Error marshaling republish payload: %v", err)
		return
	}

	log.Printf("Republishing message to topic: %s", targetTopic)
	
	// Publish message
	token := engine.RepublishClient.Publish(targetTopic, qos, retain, jsonPayload)
	token.Wait()
	
	if token.Error() != nil {
		log.Printf("Error republishing message: %v", token.Error())
	}
}

// executeLambdaAction executes a Lambda action (simulated)
func (engine *RulesEngine) executeLambdaAction(action ActionConfig, topic string, payload map[string]interface{}) {
	// This is a simulation of Lambda execution since we're not in AWS
	functionName := action.Function
	if functionName == "" {
		functionName = "unknown"
	}
	
	log.Printf("Simulated Lambda invocation of '%s' for rule", functionName)
	
	// In a real AWS environment, this would invoke a Lambda function
	// For now, we just log it
}

// executeFunctionAction executes a function action
func (engine *RulesEngine) executeFunctionAction(action ActionConfig, topic string, payload map[string]interface{}) {
    functionName := action.Function
    
    switch functionName {
    case "handleConfigRequest":
        engine.handleConfigRequest(topic, payload)
    case "handleNewConfig":
        engine.handleNewConfig(topic, payload)
    default:
        log.Printf("Unknown function action: %s", functionName)
    }
}

// handleConfigRequest processes a configuration request from a gateway
func (engine *RulesEngine) handleConfigRequest(topic string, payload map[string]interface{}) {
    // Extract gateway ID from topic (format: gateway/<gateway_id>/request_config)
    parts := strings.Split(topic, "/")
    if len(parts) != 3 {
        log.Printf("Invalid config request topic format: %s", topic)
        return
    }

    gatewayID := parts[1]
    log.Printf("Received configuration request from gateway %s", gatewayID)

    // Check if we have a configuration for this gateway
    engine.ConfigMutex.RLock()
    yamlConfig, exists := engine.ConfigStorage[gatewayID]
    engine.ConfigMutex.RUnlock()

    if !exists {
        log.Printf("No configuration available for gateway %s", gatewayID)
        return
    }

    // Send configuration to gateway
    configTopic := fmt.Sprintf("gateway/%s/config/update", gatewayID)
    log.Printf("Sending configuration to gateway %s", gatewayID)

    if engine.RepublishClient != nil && engine.RepublishClient.IsConnected() {
        token := engine.RepublishClient.Publish(configTopic, 0, false, yamlConfig)
        token.Wait()

        if token.Error() != nil {
            log.Printf("Error sending configuration: %v", token.Error())
        }
    } else {
        log.Printf("Cannot send configuration: republish client not available")
    }
}

// handleNewConfig processes a new configuration from the backend
func (engine *RulesEngine) handleNewConfig(topic string, payload map[string]interface{}) {
    gatewayID, ok := payload["gateway_id"].(string)
    if !ok || gatewayID == "" {
        log.Printf("Invalid config message: missing gateway_id")
        return
    }
    
    yamlConfig, ok := payload["yaml_config"].(string)
    if !ok || yamlConfig == "" {
        log.Printf("Invalid config message: missing yaml_config")
        return
    }

    log.Printf("Received new configuration for gateway %s (%d bytes)", 
               gatewayID, len(yamlConfig))

    // Store the configuration
    engine.ConfigMutex.Lock()
    engine.ConfigStorage[gatewayID] = yamlConfig
    engine.ConfigMutex.Unlock()

    log.Printf("Configuration stored for gateway %s, waiting for gateway request", gatewayID)
}

// loadConfig loads the configuration from a file
func loadConfig(configPath string) (Config, error) {
	var config Config

	// Read config file
	data, err := ioutil.ReadFile(configPath)
	if err != nil {
		return config, fmt.Errorf("error reading config file: %v", err)
	}

	// Parse YAML
	err = yaml.Unmarshal(data, &config)
	if err != nil {
		return config, fmt.Errorf("error parsing config file: %v", err)
	}

	return config, nil
}

func main() {
	// Parse command line flags
	configPath := flag.String("config", "config.yaml", "Path to configuration file")
	verbose := flag.Bool("verbose", false, "Enable verbose logging")
	flag.Parse()

	// Configure logging
	if *verbose {
		log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds | log.Lshortfile)
	} else {
		log.SetFlags(log.Ldate | log.Ltime)
	}

	// Resolve config path
	absConfigPath, err := filepath.Abs(*configPath)
	if err != nil {
		log.Fatalf("Error resolving config path: %v", err)
	}

	log.Printf("Using configuration file: %s", absConfigPath)

	// Create and start the rules engine
	engine, err := NewRulesEngine(absConfigPath)
	if err != nil {
		log.Fatalf("Error creating rules engine: %v", err)
	}

	if err := engine.Start(); err != nil {
		log.Fatalf("Error starting rules engine: %v", err)
	}
}
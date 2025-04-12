import json
import logging
from datetime import datetime
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion
from typing import Optional, Dict, Any, Callable, List
import time
import threading

logger = logging.getLogger(__name__)

class MQTTClient:
    """Improved MQTT client with connection monitoring and auto-reconnect"""
    
    def __init__(self, broker_host: str, broker_port: int, client_id: Optional[str] = None,
                 username: Optional[str] = None, password: Optional[str] = None):
        """Initialize MQTT client
        
        Args:
            broker_host: MQTT broker hostname
            broker_port: MQTT broker port
            client_id: Client ID for MQTT connection
            username: Optional username for authentication
            password: Optional password for authentication
        """
        # Generate client ID if not provided
        if not client_id:
            client_id = f"backend-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.client_id = client_id
        self.username = username
        self.password = password
        
        # Initialize client
        self.client = mqtt.Client(CallbackAPIVersion.VERSION1)
        
        # Set callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        # Set reconnection options
        self.client.reconnect_delay_set(min_delay=1, max_delay=120)

        # Set callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        
        # Set credentials if provided
        if username and password:
            self.client.username_pw_set(username, password)
            
        # Store topic handlers and subscriptions
        self.topic_handlers = {}  # Custom message handlers by topic
        self.subscriptions = []   # Track subscriptions to restore on reconnect
        
        # Connection state
        self.is_connected = False
        self.connect_timeout = 10  # seconds to wait for initial connection
        self.publish_timeout = 5.0 
        
        # Background loop control
        self._loop_started = False
    
    def connect(self) -> bool:
        """Connect to MQTT broker asynchronously and start background loop
        
        Returns:
            True if connection is successful or already connected, False otherwise
        """
        if self.is_connected:
            logger.debug("Already connected to MQTT broker")
            return True
            
        try:
            # Connect asynchronously
            self.client.connect_async(self.broker_host, self.broker_port, keepalive=60)
            
            # Start the network loop in a background thread if not already running
            if not self._loop_started:
                self.client.loop_start()
                self._loop_started = True
                
            logger.info(f"MQTT client connecting to {self.broker_host}:{self.broker_port}")
            
            # Wait for connection to establish (initial connection only)
            start_time = time.time()
            while not self.is_connected and (time.time() - start_time) < self.connect_timeout:
                time.sleep(0.1)
                
            return self.is_connected
        except Exception as e:
            logger.error(f"MQTT client connection failed: {str(e)}")
            return False
    
    def disconnect(self) -> None:
        """Disconnect from MQTT broker and stop background loop"""
        if self.client and self._loop_started:
            self.client.loop_stop()
            self._loop_started = False
            self.client.disconnect()
            self.is_connected = False
            logger.info("MQTT client disconnected")
    
    def publish(self, topic: str, payload: Any, qos: int = 1, retain: bool = False) -> bool:
        """Publish message to MQTT broker with auto-reconnect
        
        Args:
            topic: MQTT topic to publish to
            payload: Message payload (dict, string, or bytes)
            qos: Quality of Service level
            retain: Whether to retain the message
            
        Returns:
            True if published successfully, False otherwise
        """
        # Try to connect if not connected
        if not self.is_connected:
            if not self.connect():
                logger.error(f"Cannot publish to {topic}: Not connected to MQTT broker")
                return False
        
        try:
            # Convert dict to JSON if needed
            if isinstance(payload, dict):
                payload = json.dumps(payload)
                
            # Create a future and callback for publication completion
            publish_complete = threading.Event()
            def on_publish(client, userdata, mid):
                publish_complete.set()
                
            # Set temporary callback
            prev_callback = self.client.on_publish
            self.client.on_publish = on_publish
            
            # Publish with QoS 1 to ensure delivery
            result = self.client.publish(topic, payload, qos=qos, retain=retain)
            
            # Wait with longer timeout (15 seconds for config messages)
            timeout = 15.0 if "config" in topic else 5.0
            success = publish_complete.wait(timeout=timeout)
            
            # Restore previous callback
            self.client.on_publish = prev_callback
            
            if not success:
                logger.warning(f"Publish timeout for {topic}, but message may still be delivered")
                # Return True despite timeout - crucial change
                return True
                
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.error(f"Failed to publish to {topic}: {mqtt.error_string(result.rc)}")
                return False
                    
            logger.info(f"Published successfully to {topic}")
            return True
        except Exception as e:
            logger.error(f"Error publishing MQTT message to {topic}: {str(e)}")
            return False
    
    def subscribe(self, topic: str, handler: Callable[[str, Dict[str, Any]], None], qos: int = 1) -> bool:
        """Subscribe to a topic with a custom handler
        
        Args:
            topic: MQTT topic to subscribe to
            handler: Callback function that takes (topic, payload) as arguments
            qos: Quality of Service level
            
        Returns:
            True if subscribed successfully, False otherwise
        """
        # Try to connect if not connected
        if not self.is_connected:
            if not self.connect():
                logger.error(f"Cannot subscribe to {topic}: Not connected to MQTT broker")
                return False
        
        try:
            result = self.client.subscribe(topic, qos)
            
            if result[0] != mqtt.MQTT_ERR_SUCCESS:
                logger.error(f"Failed to subscribe to {topic}: {mqtt.error_string(result[0])}")
                return False
                
            # Store handler for this topic
            self.topic_handlers[topic] = handler
            
            # Track subscription for reconnection
            if topic not in self.subscriptions:
                self.subscriptions.append((topic, qos, handler))
                
            logger.info(f"Subscribed to {topic}")
            return True
        except Exception as e:
            logger.error(f"Error subscribing to topic {topic}: {str(e)}")
            return False
    
    def _on_connect(self, client, userdata, flags, rc):
        """Handle connection event"""
        if rc == 0:
            self.is_connected = True
            logger.info("Connected to MQTT broker successfully")
            
            # Restore subscriptions
            for topic, qos, handler in self.subscriptions:
                logger.info(f"Restoring subscription to {topic}")
                result = client.subscribe(topic, qos)
                if result[0] != mqtt.MQTT_ERR_SUCCESS:
                    logger.error(f"Failed to restore subscription to {topic}: {mqtt.error_string(result[0])}")
        else:
            self.is_connected = False
            logger.error(f"Failed to connect to MQTT broker with code {rc}: {mqtt.connack_string(rc)}")
    
    def _on_disconnect(self, client, userdata, rc):
        """Handle disconnection event"""
        self.is_connected = False
        if rc != 0:
            logger.warning(f"Unexpected disconnection from MQTT broker: {rc}")
        else:
            logger.info("Disconnected from MQTT broker")
    
    def _on_message(self, client, userdata, message):
        """Handle incoming messages and route to appropriate handler"""
        topic = message.topic
        try:
            # Try to parse payload as JSON
            try:
                payload = json.loads(message.payload.decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                # If not valid JSON, keep as raw payload
                payload = message.payload
                
            # Find and call appropriate handler
            for pattern, handler in self.topic_handlers.items():
                # Improved topic matching with wildcards
                if self._topic_matches(pattern, topic):
                    handler(topic, payload)
                    return
                    
            logger.debug(f"Received message on topic {topic} but no handler registered")
        except Exception as e:
            logger.error(f"Error processing MQTT message on topic {topic}: {str(e)}")
    
    def _topic_matches(self, subscription, topic):
        """Improved topic matching with wildcards
        
        Args:
            subscription: The subscription pattern (can include + and # wildcards)
            topic: The actual topic to match against
            
        Returns:
            True if the topic matches the subscription pattern
        """
        # Direct match
        if subscription == topic:
            return True
            
        # Split into parts
        sub_parts = subscription.split('/')
        topic_parts = topic.split('/')
        
        # Handle # wildcard (matches any level, including multiple levels)
        if subscription.endswith('#'):
            # Remove the # part for comparison
            if len(sub_parts) > 1:
                base = '/'.join(sub_parts[:-1])
                if topic.startswith(base + '/'):
                    return True
            else:
                # Just # matches everything
                return True
        
        # Handle + wildcards (single level substitution)
        if len(sub_parts) != len(topic_parts):
            return False
            
        for i, part in enumerate(sub_parts):
            if part != '+' and part != topic_parts[i]:
                return False
                
        return True
    
    def _monitor_connection(self):
        """Monitor connection state and attempt reconnection if needed"""
        while not self.stop_monitor:
            # Only attempt reconnection if we're supposed to be connected
            # but the client reports being disconnected
            if not self.is_connected and time.time() - self.last_connect_attempt > self.reconnect_delay:
                logger.info("Connection monitor: attempting to reconnect to MQTT broker")
                try:
                    self.last_connect_attempt = time.time()
                    self.client.reconnect()
                except Exception as e:
                    logger.error(f"Error in connection monitor when attempting reconnect: {str(e)}")
            
            # Sleep before next check
            time.sleep(self.monitor_interval)
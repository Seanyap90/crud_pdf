FROM golang:1.21-alpine

WORKDIR /app

# Install git (needed for Go to fetch dependencies)
RUN apk add --no-cache git ca-certificates

# Create certificates directory
RUN mkdir -p /app/certs

# Copy source code
COPY main.go ./

# Create a new go.mod file with the correct dependencies
RUN echo 'module gateway-simulator' > go.mod && \
    echo '' >> go.mod && \
    echo 'go 1.21' >> go.mod && \
    echo '' >> go.mod && \
    echo 'require github.com/eclipse/paho.mqtt.golang v1.4.3' >> go.mod

# Get all dependencies and create go.sum
RUN go mod download
RUN go mod tidy

# Set GO111MODULE to ensure modules are used
ENV GO111MODULE=on

# Build the application
RUN go build -o gateway-simulator

# Expose the port the service runs on
EXPOSE 6000

# Set default environment variables
ENV GATEWAY_ID=""
ENV MQTT_BROKER_ADDRESS="mqtt-broker:1883"
ENV API_URL="http://host.docker.internal:8000"
ENV GATEWAY_PORT="6000"
ENV WAIT_FOR_CERTIFICATES="true"

# Run the gateway simulator
CMD ["./gateway-simulator"]
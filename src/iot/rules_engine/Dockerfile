FROM golang:1.21 AS builder

WORKDIR /app

# First create a minimal go.mod
COPY go.mod .

# Fetch dependencies directly without checksum verification
RUN go env -w GOFLAGS="-mod=mod"
RUN go env -w GOSUMDB=off
RUN go mod tidy

# Manually get packages to create go.sum (but with verification disabled)
RUN go get github.com/eclipse/paho.mqtt.golang
RUN go get gopkg.in/yaml.v3

# Copy source code
COPY main.go .
COPY config.yaml .

# Build with verification fully disabled
RUN go env -w GOFLAGS="-mod=mod"
RUN CGO_ENABLED=0 GOOS=linux go build -o rules-engine .

# Use a small alpine image for the final container
FROM alpine:3.18

WORKDIR /app

# Install CA certificates for HTTPS requests
RUN apk --no-cache add ca-certificates

# Copy the binary from the builder stage
COPY --from=builder /app/rules-engine .

# Create a non-root user
RUN adduser -D -g '' appuser
RUN chown -R appuser:appuser /app
USER appuser

# Run the rules engine
ENTRYPOINT ["./rules-engine"]
CMD ["--config", "/app/config.yaml"]
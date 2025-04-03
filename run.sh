#!/bin/bash

set -e

#####################
# --- Constants --- #
#####################

THIS_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
MINIMUM_TEST_COVERAGE_PERCENT=0
FRONTEND_DIR="$THIS_DIR/../client" # Adjust if your frontend is in a different location
CERT_DIR="./certificates"
CERT_DAYS=365

##########################
# --- Task Functions --- #
##########################

# install core and development Python dependencies into the currently activated venv
function install {
    python -m pip install --upgrade pip
    python -m pip install --editable "$THIS_DIR/[dev]"
}

function run {
    AWS_PROFILE=cloud-course \
    S3_BUCKET_NAME="some-bucket" \
        uvicorn 'files_api.main:create_app' --reload
}

# start the FastAPI app, pointed at a mocked aws endpoint
function local-mock {
    set +e
    
    # Set queue type first
    export QUEUE_TYPE="local-mock"
    
    # Set model loading behavior
    export DISABLE_DUPLICATE_LOADING="true"
    export MODEL_MEMORY_LIMIT="24GiB"  # Adjust based on your available GPU memory
    
    # Start moto server
    python -m moto.server -p 5000 &
    MOTO_PID=$!
    
    # Configure AWS mock environment
    export AWS_ENDPOINT_URL="http://localhost:5000"
    export AWS_SECRET_ACCESS_KEY="mock"
    export AWS_ACCESS_KEY_ID="mock"
    export S3_BUCKET_NAME="some-bucket"
    
    # Create mock bucket
    aws s3 mb "s3://$S3_BUCKET_NAME"
    
    # Trap EXIT signal to kill all background processes
    trap 'kill $(jobs -p)' EXIT
    
    # Start worker in background with lazy model loading
    echo "Starting Local Worker (with lazy model loading)"
    python src/files_api/cli.py worker --mode local-mock --no-preload-models &
    
    # Wait a moment to ensure worker has started
    sleep 2
    
    # Start FastAPI app
    python -m uvicorn files_api.main:create_app --reload
    
    wait
}

# New function to install npm dependencies
function npm-install {
    if [ -d "$FRONTEND_DIR" ]; then
        echo "Installing npm dependencies in $FRONTEND_DIR"
        cd "$FRONTEND_DIR"
        npm install
    else
        echo "Error: Frontend directory not found at $FRONTEND_DIR"
        exit 1
    fi
}

# New function to build frontend
function npm-build {
    if [ -d "$FRONTEND_DIR" ]; then
        echo "Building frontend in $FRONTEND_DIR"
        cd "$FRONTEND_DIR"
        npm run build
    else
        echo "Error: Frontend directory not found at $FRONTEND_DIR"
        exit 1
    fi
}

# New function to run both frontend and backend with health check
function dev {
    set +e
    
    # Set queue type first
    export QUEUE_TYPE="local-mock"
    
    # Start moto server
    python -m moto.server -p 5000 &
    MOTO_PID=$!
    
    # Configure AWS mock environment
    export AWS_ENDPOINT_URL="http://localhost:5000"
    export AWS_SECRET_ACCESS_KEY="mock"
    export AWS_ACCESS_KEY_ID="mock"
    export S3_BUCKET_NAME="some-bucket"
    
    # Create mock bucket
    aws s3 mb "s3://$S3_BUCKET_NAME"
    
    # Trap EXIT signal to kill all background processes
    trap 'kill $(jobs -p)' EXIT

    echo "Preloading models..."
    python src/files_api/vlm/preload.py
    
    # Start worker in background - it will use QUEUE_TYPE from environment
    echo "Starting Local Worker"
    python src/files_api/cli.py worker --mode local-mock &
    
    # Start FastAPI app in background
    echo "Starting FastAPI app"
    python -m uvicorn files_api.main:create_app --reload --host 0.0.0.0 --port 8000 &
    
    # Wait for the API to be ready
    echo "Waiting for FastAPI to be ready..."
    max_retries=30
    counter=0
    while [ $counter -lt $max_retries ]; do
        if curl -s http://localhost:8000/health | grep -q "\"ready\":true"; then
            echo "FastAPI is ready!"
            break
        fi
        echo "Waiting for API... ($counter/$max_retries)"
        sleep 2
        counter=$((counter + 1))
    done
    
    if [ $counter -eq $max_retries ]; then
        echo "Warning: FastAPI didn't report ready status within timeout, but will continue anyway"
    fi
    
    # Start frontend
    if [ -d "$FRONTEND_DIR" ]; then
        echo "Starting frontend"
        cd "$FRONTEND_DIR"
        npm run dev
    else
        echo "Error: Frontend directory not found at $FRONTEND_DIR"
        # Keep running the backend even if frontend fails
        wait
    fi
}

function iot-backend-start() {
    echo "Starting Docker containers (MQTT broker and gateway simulator)..."
    cd src/iot && docker-compose build --no-cache gateway-simulator
    docker-compose up -d mqtt-broker
    
    echo "Waiting for MQTT broker to initialize..."
    sleep 3

    # Start rules-engine after MQTT broker is ready
    if grep -q "rules-engine:" docker-compose.yml; then
        echo "Starting Rules Engine..."
        docker-compose up -d rules-engine
        echo "Rules Engine started."
    fi
    
    echo "Starting FastAPI backend..."
    echo "Press CTRL+C to stop when done"
    # Using Python module approach while staying in src/iot directory
    cd .. && python -m iot.cli start --mode local --docker-network iot-network
}

function iot-backend-cleanup() {
    echo "Cleaning up IoT Gateway Management System..."
    
    echo "Stopping Docker containers..."
    cd src/iot && docker-compose down
    
    # Remove all gateway containers that might be running
    echo "Removing any remaining gateway containers..."
    docker ps -a | grep "gateway-" | awk '{print $1}' | xargs -r docker rm -f
    
    # Remove any rules engine containers that might be running
    echo "Removing any rules engine containers..."
    docker ps -a | grep -E "rules-engine|iot-rules" | awk '{print $1}' | xargs -r docker rm -f
    
    echo "Cleaning up Python cache files..."
    find src/iot -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find src/iot -type f -name "*.pyc" -delete 2>/dev/null || true
    
    # Kill any remaining Python processes related to the app (if any)
    echo "Killing any remaining Python processes..."
    pkill -f "iot.cli start" 2>/dev/null || true
    
    # Remove any temporary or log files
    echo "Removing temporary log files..."
    rm -f src/iot/*.log 2>/dev/null || true
    
    echo "Cleanup complete"
}

# Function to generate certificates for a gateway
function generate_cert() {
    local gateway_id=$1
    
    if [ -z "$gateway_id" ]; then
        echo -e "${RED}Error: Gateway ID is required for certificate generation${NC}"
        return 1
    fi
    
    # First check if gateway exists
    response=$(curl -s "http://localhost:8000/api/gateways/${gateway_id}")
    if [[ $response == *"detail"* ]] || [[ $response == *"not found"* ]]; then
        echo -e "${RED}Error: Gateway ${gateway_id} does not exist${NC}"
        echo "Please create the gateway first using the API"
        return 1
    fi
    
    echo -e "${GREEN}Generating certificates for gateway: ${gateway_id}${NC}"
    
    # Create certificates directory
    local gateway_cert_dir="${CERT_DIR}/${gateway_id}"
    mkdir -p "$gateway_cert_dir"
    
    # Generate certificate and private key using OpenSSL
    local cert_file="${gateway_cert_dir}/certificate.pem"
    local key_file="${gateway_cert_dir}/private_key.pem"
    
    echo -e "${YELLOW}Creating certificate and private key...${NC}"
    openssl req -x509 -newkey rsa:2048 -keyout "$key_file" \
        -out "$cert_file" -days "$CERT_DAYS" -nodes \
        -subj "/CN=gateway-${gateway_id}/O=IoT Gateway Management System" 2>/dev/null
    
    if [ $? -ne 0 ]; then
        echo -e "${RED}Failed to generate certificates${NC}"
        return 1
    fi
    
    # Set permissions
    chmod 644 "$cert_file"
    chmod 600 "$key_file"
    
    echo -e "${GREEN}Certificates generated successfully:${NC}"
    echo "Certificate: $cert_file"
    echo "Private Key: $key_file"
    
    echo -e "${YELLOW}Next steps:${NC}"
    echo "- Inject certificate into container: make inject-cert GATEWAY_ID=$gateway_id"
    
    return 0
}

# Function to inject certificates into a gateway container
function inject_cert() {
    local gateway_id=$1
    
    if [ -z "$gateway_id" ]; then
        echo -e "${RED}Error: Gateway ID is required${NC}"
        return 1
    fi
    
    # Check if container exists
    local container_name="gateway-$gateway_id"
    if ! docker ps -a --format '{{.Names}}' | grep -q "^$container_name$"; then
        echo -e "${RED}Error: Container $container_name does not exist${NC}"
        echo "Make sure the gateway has been created and a container is running"
        return 1
    fi
    
    # Check if certificates exist
    local cert_file="${CERT_DIR}/${gateway_id}/certificate.pem"
    local key_file="${CERT_DIR}/${gateway_id}/private_key.pem"
    
    if [ ! -f "$cert_file" ] || [ ! -f "$key_file" ]; then
        echo -e "${RED}Error: Certificates for gateway ${gateway_id} not found${NC}"
        echo "Please generate certificates first: make generate-cert GATEWAY_ID=$gateway_id"
        return 1
    fi
    
    echo -e "${GREEN}Injecting certificates into container: ${container_name}${NC}"
    
    # Get container running status
    local is_running=$(docker inspect --format='{{.State.Running}}' "$container_name" 2>/dev/null)
    if [ "$is_running" != "true" ]; then
        echo -e "${YELLOW}Warning: Container $container_name is not running${NC}"
        echo "Starting container..."
        docker start "$container_name"
        if [ $? -ne 0 ]; then
            echo -e "${RED}Failed to start container${NC}"
            return 1
        fi
        # Give it a moment to start
        sleep 2
    fi
    
    # Copy certificates to container
    echo -e "${YELLOW}Copying certificate to container...${NC}"
    docker cp "$cert_file" "$container_name:/app/certificates/cert.pem"
    if [ $? -ne 0 ]; then
        echo -e "${RED}Failed to copy certificate to container${NC}"
        return 1
    fi
    
    echo -e "${YELLOW}Copying private key to container...${NC}"
    docker cp "$key_file" "$container_name:/app/certificates/key.pem"
    if [ $? -ne 0 ]; then
        echo -e "${RED}Failed to copy private key to container${NC}"
        return 1
    fi
    
    echo -e "${GREEN}Certificates successfully injected into container${NC}"
    echo "The gateway should now detect the certificates and start normal operations"
    
    # Check container status
    echo -e "${YELLOW}Container status:${NC}"
    docker ps --filter "name=$container_name" --format "{{.Status}}"
    
    echo -e "${YELLOW}You can view container logs with:${NC}"
    echo "  docker logs -f $container_name"
    
    return 0
}

# run linting, formatting, and other static code quality tools
function lint {
    pre-commit run --all-files
}

# same as `lint` but with any special considerations for CI
function lint:ci {
    # We skip no-commit-to-branch since that blocks commits to `main`.
    # All merged PRs are commits to `main` so this must be disabled.
    SKIP=no-commit-to-branch pre-commit run --all-files
}

# execute tests that are not marked as `slow`
function test:quick {
    run-tests -m "not slow" ${@:-"$THIS_DIR/tests/"}
}

# execute tests against the installed package; assumes the wheel is already installed
function test:ci {
    INSTALLED_PKG_DIR="$(python -c 'import files_api; print(files_api.__path__[0])')"
    # in CI, we must calculate the coverage for the installed package, not the src/ folder
    COVERAGE_DIR="$INSTALLED_PKG_DIR" run-tests
}

# (example) ./run.sh test tests/test_states_info.py::test__slow_add
function run-tests {
    PYTEST_EXIT_STATUS=0
    rm -rf "$THIS_DIR/test-reports" || true
    python -m pytest ${@:-"$THIS_DIR/tests/"} \
        --cov "${COVERAGE_DIR:-$THIS_DIR/src}" \
        --cov-report html \
        --cov-report term \
        --cov-report xml \
        --junit-xml "$THIS_DIR/test-reports/report.xml" \
        --cov-fail-under "$MINIMUM_TEST_COVERAGE_PERCENT" || ((PYTEST_EXIT_STATUS+=$?))
    mv coverage.xml "$THIS_DIR/test-reports/" || true
    mv htmlcov "$THIS_DIR/test-reports/" || true
    mv .coverage "$THIS_DIR/test-reports/" || true
    return $PYTEST_EXIT_STATUS
}

function test:wheel-locally {
    deactivate || true
    rm -rf test-env || true
    python -m venv test-env
    source test-env/bin/activate
    clean || true
    pip install build
    build
    pip install ./dist/*.whl pytest pytest-cov
    test:ci
    deactivate || true
}

# serve the html test coverage report on localhost:8000
function serve-coverage-report {
    python -m http.server --directory "$THIS_DIR/test-reports/htmlcov/" 8000
}

# build a wheel and sdist from the Python source code
function build {
    python -m build --sdist --wheel "$THIS_DIR/"
}

function release:test {
    lint
    clean
    build
    publish:test
}

function release:prod {
    release:test
    publish:prod
}

function publish:test {
    try-load-dotenv || true
    twine upload dist/* \
        --repository testpypi \
        --username=__token__ \
        --password="$TEST_PYPI_TOKEN"
}

function publish:prod {
    try-load-dotenv || true
    twine upload dist/* \
        --repository pypi \
        --username=__token__ \
        --password="$PROD_PYPI_TOKEN"
}

# remove all files generated by tests, builds, or operating this codebase
function clean {
    rm -rf dist build coverage.xml test-reports
    find . \
      -type d \
      \( \
        -name "*cache*" \
        -o -name "*.dist-info" \
        -o -name "*.egg-info" \
        -o -name "*htmlcov" \
      \) \
      -not -path "*env/*" \
      -exec rm -r {} + || true

    find . \
      -type f \
      -name "*.pyc" \
      -not -path "*env/*" \
      -exec rm {} +
}

# export the contents of .env as environment variables
function try-load-dotenv {
    if [ ! -f "$THIS_DIR/.env" ]; then
        echo "no .env file found"
        return 1
    fi

    while read -r line; do
        export "$line"
    done < <(grep -v '^#' "$THIS_DIR/.env" | grep -v '^$')
}

# print all functions in this file
function help {
    echo "$0 <task> <args>"
    echo "Tasks:"
    compgen -A function | cat -n
}

TIMEFORMAT="Task completed in %3lR"
time ${@:-help}
#!/bin/bash

set -e

#####################
# --- Constants --- #
#####################

THIS_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
MINIMUM_TEST_COVERAGE_PERCENT=0
FRONTEND_DIR="$THIS_DIR/../client" # Adjust if your frontend is in a different location

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
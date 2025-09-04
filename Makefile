# Execute the "targets" in this file with `make <target>` e.g. `make help`.
#
# You can also run multiple in sequence, e.g. `make clean lint test serve-coverage-report`
#
# See run.sh for more in-depth comments on what each target does.

build:
	bash run.sh build

run:
	bash run.sh run

local-dev:
	bash run.sh local-dev

# Test AWS ECS deployment locally with mock environment
aws-mock:
	bash run.sh aws-mock

# Shutdown AWS mock
aws-mock-down:
	bash run.sh aws-mock-down

clean-model-cache:
	bash run.sh clean-model-cache

# Deploy to real AWS
aws-prod:
	bash run.sh aws-prod

# Cleanup AWS production deployment
aws-prod-cleanup:
	bash run.sh aws-prod-cleanup

# Show AWS production deployment status with costs and health checks
aws-prod-status:
	bash run.sh aws-prod-status

# Validate AWS production deployment prerequisites
aws-prod-validate:
	bash run.sh aws-prod-validate

# Internal targets for parallel execution
aws-prod-infra:
	@bash -c '. run.sh && load_env_file ".env.aws-prod" && python -m files_api.env_helper --validate-required-vars VPC_ID,PUBLIC_SUBNET_ID,EFS_FILE_SYSTEM_ID,EFS_ACCESS_POINT_ID,S3_BUCKET_NAME,SQS_QUEUE_URL,DATABASE_SG_ID,EFS_SG_ID,ECS_WORKERS_SG_ID && python -m deployment.aws.orchestration.deploy_ecs --mode aws-prod --hybrid-console --validate-only'

aws-prod-lambda:
	@bash -c '. run.sh && load_env_file ".env.aws-prod" && python -m deployment.aws.services.lambda_deploy --files-api-only'

aws-prod-ecs:
	@bash -c '. run.sh && load_env_file ".env.aws-prod" && python -m deployment.aws.orchestration.deploy_ecs --mode aws-prod --hybrid-console --deploy-services'

# Validate AWS mock deployment prerequisites
aws-mock-validate:
	bash run.sh aws-mock-validate

# Validate local development prerequisites
local-dev-validate:
	bash run.sh local-dev-validate

iot-backend-start:
	bash run.sh iot-backend-start

iot-backend-cleanup:
	bash run.sh iot-backend-cleanup

generate_cert:
	@if [ -z "$(GATEWAY_ID)" ]; then \
		echo "Error: GATEWAY_ID is required"; \
		exit 1; \
	fi
	@bash run.sh generate_cert $(GATEWAY_ID)

inject_cert:
	@if [ -z "$(GATEWAY_ID)" ]; then \
		echo "Error: GATEWAY_ID is required"; \
		exit 1; \
	fi
	@bash run.sh inject_cert $(GATEWAY_ID)

npm-install:
	bash run.sh npm-install

npm-build:
	bash run.sh npm-build

# Run both frontend and backend with a single command
dev:
	bash run.sh dev

clean:
	bash run.sh clean

help:
	bash run.sh help

install:
	bash run.sh install

lint:
	bash run.sh lint

lint-ci:
	bash run.sh lint:ci

publish-prod:
	bash run.sh publish:prod

publish-test:
	bash run.sh publish:test

release-prod:
	bash run.sh release:prod

release-test:
	bash run.sh release:test

serve-coverage-report:
	bash run.sh serve-coverage-report

test-ci:
	bash run.sh test:ci

test-quick:
	bash run.sh test:quick

test:
	bash run.sh run-tests

test-wheel-locally:
	bash run.sh test:wheel-locally
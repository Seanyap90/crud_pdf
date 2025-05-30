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

# Test AWS infrastructure deployment locally (default to EC2)
aws-mock:
	bash run.sh aws-mock ec2

# Test AWS EC2/ASG infrastructure deployment locally
aws-mock-ec2:
	bash run.sh aws-mock ec2

# Test AWS Elastic Beanstalk deployment locally  
aws-mock-eb:
	bash run.sh aws-mock eb

# Shutdown AWS mock (default to EC2)
aws-mock-down:
	bash run.sh aws-mock-down ec2

# Shutdown AWS mock EC2
aws-mock-ec2-down:
	bash run.sh aws-mock-down ec2

# Shutdown AWS mock EB
aws-mock-eb-down:
	bash run.sh aws-mock-down eb

# Deploy to real AWS
aws-prod:
	bash run.sh aws-prod

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
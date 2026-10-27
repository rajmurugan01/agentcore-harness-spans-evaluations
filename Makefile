# Region is pinned: a shell's AWS_REGION can silently override AWS_DEFAULT_REGION for CLI calls.
export AWS_REGION ?= us-east-1
export AWS_DEFAULT_REGION ?= us-east-1
export CDK_DEFAULT_ACCOUNT ?= $(shell aws sts get-caller-identity --query Account --output text)

install:
	cd cdk && npm install
	python3 -m pip install --quiet boto3

# The newer CDK CLI did not resolve an SSO profile for me, so pass it exported credentials.
deploy: install
	cd cdk && eval "$$(aws configure export-credentials --format env)" && npx cdk deploy --require-approval never --outputs-file cdk.outputs.json

# Read-only apart from invoking the harness: run with Transaction Search OFF, then again with it ON.
baseline:
	python3 demo/demo.py baseline

after:
	python3 demo/demo.py after

# Account-level changes. Read the README first.
enable-transaction-search:
	python3 demo/demo.py enable

revert-transaction-search:
	python3 demo/demo.py revert

# Whole A/B in one run (about 20 minutes) and puts the account setting back at the end.
demo:
	python3 demo/demo.py all | tee docs/transcript.txt

destroy:
	cd cdk && eval "$$(aws configure export-credentials --format env)" && npx cdk destroy --force

.PHONY: install deploy baseline after enable-transaction-search revert-transaction-search demo destroy

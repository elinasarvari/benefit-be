SHELL := /bin/bash
TEMPORAL_CLI_VERSION := 1.8.2
# The MongoDB CI downloads (fastdl.mongodb.org, cached; see .github/actions/setup-services). Locally, brew's.
MONGO_VERSION := 8.0.38
BENEFIT_FE_DIR ?= ../benefit-fe
MONGO_DIR ?= $(abspath .data/mongo)
# Ports default to the usual ones; `make verify` sets them to free ports for its own isolated stack.
MONGO_PORT ?= 27017
TEMPORAL_ADDRESS ?= localhost:7233
PYTEST_ARGS ?=
export BENEFIT_FE_DIR

.PHONY: doctor setup mongo-up mongo-down dev prod seed reset health e2e lint typecheck test test-unit test-workflow test-contract \
	test-integration test-api test-stack smoke-ai openapi openapi-check check verify verify-stack mutation-audit

doctor:
	@python3 scripts/doctor.py

setup:
	@python3 scripts/make_env.py
	brew bundle
	uv sync --frozen
	pnpm --dir $(BENEFIT_FE_DIR) install --frozen-lockfile
	uv run pre-commit install
	$(MAKE) mongo-up

mongo-up:
	@mkdir -p $(MONGO_DIR)/db
	@test -f $(MONGO_DIR)/keyfile || { openssl rand -base64 756 > $(MONGO_DIR)/keyfile; chmod 400 $(MONGO_DIR)/keyfile; }
	@if [ -f $(MONGO_DIR)/mongod.pid ] && kill -0 "$$(cat $(MONGO_DIR)/mongod.pid)" 2>/dev/null; then \
		echo "mongod already running"; \
	else \
		mongod --replSet rs0 --keyFile $(MONGO_DIR)/keyfile --bind_ip 127.0.0.1 --port $(MONGO_PORT) \
			--dbpath $(MONGO_DIR)/db --logpath $(MONGO_DIR)/mongod.log \
			--pidfilepath $(MONGO_DIR)/mongod.pid --fork > /dev/null; \
	fi
	uv run python -m db.mongo.provision

mongo-down:
	@mongod --shutdown --dbpath $(MONGO_DIR)/db > /dev/null 2>&1 || echo "mongod was not running"

dev: mongo-up
	uv run honcho -f Procfile start

prod: mongo-up
	pnpm --dir $(BENEFIT_FE_DIR) build
	APP_ENV=prod uv run honcho -f Procfile.prod start

seed:
	uv run python -m db.seeds.generate

# Safe to run while `make dev` is running: it rebuilds the data and stops in-flight workflows.
reset: mongo-up
	uv run python -m db.seeds.reset
	@temporal workflow terminate --address $(TEMPORAL_ADDRESS) --query 'ExecutionStatus="Running"' --yes > /dev/null 2>&1 \
		|| echo "note: no Temporal server to clear (it is not running)"
	@uv run python -m scripts.clear_traces

health:
	uv run python -m scripts.check_health

e2e:
	pnpm --dir $(BENEFIT_FE_DIR) e2e

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

test-unit:
	uv run pytest -m unit $(PYTEST_ARGS)

test-workflow:
	uv run pytest -m workflow $(PYTEST_ARGS)

test-contract:
	uv run pytest -m contract $(PYTEST_ARGS)

test-integration:
	uv run pytest -m integration $(PYTEST_ARGS)

# api and stack tests need the running stack (make dev, or Procfile.ci in CI)
test-api:
	uv run pytest -m api $(PYTEST_ARGS)

test-stack:
	uv run pytest -m stack $(PYTEST_ARGS)

# Calls the live provider chosen by LLM_PROVIDER: needs keys in .env, costs a few cents, never runs in CI
smoke-ai:
	LLM_MODE=live uv run pytest -m live_llm tests/packages/agents/llm/test_live_smoke.py $(PYTEST_ARGS)

test:
	uv run pytest $(PYTEST_ARGS)

openapi:
	uv run python -m scripts.export_openapi

openapi-check: openapi
	git diff --exit-code openapi/openapi.json

check: lint typecheck test-unit test-workflow test-contract openapi-check
	gitleaks detect --no-banner --config scripts/gitleaks.toml --log-opts=HEAD  # this branch's history only, not every fetched ref
	uv run pip-audit

# Every CI job's checks in both repos, one table. Starts its own MongoDB, Temporal and stack on free
# ports, so it runs beside `make dev`, and stops all of it at the end.
verify:
	uv run python -m scripts.verify

# Only the stages that need the stack (client in sync, integration, stack and api tests, e2e), on the
# same isolated stack: what the feature workflow's e2e job runs beside `make check`.
verify-stack:
	uv run python -m scripts.verify --stack-only

# Plants known bugs one at a time and checks the tests fail for each. Slow: runs real tests.
mutation-audit:
	uv run python -m scripts.mutation_audit

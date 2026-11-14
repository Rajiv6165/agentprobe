.PHONY: help up down test lint format migrate demo

PYTHON ?= python

help:
	@echo "Available commands:"
	@echo "  make up       - Build and start PostgreSQL and API services in Docker"
	@echo "  make down     - Stop and remove Docker containers and volumes"
	@echo "  make migrate  - Apply database migrations with Alembic"
	@echo "  make test     - Run pytest test suites for both SDK and API"
	@echo "  make lint     - Run ruff linter and strict mypy type-checker"
	@echo "  make format   - Format code across all packages using ruff"
	@echo "  make demo     - Run the instrumented customer support demo agent"

up:
	docker compose up -d --build

down:
	docker compose down -v

migrate:
	cd services/api && $(PYTHON) -m alembic upgrade head

test:
	$(PYTHON) -m pytest packages/sdk/tests --cov=agentprobe --cov-report=term-missing
	cd services/api && $(PYTHON) -m pytest tests --cov=app --cov-report=term-missing

lint:
	$(PYTHON) -m ruff check packages/sdk services/api examples
	$(PYTHON) -m ruff format --check packages/sdk services/api examples
	$(PYTHON) -m mypy packages/sdk/src/agentprobe

format:
	$(PYTHON) -m ruff format packages/sdk services/api examples
	$(PYTHON) -m ruff check --fix packages/sdk services/api examples

demo:
	$(PYTHON) examples/demo_agent/main.py


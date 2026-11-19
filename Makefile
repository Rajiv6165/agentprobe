.PHONY: help up down test lint format migrate demo replay-demo

PYTHON ?= python

help:
	@echo "Available commands (forwards to tasks.py):"
	@echo "  make up          - Start PostgreSQL and API services in Docker"
	@echo "  make down        - Stop Docker containers and services"
	@echo "  make migrate     - Apply database migrations with Alembic"
	@echo "  make test        - Run pytest test suites for both SDK and API"
	@echo "  make lint        - Run ruff linter and strict mypy type-checker"
	@echo "  make format      - Format code across all packages using ruff"
	@echo "  make demo        - Run the customer support demo agent"
	@echo "  make replay-demo - Run the cassette replay regression demo"

up:
	$(PYTHON) tasks.py up

down:
	$(PYTHON) tasks.py down

migrate:
	$(PYTHON) tasks.py migrate

test:
	$(PYTHON) tasks.py test

lint:
	$(PYTHON) tasks.py lint

format:
	$(PYTHON) -m ruff format packages/sdk services/api examples
	$(PYTHON) -m ruff check --fix packages/sdk services/api examples

demo:
	$(PYTHON) tasks.py demo

replay-demo:
	$(PYTHON) tasks.py replay-demo

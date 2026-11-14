# Contributing to AgentProbe

Thank you for your interest in contributing to AgentProbe!

## Monorepo Layout

- `packages/sdk`: The core Python instrumentation SDK (`agentprobe`).
- `services/api`: The FastAPI backend service and PostgreSQL database migrations.
- `examples/demo_agent`: The reference customer support agent demonstrating end-to-end tracing.

## Development Workflow

1. **Virtual Environment**: Create a virtual environment using Python 3.10+ and install packages in editable mode:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # or .venv\Scripts\activate on Windows
   pip install -e "packages/sdk[dev]"
   pip install -e "services/api[dev]"
   ```

2. **Database Migrations**:
   ```bash
   make migrate
   ```

3. **Running Tests**:
   ```bash
   make test
   ```
   Both the SDK and API test suites maintain 85%+ coverage.

4. **Linting and Typing**:
   ```bash
   make lint
   ```
   Code formatting is enforced via `ruff` and strict type annotations via `mypy`.

## Pull Request Guidelines

- Ensure all new features or bug fixes include corresponding unit and integration tests.
- Verify `make lint` and `make test` pass cleanly before submitting.


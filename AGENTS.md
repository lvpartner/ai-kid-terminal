# Repository Guidelines

## Project Structure & Module Organization

Server code lives in `src/kid_terminal/`. Keep HTTP/WebSocket orchestration in `app.py`, request models in `schemas.py`, persistence in `models.py` and `db.py`, and provider integrations behind `RealtimeAIProvider` in `providers.py`. The Android client lives in `android/app/`; keep audio, protocol, security, and kiosk concerns separate. Tests mirror behavior under `tests/` and `android/app/src/test/`. Alembic revisions belong in `migrations/versions/`; operational utilities belong in `scripts/`; protocol, security, and deployment decisions belong in `docs/`.

## Build, Test, and Development Commands

Use Python 3.12 and the checked-in Make targets:

- `make setup` creates `.venv`, installs pinned dependencies, and generates `.env` with mode `0600`.
- `make migrate` applies Alembic migrations to the configured database.
- `make run` starts the API on `127.0.0.1:8000`.
- `make lint` runs Ruff formatting/lint and mypy.
- `make test` runs all unit and integration tests.
- `make demo` exercises enrollment, WebSocket audio, interruption, telemetry, config, and releases.
- `make up` builds and starts PostgreSQL plus the API with Docker Compose.

Never commit generated data or reveal values from `.env`.

## Coding Style & Naming Conventions

Ruff defines formatting and lint rules with a 100-column limit; mypy checks all source modules. Use four spaces, type annotations for public functions, `snake_case` for functions/modules, `PascalCase` for classes, and uppercase constants. Keep async I/O non-blocking and bound retries, message sizes, and retention. Add comments only for non-obvious constraints.

## Testing Guidelines

Pytest and `pytest-asyncio` are used. Name files `test_<area>.py` and tests `test_<behavior>`. Every authentication, protocol, migration, privacy, rollout, or persistence change requires a regression test. Mock external AI by default; real Qwen checks must be opt-in and must never print credentials. Run `make lint && make test` before review.

## Commit & Pull Request Guidelines

Use concise imperative Conventional Commit subjects, such as `feat: add device token rotation` or `fix: close websocket database sessions`. Keep commits focused. Pull requests must explain the problem and security impact, list exact verification commands, link issues, and include protocol examples or terminal output for behavioral changes. Call out migrations, new environment variables, dependency changes, and rollback steps.

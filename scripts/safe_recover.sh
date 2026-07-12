#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  restart-api) docker compose restart api ;;
  migrate) docker compose run --rm api alembic upgrade head ;;
  health) python3 scripts/healthcheck.py ;;
  *) echo "Allowed actions: restart-api, migrate, health" >&2; exit 2 ;;
esac


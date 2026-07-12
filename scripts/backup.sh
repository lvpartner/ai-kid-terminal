#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
docker_cmd=()
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  docker_cmd=(docker)
elif sudo -n docker info >/dev/null 2>&1; then
  docker_cmd=(sudo docker)
fi
if [[ ${#docker_cmd[@]} -gt 0 ]] && "${docker_cmd[@]}" compose ps --status running db 2>/dev/null | grep -q db; then
  "${docker_cmd[@]}" compose exec -T db pg_dump -U kid_terminal -Fc kid_terminal > "backups/db-${stamp}.dump"
else
  [[ -f data/kid-terminal.db ]] || { echo "Database not found" >&2; exit 1; }
  .venv/bin/python scripts/sqlite_backup.py data/kid-terminal.db "backups/db-${stamp}.sqlite"
fi
sha256sum backups/db-${stamp}.* > "backups/db-${stamp}.sha256"
echo "Backup written and checksummed under backups/."

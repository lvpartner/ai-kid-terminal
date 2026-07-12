#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
backup="${1:?usage: scripts/restore.sh BACKUP}"
[[ -f "$backup" ]] || { echo "Backup not found" >&2; exit 1; }
if [[ "$backup" == *.dump ]]; then
  docker compose exec -T db pg_restore -U kid_terminal --clean --if-exists -d kid_terminal < "$backup"
else
  [[ "$backup" == backups/*.sqlite ]] || { echo "Only backups/*.sqlite is accepted" >&2; exit 1; }
  cp data/kid-terminal.db "backups/pre-restore-$(date -u +%s).sqlite" 2>/dev/null || true
  cp "$backup" data/kid-terminal.db
fi
echo "Restore complete; run make status and make test."


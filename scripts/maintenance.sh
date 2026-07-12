#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source .env

base_url="${MAINTENANCE_BASE_URL:-http://127.0.0.1:8000}"
minimum_free_kb="${MINIMUM_DISK_FREE_KB:-10485760}"
backup_keep="${BACKUP_KEEP_COUNT:-14}"

free_kb="$(df -Pk . | awk 'NR==2 {print $4}')"
if (( free_kb < minimum_free_kb )); then
  printf '{"level":"error","event":"disk_low","free_kb":%s,"required_kb":%s}\n' \
    "$free_kb" "$minimum_free_kb" >&2
  exit 1
fi

curl -fsS "${base_url}/health/ready" >/dev/null
curl -fsS -X POST -H "X-Admin-Key: ${ADMIN_API_KEY}" \
  "${base_url}/v1/admin/cleanup" >/dev/null
bash scripts/backup.sh

latest="$(find backups -maxdepth 1 -type f \( -name '*.dump' -o -name '*.sqlite' \) \
  -printf '%T@ %p\n' | sort -nr | awk 'NR==1 {print $2}')"
if [[ "$latest" == *.dump ]]; then
  if docker info >/dev/null 2>&1; then
    docker compose exec -T db pg_restore --list < "$latest" >/dev/null
  else
    sudo -n docker compose exec -T db pg_restore --list < "$latest" >/dev/null
  fi
else
  .venv/bin/python - "$latest" <<'PY'
import sqlite3
import sys

with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as database:
    assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
PY
fi

mapfile -t old_backups < <(
  find backups -maxdepth 1 -type f \( -name '*.dump' -o -name '*.sqlite' \) \
    -printf '%T@ %p\n' | sort -nr | awk -v keep="$backup_keep" 'NR > keep {print $2}'
)
for backup in "${old_backups[@]}"; do
  rm -f -- "$backup" "${backup%.*}.sha256"
done

printf '{"level":"info","event":"maintenance_complete","free_kb":%s,"backup":"%s"}\n' \
  "$free_kb" "$(basename "$latest")"


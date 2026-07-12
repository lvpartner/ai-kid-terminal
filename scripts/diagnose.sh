#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source .env
echo "disk_free_kb=$(df -Pk . | awk 'NR==2 {print $4}')"
curl -fsS http://127.0.0.1:8000/health/ready
printf '\n'
curl -fsS -H "X-Admin-Key: ${ADMIN_API_KEY}" http://127.0.0.1:8000/v1/admin/diagnose
printf '\n'
if [[ -n "${TLS_HOST:-}" ]]; then
  echo | openssl s_client -connect "${TLS_HOST}:443" -servername "$TLS_HOST" 2>/dev/null \
    | openssl x509 -noout -enddate
else
  echo "tls=not_configured (localhost deployment)"
fi


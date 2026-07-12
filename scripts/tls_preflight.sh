#!/usr/bin/env bash
set -euo pipefail

domain="${TLS_HOST:?set TLS_HOST to your public hostname}"
resolved_ip="$(getent ahostsv4 "$domain" | awk 'NR == 1 {print $1}')"

if [[ -z "$resolved_ip" ]]; then
  echo "TLS preflight failed: $domain has no IPv4 DNS record" >&2
  exit 1
fi

curl -fsS --max-time 5 http://127.0.0.1:8000/health/ready >/dev/null
echo "TLS preflight passed: $domain -> $resolved_ip; local API ready"

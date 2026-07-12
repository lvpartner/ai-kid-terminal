#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
  admin_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  pepper="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  postgres_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  sed -e "s|ADMIN_API_KEY=.*|ADMIN_API_KEY=${admin_key}|" \
      -e "s|TOKEN_PEPPER=.*|TOKEN_PEPPER=${pepper}|" \
      .env.example > .env
  printf 'POSTGRES_PASSWORD=%s\n' "$postgres_password" >> .env
  chmod 600 .env
fi
python3 -m venv .venv
.venv/bin/pip install --disable-pip-version-check -e '.[dev]'
mkdir -p data releases backups
chmod 700 data releases backups
echo "Setup complete. Secrets are stored only in .env (mode 600)."


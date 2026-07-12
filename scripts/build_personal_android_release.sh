#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
version_code="${1:?usage: build_personal_android_release.sh VERSION_CODE VERSION_NAME}"
version_name="${2:?usage: build_personal_android_release.sh VERSION_CODE VERSION_NAME}"
server_url="${PERSONAL_API_BASE_URL:?PERSONAL_API_BASE_URL is required}"

if [[ "$server_url" != https://* ]]; then
  echo "personal API base URL must use HTTPS" >&2
  exit 1
fi

API_BASE_URL="$server_url" bash scripts/build_android_release.sh "$version_code" "$version_name"
apk="dist/ai-kid-terminal-${version_name}.apk"
if ! unzip -p "$apk" classes.dex | strings | awk -v expected="$server_url" '
  $0 == expected { found = 1 }
  END { exit(found ? 0 : 1) }
'; then
  echo "personal APK does not contain the configured server URL" >&2
  exit 1
fi
echo "personal server URL verified in signed APK"

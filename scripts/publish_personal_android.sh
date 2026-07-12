#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
version_name="${1:?usage: publish_personal_android.sh VERSION_NAME}"
source_apk="dist/ai-kid-terminal-${version_name}.apk"
destination_dir="downloads/install"
destination_apk="$destination_dir/ai-kid-terminal.apk"
temporary_apk="$destination_dir/.ai-kid-terminal.apk.tmp"

if [[ ! -s "$source_apk" ]]; then
  echo "signed APK not found: $source_apk" >&2
  exit 1
fi

apksigner="${ANDROID_HOME:-/opt/android-sdk}/build-tools/36.0.0/apksigner"
"$apksigner" verify "$source_apk" >/dev/null
mkdir -p "$destination_dir"
trap 'rm -f "$temporary_apk"' EXIT
install -m 0644 "$source_apk" "$temporary_apk"
mv -f "$temporary_apk" "$destination_apk"
echo "personal APK published: $destination_apk"

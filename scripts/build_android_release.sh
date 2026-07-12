#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
version_code="${1:?usage: build_android_release.sh VERSION_CODE VERSION_NAME [BOOTSTRAP_TOKEN_FILE]}"
version_name="${2:?usage: build_android_release.sh VERSION_CODE VERSION_NAME [BOOTSTRAP_TOKEN_FILE]}"
bootstrap_file="${3:-}"
secret_dir="${ANDROID_RELEASE_SECRET_DIR:-$HOME/.config/ai-kid-terminal}"

source "$secret_dir/signing.env"
export ANDROID_SIGNING_STORE_FILE ANDROID_SIGNING_STORE_PASSWORD
export ANDROID_SIGNING_KEY_ALIAS ANDROID_SIGNING_KEY_PASSWORD
export ORG_GRADLE_PROJECT_API_BASE_URL="https://api.invalid"
export ORG_GRADLE_PROJECT_LEGACY_API_BASE_URL="${API_BASE_URL:-}"
export ORG_GRADLE_PROJECT_EXPECTED_SIGNER_SHA256="$(cat "$secret_dir/signer.sha256")"
export ORG_GRADLE_PROJECT_APP_VERSION_CODE="$version_code"
export ORG_GRADLE_PROJECT_APP_VERSION_NAME="$version_name"
export ORG_GRADLE_PROJECT_BOOTSTRAP_ENROLLMENT_TOKEN=""
if [[ -n "$bootstrap_file" ]]; then
  export ORG_GRADLE_PROJECT_BOOTSTRAP_ENROLLMENT_TOKEN="$(cat "$bootstrap_file")"
fi

(
  cd android
  ANDROID_HOME="${ANDROID_HOME:-/opt/android-sdk}" \
    ./gradlew --no-daemon :app:clean :app:assembleRelease
)

mkdir -p dist
destination="dist/ai-kid-terminal-${version_name}.apk"
cp android/app/build/outputs/apk/release/app-release.apk "$destination"
chmod 600 "$destination"

apksigner="${ANDROID_HOME:-/opt/android-sdk}/build-tools/36.0.0/apksigner"
"$apksigner" verify --verbose "$destination" >/dev/null
actual_digest="$("$apksigner" verify --print-certs "$destination" \
  | sed -n 's/Signer #1 certificate SHA-256 digest: //p')"
expected_digest="$(cat "$secret_dir/signer.sha256")"
if [[ "${actual_digest,,}" != "${expected_digest,,}" ]]; then
  echo "release signer mismatch" >&2
  exit 1
fi

sha256sum "$destination" >"${destination}.sha256"
chmod 600 "${destination}.sha256"
echo "release build verified: $destination"

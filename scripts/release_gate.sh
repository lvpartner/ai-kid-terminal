#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
make lint
make test
make android-test

if [[ "${REAL_QWEN_GATE:-0}" == "1" ]]; then
  make qwen-voice-e2e
fi

git diff --check
echo "release gate passed"

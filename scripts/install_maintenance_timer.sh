#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
root="$(pwd)"
sed "s|@@PROJECT_ROOT@@|${root}|g" deploy/systemd/ai-kid-terminal-maintenance.service \
  | sudo tee /etc/systemd/system/ai-kid-terminal-maintenance.service >/dev/null
sudo install -m 0644 deploy/systemd/ai-kid-terminal-maintenance.timer \
  /etc/systemd/system/ai-kid-terminal-maintenance.timer
sudo systemctl daemon-reload
sudo systemctl enable --now ai-kid-terminal-maintenance.timer
sudo systemctl list-timers ai-kid-terminal-maintenance.timer --no-pager


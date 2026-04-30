#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME=bond-host-daemon
REPO_DIR=${REPO_DIR:-/srv/environments/dev/bond}
SERVICE_SRC="$REPO_DIR/deploy/systemd/${SERVICE_NAME}.service"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ ! -f "$SERVICE_SRC" ]]; then
  echo "Service file not found: $SERVICE_SRC" >&2
  exit 1
fi

sudo cp "$SERVICE_SRC" "$SERVICE_DST"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager

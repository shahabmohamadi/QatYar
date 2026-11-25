#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export STORE_DATA_DIR="$HOME/.local/share/qatyar-codespace"
export STORE_HOST=0.0.0.0
export STORE_PORT=8080
mkdir -p "$STORE_DATA_DIR"
python .devcontainer/init_demo.py
if curl --fail --silent --max-time 2 http://127.0.0.1:8080/health >/dev/null; then
  exit 0
fi
nohup python run.py serve > "$STORE_DATA_DIR/server.log" 2>&1 < /dev/null &

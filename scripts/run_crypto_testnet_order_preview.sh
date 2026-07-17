#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
PREVIEW_LOG="$LOG_DIR/crypto_testnet_order_preview.out.log"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

{
  echo "=== Crypto testnet order preview started at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo "Project: $PROJECT_DIR"
  echo "Report: reports/crypto_phase10_testnet_order_preview.json"
  echo "Requests: reports/crypto_phase10_testnet_order_requests.csv"
  echo
  "$PROJECT_DIR/.venv/bin/python" -m research.crypto_testnet_order_preview "$@"
  echo "=== Crypto testnet order preview finished at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo
} >> "$PREVIEW_LOG" 2>&1

echo "Crypto testnet order preview completed. Log: $PREVIEW_LOG"

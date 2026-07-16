#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
DRY_RUN_LOG="$LOG_DIR/crypto_execution_dry_run.out.log"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

{
  echo "=== Crypto execution dry run started at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo "Project: $PROJECT_DIR"
  echo "Report: reports/crypto_phase8_execution_dry_run.json"
  echo "Tickets: reports/crypto_phase8_order_intents.csv"
  echo
  "$PROJECT_DIR/.venv/bin/python" -m research.crypto_execution_dry_run --sent-only "$@"
  echo "=== Crypto execution dry run finished at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo
} >> "$DRY_RUN_LOG" 2>&1

echo "Crypto execution dry run completed. Log: $DRY_RUN_LOG"

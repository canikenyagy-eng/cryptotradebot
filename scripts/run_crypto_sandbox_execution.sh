#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
SANDBOX_LOG="$LOG_DIR/crypto_sandbox_execution.out.log"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

{
  echo "=== Crypto sandbox execution started at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo "Project: $PROJECT_DIR"
  echo "Report: reports/crypto_phase9_sandbox_execution_report.json"
  echo "Ledger: reports/crypto_phase9_sandbox_order_ledger.csv"
  echo
  "$PROJECT_DIR/.venv/bin/python" -m research.crypto_sandbox_execution "$@"
  echo "=== Crypto sandbox execution finished at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo
} >> "$SANDBOX_LOG" 2>&1

echo "Crypto sandbox execution completed. Log: $SANDBOX_LOG"

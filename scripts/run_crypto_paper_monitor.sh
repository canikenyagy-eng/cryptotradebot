#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
MONITOR_LOG="$LOG_DIR/crypto_paper_monitor.out.log"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

{
  echo "=== Crypto paper monitor started at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo "Project: $PROJECT_DIR"
  echo "Monitor: reports/crypto_phase7_paper_monitor.json"
  echo "Paper: reports/crypto_paper_trading_report.json"
  echo
  "$PROJECT_DIR/.venv/bin/python" -m research.crypto_paper_monitor --sent-only "$@"
  echo "=== Crypto paper monitor finished at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo
} >> "$MONITOR_LOG" 2>&1

echo "Crypto paper monitor completed. Log: $MONITOR_LOG"

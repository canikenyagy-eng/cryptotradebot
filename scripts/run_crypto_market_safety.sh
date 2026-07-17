#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
SAFETY_LOG="$LOG_DIR/crypto_market_safety.out.log"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

{
  echo "=== Crypto realtime market safety started at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo "Project: $PROJECT_DIR"
  echo "Report: reports/crypto_phase11_market_safety_report.json"
  echo "Checks: reports/crypto_phase11_market_safety_checks.csv"
  echo
  "$PROJECT_DIR/.venv/bin/python" -m research.crypto_market_safety "$@"
  echo "=== Crypto realtime market safety finished at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo
} >> "$SAFETY_LOG" 2>&1

echo "Crypto realtime market safety completed. Log: $SAFETY_LOG"

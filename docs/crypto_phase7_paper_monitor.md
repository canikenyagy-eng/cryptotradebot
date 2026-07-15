# Crypto Phase 7 Paper Monitor

Phase 7 turns Phase 6 paper trading into a repeatable live-monitor workflow. It refreshes theoretical outcomes, rebuilds the paper account, and writes execution-readiness guardrails.

This is still not live exchange execution. It does not use exchange API keys, account balances, order routing, or order placement.

## Main Command

```bash
python -m research.crypto_paper_monitor --sent-only
```

The command:

- refreshes Phase 5 outcomes unless `--skip-outcome-update` is used
- rebuilds the Phase 6 paper account JSON, CSV ledger, and HTML dashboard
- checks heartbeat/feed health
- writes a Phase 7 readiness JSON report and HTML dashboard
- keeps `live_execution_allowed=false` in every Phase 7 decision

Outputs:

- `reports/crypto_phase7_paper_monitor.json`
- `reports/crypto_phase7_paper_monitor_dashboard.html`
- `reports/crypto_paper_trading_report.json`
- `reports/crypto_paper_trading_ledger.csv`
- `reports/crypto_paper_trading_dashboard.html`

## Runner Script

Use the script form for cron, launchd, or systemd timers:

```bash
scripts/run_crypto_paper_monitor.sh
```

Pass CLI options through the script:

```bash
scripts/run_crypto_paper_monitor.sh --skip-outcome-update --no-require-health-ok
```

## Readiness Guardrails

Default guardrails:

- `MARKET_TYPE=crypto_spot`
- `DATA_SOURCE=ccxt`
- required symbols include `BTCUSDT` and `ETHUSDT`
- `ENABLE_LIVE_MODE=0`
- heartbeat and feed health are OK
- at least `30` executed paper trades
- at least `5` executed paper trades per required symbol
- paper AvgR is at least `0.10`
- paper profit factor is at least `1.20`
- paper max drawdown is at most `10%`
- paper ROI is at least `0%`

Passing all guardrails only means `READY_FOR_EXECUTION_DESIGN_REVIEW`. It does not permit live execution.

## Local Diagnostic Runs

Skip outcome refresh and ignore missing local heartbeat/feed health:

```bash
python -m research.crypto_paper_monitor \
  --skip-outcome-update \
  --include-unsent \
  --no-require-health-ok
```

Fail a shell job when guardrails are not ready:

```bash
python -m research.crypto_paper_monitor --sent-only --fail-on-not-ready
```

Tighten paper thresholds:

```bash
python -m research.crypto_paper_monitor \
  --sent-only \
  --min-paper-trades 60 \
  --min-symbol-trades 15 \
  --min-profit-factor 1.40 \
  --max-drawdown-pct 6
```

## Environment

Use `docs/phase7_crypto_paper_monitor.env.example` as the explicit profile for Phase 7 paths and thresholds.

## Phase 7 Exit Criteria

Before considering an execution-design phase:

- Phase 4 signal loop is stable
- Phase 5 outcomes are updating automatically
- Phase 6 paper account updates after each monitor run
- Phase 7 guardrails pass for BTCUSDT and ETHUSDT
- readiness report still says `live_execution_allowed=false`
- manual review confirms fees, funding, order-book slippage, exchange errors, and kill-switch requirements for the next phase

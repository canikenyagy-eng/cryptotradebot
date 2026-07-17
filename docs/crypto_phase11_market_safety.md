# Crypto Phase 11 Real-Time Market Safety

Phase 11 adds execution-grade market-data checks before any future order can move past preview/sandbox. It consumes Phase 10 request previews, checks public realtime market data, and writes a readiness report.

It does not load exchange API keys, fetch balances, create orders, or submit testnet/live orders.

## Main Command

```bash
python -m research.crypto_market_safety
```

Outputs:

- `reports/crypto_phase11_market_safety_report.json`
- `reports/crypto_phase11_market_safety_checks.csv`

## What It Checks

- Phase 10 request-preview source is clean
- live ticker is available
- ticker timestamp is fresh enough
- top-of-book order book snapshot is available, when enabled
- bid/ask spread is below the configured maximum
- exchange server time is close to local UTC time
- request entry price is close enough to current bid/ask/last
- recent Phase 4 trigger-timeframe market diagnostics are clean
- stale cache fallback blocks execution readiness

## Default Gates

```text
max ticker age: 30 seconds
max spread: 10 bps
max exchange time drift: 2000 ms
max entry/current price deviation: 100 bps
diagnostics timeframe: M5
max diagnostics age: 900 seconds
```

## Diagnostic Runs

Skip order book snapshots:

```bash
python -m research.crypto_market_safety --disable-order-book-check
```

Run checks even if Phase 10 is not preview-ready:

```bash
python -m research.crypto_market_safety --allow-phase10-not-ready
```

Do not block on missing recent market diagnostics:

```bash
python -m research.crypto_market_safety --allow-missing-diagnostics
```

## Runner Script

```bash
scripts/run_crypto_market_safety.sh
```

Pass options through:

```bash
scripts/run_crypto_market_safety.sh --disable-order-book-check
```

## Environment

Use `docs/phase11_crypto_market_safety.env.example` as the explicit profile for Phase 11 paths and safety thresholds.

## Phase 11 Exit Criteria

Before any future testnet submission phase:

- Phase 10 request previews exist for real delivered forward signals
- ticker checks pass for BTCUSDT and ETHUSDT
- order book spread checks pass, or the decision to disable them is documented
- exchange time drift stays within threshold
- entry/current price deviation stays within threshold
- stale cache or stale trigger diagnostics block readiness
- every report keeps `order_submission_allowed=false`
- every report keeps `live_execution_allowed=false`

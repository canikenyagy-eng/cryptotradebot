# Crypto Phase 12 Realtime Feed + Final Pre-Order Guard

Phase 12 adds a live market snapshot layer before any future testnet order submission. It is still paper/testnet readiness only: it does not load exchange trading keys, create orders, submit testnet orders, or send live orders.

## What It Adds

- Binance spot websocket snapshots for BTCUSDT and ETHUSDT
- `bookTicker` best bid/ask updates
- `miniTicker` last-price updates
- local snapshot store with freshness timestamps
- REST ticker fallback when websocket data is stale or missing
- optional REST order-book refresh before approval
- exchange server time drift check
- final spread and entry/current price deviation gates
- stale-cache hard block through Phase 4 market diagnostics
- JSON and CSV readiness reports

Binance spot `bookTicker` is used for bid/ask because it pushes best bid and ask updates in real time. `miniTicker` is paired with it for last price because mini ticker carries close/last-style price fields but not bid/ask.

## Main Command

Run with existing snapshots and REST fallback:

```bash
python -m research.crypto_realtime_guard
```

Collect fresh websocket snapshots first, then run the guard:

```bash
python -m research.crypto_realtime_guard --collect-websocket-seconds 15
```

Runner script:

```bash
scripts/run_crypto_realtime_guard.sh --collect-websocket-seconds 15
```

Outputs:

- `reports/crypto_phase12_realtime_guard_report.json`
- `reports/crypto_phase12_realtime_guard_checks.csv`
- `logs/crypto_realtime_snapshots.json`

## Default Gates

```text
max snapshot age: 10 seconds
max spread: 10 bps
max entry/current price deviation: 50 bps
max exchange time drift: 2000 ms
diagnostics timeframe: M5
max diagnostics age: 900 seconds
```

## Diagnostic Options

Block instead of using REST fallback:

```bash
python -m research.crypto_realtime_guard --disable-rest-fallback
```

Skip the final order-book refresh:

```bash
python -m research.crypto_realtime_guard --disable-order-book-refresh
```

Run even when Phase 11 is not ready:

```bash
python -m research.crypto_realtime_guard --allow-phase11-not-ready
```

Do not block on missing recent Phase 4 market diagnostics:

```bash
python -m research.crypto_realtime_guard --allow-missing-diagnostics
```

## Safety Rules

- report keeps `order_submission_allowed=false`
- report keeps `live_execution_allowed=false`
- report can only set `paper_testnet_readiness_allowed=true`
- stale websocket snapshots block unless REST fallback refreshes them
- missing websocket snapshots block when REST fallback is disabled
- stale cache diagnostics block readiness
- wide spread blocks readiness
- exchange time drift blocks readiness
- entry/current price deviation blocks readiness

## Position In The Chain

```text
Phase 4 signal
-> Phase 5 outcome tracking
-> Phase 6/7 paper monitor
-> Phase 8 dry-run intent
-> Phase 9 sandbox execution model
-> Phase 10 request preview
-> Phase 11 market safety report
-> Phase 12 realtime final guard
-> future Phase 13 testnet submission only
```

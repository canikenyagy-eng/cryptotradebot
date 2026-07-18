# Crypto Phase 4 Forward Validation

Phase 4 runs the calibrated BTCUSDT/ETHUSDT/LTCUSDT profile against live CCXT candles and records what the signal engine sees. It is signal-only: Telegram alerts and local journals are allowed, exchange execution is not implemented.

## Scope

- Poll exchange-native OHLCV through `DATA_SOURCE=ccxt`
- Start with `BTCUSDT`, `ETHUSDT`, and `LTCUSDT`
- Use strict pair profiles: BTC score `78`; ETH score `90` with no MARKET fallback; LTC score `90` with no MARKET fallback; range-only
- Write forward candidate and Telegram delivery events to JSONL
- Write telemetry, heartbeat, and market-data diagnostics
- Keep exchange API keys out of the workflow

## Entrypoint

The dedicated runner applies Phase 4 defaults after loading `.env`:

```bash
python -m research.crypto_forward_validation --print-config --no-telegram
```

By default it runs one scan cycle. For continuous forward collection with Telegram alerts:

```bash
python -m research.crypto_forward_validation --loop --print-config
```

For a bounded validation window:

```bash
python -m research.crypto_forward_validation --max-cycles 12 --print-config
```

Use `--force-phase4-defaults` only when you intentionally want the Phase 4 defaults to override values already present in `.env`.

## Config

Use `docs/phase4_crypto_forward_validation.env.example` as the starting profile.

Important paths:

- Forward journal: `logs/crypto_forward_journal.jsonl`
- Live telemetry: `logs/crypto_forward_telemetry.jsonl`
- Heartbeat: `logs/crypto_forward_heartbeat.json`
- Market-data diagnostics: `logs/crypto_forward_market_data.jsonl`
- Daily report: `reports/crypto_forward_daily_report.json`

## Health Check

Run a heartbeat check without Telegram alerts:

```bash
python -m research.live_health_check \
  --heartbeat logs/crypto_forward_heartbeat.json \
  --max-age-minutes 15 \
  --output reports/crypto_forward_health.json \
  --no-alert
```

Send Telegram health alerts only after the live loop is stable:

```bash
ENABLE_HEALTH_ALERTS=1 python -m research.live_health_check \
  --heartbeat logs/crypto_forward_heartbeat.json \
  --max-age-minutes 15 \
  --output reports/crypto_forward_health.json \
  --alert
```

## Daily Summary

After enough forward candidates exist, update theoretical outcomes and build the daily report:

```bash
python -m research.daily_live_forward_report \
  --data-source ccxt \
  --output reports/crypto_forward_daily_report.json \
  --sent-only
```

Add `--telegram` when you want the compact summary sent to Telegram.

## Phase 4 Exit Criteria

Collect forward data before considering execution work:

- At least 2 weeks of stable polling
- No repeated CCXT health or freshness failures
- Forward journal and heartbeat files update every scan cycle
- Enough closed theoretical outcomes to compare BTC vs ETH vs LTC and range vs blocked regimes
- No live exchange orders or exchange API keys introduced

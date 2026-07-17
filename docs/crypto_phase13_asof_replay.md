# Crypto Phase 13 Honest As-Of Replay Validation

Phase 13 tests the bot as if it were running live on historical CCXT candles. The scanner only receives candles that existed at each simulated M5 replay step.

It does not create order intents, submit testnet orders, or submit live orders.

## Main Command

Print the effective config without fetching data:

```bash
python -m research.crypto_asof_replay --config-only
```

Run a bounded replay:

```bash
python -m research.crypto_asof_replay --max-steps 96
```

Choose an explicit window:

```bash
python -m research.crypto_asof_replay \
  --start 2026-07-01T00:00:00Z \
  --end 2026-07-07T00:00:00Z \
  --max-steps 500
```

Runner script:

```bash
scripts/run_crypto_asof_replay.sh --max-steps 96
```

## What It Uses

- real Phase 4 crypto defaults unless existing env overrides them
- BTCUSDT and ETHUSDT by default
- H1, M15, and M5 signal frames
- the real `SignalEngine`
- the real Phase 3 pair profiles and score gates
- forward-style journal rows
- forward outcome evaluation after signal time only

## No-Future Rule

At each replay step:

```text
as_of = current M5 timestamp
fetch_ohlcv(symbol, timeframe) returns only rows where candle_time <= as_of
future rows are counted as blocked
the replay report fails the no-future guard if any returned candle is after as_of
```

This is different from a normal backtest that scans a full dataframe and later pretends the signal happened earlier.

## Outputs

- `reports/crypto_phase13_asof_replay_report.json`
- `logs/crypto_phase13_asof_replay_decisions.jsonl`
- `logs/crypto_phase13_asof_replay_journal.jsonl`
- `logs/crypto_phase13_asof_replay_outcomes.jsonl`
- `reports/crypto_phase13_asof_replay_outcome_summary.json`

## Report Metrics

The report includes:

- replay steps
- accepted/rejected decisions
- signal count
- future rows blocked by the as-of wrapper
- future leak count
- AvgR
- profit factor
- cumulative R
- max drawdown in R
- ROI estimate from configured risk per trade

## Replay Adjustment

The live wall-clock candle freshness gate is disabled inside Phase 13 because historical candles would otherwise be compared to the current computer time. The as-of wrapper replaces that check by enforcing simulated-time freshness and no-future candle access.

## Exit Criteria

Before using replay results for calibration:

- `no_future_guard.passed=true`
- `future_leaks=0`
- enough replay steps were sampled
- closed outcomes are available
- BTCUSDT and ETHUSDT both have coverage
- metrics are compared against Phase 4 live forward results

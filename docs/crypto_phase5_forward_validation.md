# Crypto Phase 5 Forward Outcome Validation

Phase 5 measures whether Phase 4 signals are working before any exchange execution work. It labels forward candidates with theoretical outcomes, aggregates performance, checks feed health, and writes a local validation dashboard.

## Scope

- Read Phase 4 journal rows from `logs/crypto_forward_journal.jsonl`
- Refresh theoretical outcomes with CCXT candles
- Aggregate performance by pair, regime, session, score bucket, and pre-trade shadow state
- Produce a JSON validation report and an HTML dashboard
- Keep the workflow signal-only; no exchange API keys or live orders

## Main Command

```bash
python -m research.crypto_forward_validation_report
```

Outputs:

- `logs/crypto_forward_outcomes.jsonl`
- `reports/crypto_forward_outcomes_summary.json`
- `reports/crypto_forward_performance_report.json`
- `reports/crypto_forward_validation_report.json`
- `reports/crypto_forward_validation_dashboard.html`

Use `docs/phase5_crypto_validation.env.example` as the environment profile if you want explicit Phase 5 paths.

## Common Modes

Build the dashboard without updating outcomes:

```bash
python -m research.crypto_forward_validation_report --skip-outcome-update
```

Use only cached OHLCV:

```bash
python -m research.crypto_forward_validation_report --cache-only
```

Refresh CCXT cache before labeling:

```bash
python -m research.crypto_forward_validation_report --refresh-cache
```

Include unsent dry-run candidates:

```bash
python -m research.crypto_forward_validation_report --include-unsent
```

Adjust validation thresholds:

```bash
python -m research.crypto_forward_validation_report \
  --min-closed-trades 50 \
  --min-avg-r 0.10 \
  --min-profit-factor 1.20 \
  --max-drawdown-r 3.0
```

## Recommendation Actions

`COLLECT_SIGNALS` means the journal has no candidates yet.

`COLLECT_MORE_FORWARD_DATA` means the profile has signals, but not enough closed outcomes to decide.

`FIX_FEED_HEALTH` means heartbeat or CCXT trigger-candle diagnostics need attention before judging signal quality.

`PAUSE_OR_TIGHTEN_PROFILE` means forward expectancy is negative or profit factor is below `1.0`.

`REVIEW_DRAWDOWN` means expectancy may be positive, but drawdown breached the configured R limit.

`KEEP_PROFILE_SIGNAL_ONLY` means the current signal-only profile passes the configured forward thresholds. This is not approval for exchange execution; it is approval to keep collecting or move to a separate paper-trading design phase.

## Decision Rules

Default thresholds:

- minimum closed-with-R outcomes: `30`
- minimum average R: `0.10`
- minimum profit factor: `1.20`
- maximum drawdown: `3.0R`

Do not treat a group with `sample_ok=false` as reliable. Use the watchlist to identify pairs, regimes, sessions, or score buckets that are negative after enough sample.

## Suggested Cadence

Run Phase 4 continuously. Run this Phase 5 report daily after enough candles have passed for older signals to close:

```bash
python -m research.crypto_forward_validation_report --sent-only
```

Before Phase 6, collect at least two weeks of stable Phase 4/5 evidence and review BTCUSDT vs ETHUSDT separately.

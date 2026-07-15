# Crypto Phase 3 Backtest Calibration

Phase 3 uses exchange-native CCXT candles to validate and recalibrate the SMC signal engine before any execution code exists.

## Baseline Runs

Commands used:

```bash
TELEGRAM_BOT_TOKEN=dummy TELEGRAM_CHAT_ID=dummy \
MARKET_TYPE=crypto_spot DATA_SOURCE=ccxt CCXT_EXCHANGE_ID=binance CCXT_DEFAULT_TYPE=spot \
MARKET_DATA_CACHE_MODE=refresh EXPORT_REPORTS=1 \
ENABLE_PAIR_PROFILES=1 ALLOW_LIVE_PAIR_PROFILES=1 \
PAIR_PROFILES_JSON='{"BTCUSDT":{"min_score":78,"evaluation_step":20},"ETHUSDT":{"min_score":78,"evaluation_step":20}}' \
.venv/bin/python backtest_runner.py --pairs BTCUSDT,ETHUSDT --history-limit 1200 --evaluation-step 20 --warmup-bars 120 \
  --output-dir reports/phase3_baseline_1200 --analyze-scores --skip-realistic-comparison
```

```bash
TELEGRAM_BOT_TOKEN=dummy TELEGRAM_CHAT_ID=dummy \
MARKET_TYPE=crypto_spot DATA_SOURCE=ccxt CCXT_EXCHANGE_ID=binance CCXT_DEFAULT_TYPE=spot \
MARKET_DATA_CACHE_MODE=read_through EXPORT_REPORTS=1 MIN_SIGNAL_SCORE=72 \
ENABLE_PAIR_PROFILES=1 ALLOW_LIVE_PAIR_PROFILES=1 \
PAIR_PROFILES_JSON='{"BTCUSDT":{"min_score":72,"evaluation_step":20},"ETHUSDT":{"min_score":72,"evaluation_step":20}}' \
.venv/bin/python backtest_runner.py --pairs BTCUSDT,ETHUSDT --history-limit 1200 --evaluation-step 20 --warmup-bars 120 \
  --output-dir reports/phase3_score72_1200 --analyze-scores --skip-realistic-comparison
```

```bash
TELEGRAM_BOT_TOKEN=dummy TELEGRAM_CHAT_ID=dummy \
MARKET_TYPE=crypto_spot DATA_SOURCE=ccxt CCXT_EXCHANGE_ID=binance CCXT_DEFAULT_TYPE=spot \
ENABLE_REALISTIC_EXECUTION=1 SPREAD_BY_PAIR='BTCUSDT:5,ETHUSDT:5' SLIPPAGE_MODE=volatility MAX_SLIPPAGE_PIPS=10 \
LIMIT_TOUCH_TOLERANCE_PIPS=5 APPLY_SPREAD_TO_LIMIT=1 \
ENABLE_PAIR_PROFILES=1 ALLOW_LIVE_PAIR_PROFILES=1 \
PAIR_PROFILES_JSON='{"BTCUSDT":{"min_score":78,"evaluation_step":20},"ETHUSDT":{"min_score":80,"evaluation_step":20}}' \
.venv/bin/python backtest_runner.py --pairs BTCUSDT,ETHUSDT --history-limit 1200 --evaluation-step 20 --warmup-bars 120 \
  --output-dir reports/phase3_strict_friction_1200 --skip-realistic-comparison
```

## Findings

Summary helper:

```bash
.venv/bin/python research/crypto_phase3_calibration_report.py \
  reports/phase3_baseline_1200 \
  reports/phase3_score72_1200 \
  reports/phase3_strict_friction_1200
```

| run | trades | win rate | avg R | max DD R | ROI % | realistic |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| phase3_baseline_1200 | 3 | 1.0000 | 0.2206 | 0.0000 | 3.3095 | false |
| phase3_score72_1200 | 7 | 0.5714 | -0.2667 | 2.4658 | -9.3330 | false |
| phase3_strict_friction_1200 | 3 | 1.0000 | 0.2144 | 0.0000 | 3.2160 | true |

- The strict 78/80 profile produced only 3 trades over this sample, all in `RANGE`, all timeout exits.
- The loose 72/72 profile increased sample size to 7 trades but turned negative: `avg_r=-0.27`, max drawdown `2.47R`.
- The loose profile accepted expansion/continuation trades that hit stops. Expansion regime expectancy was negative in this sample.
- Simple friction (`BTCUSDT:5`, `ETHUSDT:5`, volatility slippage up to `10`) did not break the strict profile, but reduced already-small timeout gains.
- Current exits are not decisive: strict winners were timeouts, not TP hits. Exit calibration remains required before execution.

## First Forward-Validation Profile

Use `docs/phase3_crypto_signal_only.env.example` as the starting signal-only profile:

- `BTCUSDT` min score: `78`
- `ETHUSDT` min score: `80`
- 24/7 crypto behavior: session gate disabled
- Block `EXPANSION`, `CONTRACTION`, and `TREND` for the first forward window; collect `RANGE` evidence first
- Keep realistic execution assumptions in backtests
- Continue Telegram-only forward validation, no exchange execution

This is not edge certification. It is a conservative first filter based on a small initial CCXT sample.

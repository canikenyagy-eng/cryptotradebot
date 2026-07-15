# Crypto Trade Bot

Crypto-first SMC signal bot, forked from the forex `smctradebot` codebase.

Current status: Phase 7 paper-trading monitor and execution-readiness guardrails. The repo has the existing SMC signal engine, backtesting, telemetry, journaling, and Telegram alert stack, with crypto symbol plumbing, exchange-native OHLCV candles via CCXT, Phase 3 BTC/ETH calibration, a safe Phase 4 forward-validation runner, Phase 5 outcome/reporting dashboards, Phase 6 virtual account reporting, and Phase 7 readiness monitoring.

## What Works Now

- Crypto-first defaults in `.env.example`
- `MARKET_TYPE=crypto_spot`
- Symbol normalization for `BTCUSDT`, `BTC/USDT`, and exchange-style metadata
- `SymbolSpec` support for tick size, minimum size, fee rates, and exchange symbol
- Forex 6-character assumptions removed from the main signal/data/backtest path
- Exchange-native OHLCV candles through `DATA_SOURCE=ccxt`
- Cache and health-check support through the existing market data client
- Phase 3 BTC/ETH backtest calibration docs and signal-only profile
- Phase 4 CCXT forward-validation runner with Telegram alerts, forward journal, telemetry, heartbeat, and diagnostics
- Phase 5 forward outcome tracking, performance aggregation, feed-health decisioning, and local dashboard output
- Phase 6 paper-trading replay from forward signals and outcomes with ledger, equity metrics, and local dashboard output
- Phase 7 paper-trading monitor that refreshes outcomes, rebuilds paper reports, and blocks live execution design until readiness guardrails pass

## Not Ready Yet

- Fees, funding, and order-book slippage calibration are later phases
- Real exchange execution is intentionally not implemented yet

## Starter Config

Copy `.env.example` to `.env`, fill Telegram secrets, and keep execution disabled:

```env
MARKET_TYPE=crypto_spot
PAIRS=BTCUSDT,ETHUSDT
DATA_SOURCE=ccxt
CCXT_EXCHANGE_ID=binance
CCXT_DEFAULT_TYPE=spot
ENABLE_LIVE_MODE=0
```

Smoke-check exchange candles:

```bash
python research/ccxt_provider_check.py --pairs BTCUSDT,ETHUSDT --timeframes M5,M15 --limit 10
```

Run a backtest once dependencies are installed:

```bash
python backtest_runner.py --pairs BTCUSDT,ETHUSDT --history-limit 3000
```

Run one Phase 4 forward-validation scan without Telegram:

```bash
python -m research.crypto_forward_validation --no-telegram --print-config
```

Run continuous Phase 4 signal-only forward validation with Telegram:

```bash
python -m research.crypto_forward_validation --loop --print-config
```

Build the Phase 5 validation report and dashboard:

```bash
python -m research.crypto_forward_validation_report
```

Replay Phase 5 outcomes into a Phase 6 paper-trading account:

```bash
python -m research.crypto_paper_trading_report --sent-only
```

Run the Phase 7 paper monitor and execution-readiness guardrails:

```bash
python -m research.crypto_paper_monitor --sent-only
```

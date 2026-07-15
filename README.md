# Crypto Trade Bot

Crypto-first SMC signal bot, forked from the forex `smctradebot` codebase.

Current status: Phase 2 market data. The repo has the existing SMC signal engine, backtesting, telemetry, journaling, and Telegram alert stack, with crypto symbol plumbing and exchange-native OHLCV candles via CCXT.

## What Works In Phase 1

- Crypto-first defaults in `.env.example`
- `MARKET_TYPE=crypto_spot`
- Symbol normalization for `BTCUSDT`, `BTC/USDT`, and exchange-style metadata
- `SymbolSpec` support for tick size, minimum size, fee rates, and exchange symbol
- Forex 6-character assumptions removed from the main signal/data/backtest path
- Exchange-native OHLCV candles through `DATA_SOURCE=ccxt`
- Cache and health-check support through the existing market data client
- Telegram signal-only flow remains available

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

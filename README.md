# Crypto Trade Bot

Crypto-first SMC signal bot, forked from the forex `smctradebot` codebase.

Current status: Phase 1 foundation. The repo has the existing SMC signal engine, backtesting, telemetry, journaling, and Telegram alert stack, with new market/symbol plumbing for crypto symbols such as `BTCUSDT` and `ETHUSDT`.

## What Works In Phase 1

- Crypto-first defaults in `.env.example`
- `MARKET_TYPE=crypto_spot`
- Symbol normalization for `BTCUSDT`, `BTC/USDT`, and exchange-style metadata
- `SymbolSpec` support for tick size, minimum size, fee rates, and exchange symbol
- Forex 6-character assumptions removed from the main signal/data/backtest path
- Yahoo starter candles for common crypto USD pairs
- Telegram signal-only flow remains available

## Not Ready Yet

- Exchange-native market data is Phase 2
- Fees, funding, and order-book slippage calibration are later phases
- Real exchange execution is intentionally not implemented yet

## Starter Config

Copy `.env.example` to `.env`, fill Telegram secrets, and keep execution disabled:

```env
MARKET_TYPE=crypto_spot
PAIRS=BTCUSDT,ETHUSDT
DATA_SOURCE=yahoo
ENABLE_LIVE_MODE=0
```

Run a backtest once dependencies are installed:

```bash
python backtest_runner.py --pairs BTCUSDT,ETHUSDT --history-limit 3000
```


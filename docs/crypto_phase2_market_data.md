# Crypto Phase 2 Market Data

Phase 2 adds exchange-native crypto OHLCV candles through CCXT. This is still data-only: no exchange keys, balances, orders, or live execution.

## Provider

Use:

```env
DATA_SOURCE=ccxt
CCXT_EXCHANGE_ID=binance
CCXT_DEFAULT_TYPE=spot
MARKET_TYPE=crypto_spot
PAIRS=BTCUSDT,ETHUSDT
```

`SymbolSpec.exchange_symbol` controls the CCXT unified symbol, for example `BTC/USDT`.

## Checks

Install dependencies, then run:

```bash
python research/ccxt_provider_check.py --pairs BTCUSDT,ETHUSDT --timeframes M5,M15,H1 --limit 10
```

The provider is routed through `MarketDataClient`, so the existing cache, diagnostics, and stale-cache fallback behavior also apply to `DATA_SOURCE=ccxt`.

## Next

Phase 3 should use this provider to run BTC/ETH/SOL backtests and recalibrate SMC thresholds before any execution code exists.

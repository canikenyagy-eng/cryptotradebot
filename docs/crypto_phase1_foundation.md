# Crypto Phase 1 Foundation

Phase 1 turns the copied forex bot into a crypto-ready codebase without adding exchange execution.

## Completed In This Phase

- Added `core.symbols` with `MarketType`, `SymbolSpec`, shared symbol normalization, base/quote parsing, Yahoo ticker conversion, and market-aware price units.
- Added `MARKET_TYPE` and `SYMBOL_SPECS_JSON` settings.
- Changed defaults from `EURUSD/EURJPY/CADJPY` to `BTCUSDT/ETHUSDT`.
- Removed 6-character forex symbol checks from the main signal, market data, pair profile, portfolio exposure, backtest, and forward-journal paths.
- Made crypto session scoring neutral/24-7 instead of forex-session based.
- Kept old forex/iTick/MT5 docs as legacy references until replacement crypto docs are written.

## Phase 2 Target

Add an exchange-native crypto market data provider, likely through `ccxt`, with OHLCV fetching, health checks, cache support, and exchange symbol mapping from `SymbolSpec`.


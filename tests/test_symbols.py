from __future__ import annotations

import unittest

from core.symbols import (
    MarketType,
    SymbolSpec,
    default_price_unit,
    normalize_symbol,
    split_symbol,
    yahoo_ticker_for_symbol,
)


class SymbolTests(unittest.TestCase):
    def test_normalizes_common_crypto_symbol_forms(self) -> None:
        self.assertEqual(normalize_symbol("BTC/USDT"), "BTCUSDT")
        self.assertEqual(normalize_symbol("eth-usdt"), "ETHUSDT")
        self.assertEqual(split_symbol("BTCUSDT", market_type="crypto_spot"), ("BTC", "USDT"))
        self.assertEqual(split_symbol("ETH/BTC", market_type="crypto_spot"), ("ETH", "BTC"))

    def test_keeps_forex_compatibility(self) -> None:
        self.assertEqual(split_symbol("EURUSD", market_type="forex"), ("EUR", "USD"))
        self.assertEqual(yahoo_ticker_for_symbol("EURUSD", market_type="forex"), "EURUSD=X")

    def test_crypto_yahoo_ticker_uses_usd_proxy_for_stable_quotes(self) -> None:
        self.assertEqual(yahoo_ticker_for_symbol("BTCUSDT", market_type="crypto_spot"), "BTC-USD")

    def test_symbol_spec_preserves_exchange_metadata(self) -> None:
        spec = SymbolSpec.from_symbol(
            "BTCUSDT",
            market_type="crypto_spot",
            payload={"exchange_symbol": "BTC/USDT", "tick_size": 0.01, "taker_fee_rate": 0.001},
        )

        self.assertEqual(spec.symbol, "BTCUSDT")
        self.assertEqual(spec.base, "BTC")
        self.assertEqual(spec.quote, "USDT")
        self.assertEqual(spec.market_type, MarketType.CRYPTO_SPOT)
        self.assertEqual(spec.price_unit, 0.01)
        self.assertEqual(spec.exchange_or_symbol, "BTC/USDT")
        self.assertEqual(spec.taker_fee_rate, 0.001)

    def test_default_price_units_are_market_aware(self) -> None:
        self.assertEqual(default_price_unit("USDJPY", market_type="forex"), 0.01)
        self.assertEqual(default_price_unit("EURUSD", market_type="forex"), 0.0001)
        self.assertEqual(default_price_unit("BTCUSDT", market_type="crypto_spot"), 0.01)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from data.ccxt_provider import CcxtConfig, CcxtMarketDataProvider


class FakeExchange:
    has = {"fetchOHLCV": True}

    def __init__(self) -> None:
        self.loaded = 0
        self.calls: list[dict[str, object]] = []

    def load_markets(self) -> None:
        self.loaded += 1

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: object, limit: int, params: dict[str, object]):
        self.calls.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "since": since,
                "limit": limit,
                "params": params,
            }
        )
        return [
            [1_700_000_000_000, 100.0, 110.0, 90.0, 105.0, 12.5],
            [1_700_000_300_000, 105.0, 111.0, 104.0, 108.0, 8.0],
        ]


class FakePagedExchange(FakeExchange):
    def __init__(self) -> None:
        super().__init__()
        self.rows = [
            [1_700_000_000_000, 100.0, 110.0, 90.0, 101.0, 1.0],
            [1_700_000_300_000, 101.0, 111.0, 91.0, 102.0, 2.0],
            [1_700_000_600_000, 102.0, 112.0, 92.0, 103.0, 3.0],
        ]

    def milliseconds(self) -> int:
        return 1_700_000_900_000

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: object, limit: int, params: dict[str, object]):
        self.calls.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "since": since,
                "limit": limit,
                "params": params,
            }
        )
        since_ms = int(since or 0)
        page = [row for row in self.rows if row[0] >= since_ms]
        return page[:limit]


class CcxtProviderTests(unittest.TestCase):
    def test_fetch_ohlcv_formats_symbol_timeframe_and_frame(self) -> None:
        config = CcxtConfig.from_dict(
            {
                "exchange_id": "binance",
                "market_type": "crypto_spot",
                "symbol_specs": {"BTCUSDT": {"exchange_symbol": "BTC/USDT"}},
                "ohlcv_params": {"price": "mark"},
            }
        )
        provider = CcxtMarketDataProvider(config=config, history_limit=500)
        fake = FakeExchange()
        provider._exchange = fake
        provider._initialized = True

        frame = provider.fetch_ohlcv("BTCUSDT", "M5", limit=2)

        self.assertEqual(fake.loaded, 1)
        self.assertEqual(fake.calls[0]["symbol"], "BTC/USDT")
        self.assertEqual(fake.calls[0]["timeframe"], "5m")
        self.assertEqual(fake.calls[0]["limit"], 2)
        self.assertEqual(fake.calls[0]["params"], {"price": "mark"})
        self.assertEqual(list(frame.columns), ["open", "high", "low", "close", "volume"])
        self.assertEqual(len(frame), 2)
        self.assertEqual(float(frame["close"].iloc[-1]), 108.0)
        self.assertIsNotNone(frame.index.tz)

    def test_health_check_uses_configured_symbol(self) -> None:
        config = CcxtConfig.from_dict(
            {
                "exchange_id": "binance",
                "market_type": "crypto_spot",
                "health_check_symbol": "ETHUSDT",
                "symbol_specs": {"ETHUSDT": {"exchange_symbol": "ETH/USDT"}},
            }
        )
        provider = CcxtMarketDataProvider(config=config, history_limit=500)
        fake = FakeExchange()
        provider._exchange = fake
        provider._initialized = True

        self.assertTrue(provider.health_check())
        self.assertEqual(fake.calls[0]["symbol"], "ETH/USDT")
        self.assertEqual(fake.calls[0]["limit"], 1)

    def test_fetch_ohlcv_paginates_when_requested_rows_exceed_request_limit(self) -> None:
        config = CcxtConfig.from_dict(
            {
                "exchange_id": "binance",
                "market_type": "crypto_spot",
                "symbol_specs": {"BTCUSDT": {"exchange_symbol": "BTC/USDT"}},
                "ohlcv_request_limit": 1,
            }
        )
        provider = CcxtMarketDataProvider(config=config, history_limit=500)
        fake = FakePagedExchange()
        provider._exchange = fake
        provider._initialized = True
        provider._timeframe_milliseconds = lambda timeframe: 300_000

        frame = provider.fetch_ohlcv("BTCUSDT", "M5", limit=3)

        self.assertGreaterEqual(len(fake.calls), 3)
        self.assertEqual(len(frame), 3)
        self.assertEqual(float(frame["close"].iloc[-1]), 103.0)


if __name__ == "__main__":
    unittest.main()
